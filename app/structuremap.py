"""
StructureMap:1.0.0 generation from RDDMS Grid2dRepresentation objects.

Discovers depth-domain Grid2dRepresentations in a Reservoir DDMS dataspace
and converts them to OSDU M27 StructureMap:1.0.0 records.

Reuses:
  - resqml_viz.fetch_grid2d_surface()  - fetch object + z-values + CRS
  - resqml_viz._parse_lattice()        - RESQML 2.0.1 lattice geometry parsing
  - osdu.list_resources()        - enumerate objects by type
  - Conversion logic adapted from demo/seisint/gen_structuremap_from_resqml.py

Architecture note:
  The RDDMS stores RESQML 2.0.1 objects (Grid2dPatch, Offset[], Spacing etc.)
  while the demo script operates on RESQML 2.2 JSON ($type, Dimension[] etc.).
  This module handles the 2.0.1 format directly via osdu.py helpers.
"""
from __future__ import annotations

import logging
import math
import urllib.parse
import uuid
from typing import Any, Dict, List, Optional, Tuple

from . import osdu
from . import resqml_viz

log = logging.getLogger("rddms-admin.structuremap")

# ── Deterministic UUID namespace (same as demo/seisint/_shared.py) ────
NS_SMAP = uuid.UUID("d1e2f3a4-b5c6-7890-abcd-ef0123456789")


def stable_uuid(name: str) -> str:
    """UUID5 from a fixed namespace - same name always gives same UUID."""
    return str(uuid.uuid5(NS_SMAP, name))


# ── ID builders ───────────────────────────────────────────────────────
def wpc_id(prefix: str, entity: str, uid: str) -> str:
    return f"{prefix}:work-product-component--{entity}:{uid}:1"


# ── Grid geometry helpers ─────────────────────────────────────────────
def offsets_to_bearing(dx: float, dy: float) -> Tuple[float, float]:
    """(dEasting, dNorthing) → (bearing°CW from north, width)."""
    width = math.sqrt(dx * dx + dy * dy)
    bearing = math.degrees(math.atan2(dx, dy)) % 360
    return (round(bearing, 6), round(width, 6))


def bearing_to_offsets(bearing_deg: float, bin_width: float) -> Tuple[float, float]:
    """Compass bearing + width → (dEasting, dNorthing)."""
    rad = math.radians(bearing_deg)
    dx = bin_width * math.sin(rad)
    dy = bin_width * math.cos(rad)
    return (round(dx, 6), round(dy, 6))


def abcd_corners(
    origin_e: float, origin_n: float,
    bearing_i: float, width_i: float, count_i: int,
    bearing_j: float, width_j: float, count_j: int,
) -> Dict[str, Dict[str, float]]:
    """Compute ABCD corner coordinates from grid parameters.

    A = origin (i=0, j=0), B = (i=max, j=0),
    C = (i=max, j=max),    D = (i=0, j=max).
    """
    di = bearing_to_offsets(bearing_i, width_i)
    dj = bearing_to_offsets(bearing_j, width_j)
    ni, nj = count_i - 1, count_j - 1

    a = (origin_e, origin_n)
    b = (origin_e + ni * di[0], origin_n + ni * di[1])
    c = (origin_e + ni * di[0] + nj * dj[0], origin_n + ni * di[1] + nj * dj[1])
    d = (origin_e + nj * dj[0], origin_n + nj * dj[1])

    def pt(xy: Tuple[float, float]) -> Dict[str, float]:
        return {"Easting": round(xy[0], 2), "Northing": round(xy[1], 2)}

    return {"A": pt(a), "B": pt(b), "C": pt(c), "D": pt(d)}


# ── Default ACL / Legal (delegate to osdu.py module-level constants) ──
def _acl_block() -> Dict[str, Any]:
    return {"owners": osdu.DEFAULT_OWNERS[:], "viewers": osdu.DEFAULT_VIEWERS[:]}


def _legal_block() -> Dict[str, Any]:
    return {
        "legaltags": [osdu.DEFAULT_LEGAL_TAG],
        "otherRelevantDataCountries": osdu.DEFAULT_COUNTRIES[:],
    }


# ── Domain classification ─────────────────────────────────────────────
def _is_depth_crs(crs: Optional[dict]) -> bool:
    """Determine whether a CRS represents depth domain (not time).

    Works with RESQML 2.0.1 objects as returned by the RDDMS REST API.
    """
    if not crs:
        return True  # default assumption when CRS is unavailable

    # 1. Check $type / contentType - fastest path
    ctype = (crs.get("$type") or crs.get("contentType") or "").lower()
    if "localtime" in ctype:
        return False
    if "localdepth" in ctype:
        return True

    # 2. Check ZIncreasingDownward (present in depth CRS, absent in time)
    if crs.get("ZIncreasingDownward") is not None:
        return True

    # 3. Check VerticalUom - 'ms' or 's' implies time
    vuom = (crs.get("VerticalUom") or "").lower()
    if vuom in ("ms", "s"):
        return False

    # 4. RESQML 2.2 VerticalAxis.IsTime (from demo format)
    vert_axis = crs.get("VerticalAxis") or {}
    if vert_axis.get("IsTime") is True:
        return False

    return True  # default: depth


# ── Single surface → StructureMap record ──────────────────────────────

def surface_to_structuremap(
    surface: Dict[str, Any],
    ds_path: str,
    *,
    prefix: str = "dev",
) -> Optional[Dict[str, Any]]:
    """Convert a fetched Grid2d surface to an OSDU StructureMap:1.0.0 record.

    Args:
        surface: Output of resqml_viz.fetch_grid2d_surface().
        ds_path: Dataspace path (e.g. 'maap/drogon').
        prefix: OSDU namespace prefix.

    Returns:
        StructureMap record dict, or None if the surface is not depth-domain.
    """
    grid = surface["grid"]
    crs = surface["crs"]
    geometry = surface["geometry"]
    dims = surface["dims"]  # [n_slow, n_fast]

    if not _is_depth_crs(crs):
        return None

    uuid_val = grid.get("Uuid") or grid.get("UUID") or grid.get("uuid") or ""
    title = (grid.get("Citation") or {}).get("Title", "Unnamed")
    n_slow, n_fast = dims

    # ── Extract grid parameters from parsed geometry ──
    origin_e, origin_n, _origin_z = geometry["origin"]
    u_dx, u_dy = geometry["u_vec"]   # slow-axis unit direction
    v_dx, v_dy = geometry["v_vec"]   # fast-axis unit direction
    u_space = geometry["u_space"]     # slow-axis spacing
    v_space = geometry["v_space"]     # fast-axis spacing

    # Convert offset vectors → bearings
    # RESQML 2.0.1 convention: offset[0]=slow axis (J), offset[1]=fast axis (I)
    bearing_j, _ = offsets_to_bearing(u_dx * u_space, u_dy * u_space)
    bearing_i, _ = offsets_to_bearing(v_dx * v_space, v_dy * v_space)
    width_j = u_space
    width_i = v_space
    count_j = n_slow
    count_i = n_fast

    # Determine handedness (TransformationMethod)
    # EPSG 9666 = right-handed (I = J + 90°), EPSG 1049 = left-handed
    expected_rh = (bearing_j + 90.0) % 360
    diff_rh = min(abs(bearing_i - expected_rh) % 360, 360 - abs(bearing_i - expected_rh) % 360)
    transform = 9666 if diff_rh < 5.0 else 1049

    # ABCD corners
    corners = abcd_corners(
        origin_e, origin_n,
        bearing_i, width_i, count_i,
        bearing_j, width_j, count_j,
    )

    # EML URI for DDMSDatasets
    ddms_uri = (
        f"eml:///dataspace('{ds_path}')/"
        f"resqml20.obj_Grid2dRepresentation('{uuid_val}')"
    )

    # Interpretation reference (if available)
    interp_ref = grid.get("RepresentedInterpretation") or {}
    interp_uuid = interp_ref.get("UUID") or interp_ref.get("Uuid") or ""
    interp_title = interp_ref.get("Title") or ""
    interp_id = ""
    if interp_uuid:
        osdu_uuid = stable_uuid(f"resqml-interp:{interp_uuid}")
        interp_id = wpc_id(prefix, "HorizonInterpretation", osdu_uuid)

    # CRS info for metadata
    crs_title = ""
    if crs:
        crs_title = (crs.get("Citation") or {}).get("Title", "")

    # Build the StructureMap data block
    smap_uuid = stable_uuid(f"resqml-smap:{uuid_val}")
    smap_data: Dict[str, Any] = {
        "Name": title,
        "Description": f"StructureMap from RDDMS Grid2dRepresentation {uuid_val}",
        "DomainTypeID": f"{prefix}:reference-data--DomainType:Depth:",
        "DDMSDatasets": [ddms_uri],
        "BinGridName": f"{title} grid",
        "OriginEasting": round(origin_e, 2),
        "OriginNorthing": round(origin_n, 2),
        "BinWidthOnIaxis": round(width_i, 4),
        "BinWidthOnJaxis": round(width_j, 4),
        "MapGridBearingOfBinGridJaxis": round(bearing_j, 4),
        "NodeCountOnIAxis": count_i,
        "NodeCountOnJAxis": count_j,
        "TransformationMethod": transform,
        "ABCDBinGridSpatialLocation": {
            "AsIngestedCoordinates": {
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "geometry": {
                        "type": "MultiPoint",
                        "coordinates": [
                            [corners["A"]["Easting"], corners["A"]["Northing"]],
                            [corners["B"]["Easting"], corners["B"]["Northing"]],
                            [corners["C"]["Easting"], corners["C"]["Northing"]],
                            [corners["D"]["Easting"], corners["D"]["Northing"]],
                        ],
                    },
                    "properties": {"name": "ABCD corners"},
                }],
            },
        },
    }

    if interp_id:
        smap_data["InterpretationID"] = interp_id

    # Add CRS title as metadata
    if crs_title:
        smap_data["CoordinateReferenceSystemID"] = crs_title

    return {
        "id": wpc_id(prefix, "StructureMap", smap_uuid),
        "kind": "osdu:wks:work-product-component--StructureMap:1.0.0",
        "acl": _acl_block(),
        "legal": _legal_block(),
        "data": smap_data,
    }


# ── Lightweight surface info (no z-values fetch) ─────────────────────

async def classify_grid2d(
    access_token: str,
    ds_path: str,
    uuid_val: str,
) -> Dict[str, Any]:
    """Fetch a Grid2dRepresentation and classify it (depth vs time, dims, title).

    Returns a summary dict (does NOT fetch z-values for speed).
    """
    enc = urllib.parse.quote(ds_path, safe="")
    typ = "resqml20.obj_Grid2dRepresentation"

    obj_raw = await osdu.get_resource(access_token, enc, typ, uuid_val)
    obj = osdu._normalize_obj(obj_raw, uuid_val)

    title = (obj.get("Citation") or {}).get("Title", uuid_val)
    patch = obj.get("Grid2dPatch") or {}
    n_fast = int(patch.get("FastestAxisCount", 0))
    n_slow = int(patch.get("SlowestAxisCount", 0))

    # Resolve CRS
    geom = patch.get("Geometry") or {}
    local_crs_ref = geom.get("LocalCrs") or {}
    crs = local_crs_ref.get("_data")  # RDDMS often inlines it
    crs_title = ""
    crs_type = ""

    if not crs:
        crs_uuid = local_crs_ref.get("UUID") or local_crs_ref.get("Uuid")
        if crs_uuid:
            ct = local_crs_ref.get("ContentType", "")
            crs_type = (
                "resqml20.obj_LocalTime3dCrs"
                if "LocalTime3dCrs" in ct
                else "resqml20.obj_LocalDepth3dCrs"
            )
            try:
                crs_raw = await osdu.get_resource(access_token, enc, crs_type, crs_uuid)
                crs = osdu._normalize_obj(crs_raw, crs_uuid)
            except Exception as e:
                log.warning("classify: CRS fetch failed for %s: %s", uuid_val, e)

    if crs:
        crs_title = (crs.get("Citation") or {}).get("Title", "")

    is_depth = _is_depth_crs(crs)

    # Interpretation info
    interp_ref = obj.get("RepresentedInterpretation") or {}
    interp_title = interp_ref.get("Title") or ""

    return {
        "uuid": uuid_val,
        "title": title,
        "domain": "depth" if is_depth else "time",
        "dims": [n_slow, n_fast],
        "n_nodes": n_slow * n_fast,
        "crs_title": crs_title,
        "crs_type": crs_type,
        "interpretation": interp_title,
        "uri": obj.get("uri") or osdu._eml_uri_from_parts(
            ds_path, typ, uuid_val
        ),
    }


# ── Discovery: list all Grid2d surfaces in a dataspace ────────────────

async def discover_surfaces(
    access_token: str,
    ds_path: str,
) -> List[Dict[str, Any]]:
    """List and classify all Grid2dRepresentations in a dataspace.

    Returns a list of surface info dicts (lightweight - no z-value fetch).
    """
    enc = urllib.parse.quote(ds_path, safe="")
    typ = "resqml20.obj_Grid2dRepresentation"

    try:
        resources = await osdu.list_resources(access_token, enc, typ) or []
    except Exception as e:
        log.warning("discover_surfaces: list_resources failed: %s", e)
        return []

    surfaces: List[Dict[str, Any]] = []
    for r in resources:
        uid = r.get("Uuid") or r.get("UUID") or r.get("uuid") or ""
        if not uid:
            uri = r.get("uri", "")
            if "(" in uri:
                uid = uri.split("(")[-1].rstrip(")")
        if not uid:
            continue

        try:
            info = await classify_grid2d(access_token, ds_path, uid)
            surfaces.append(info)
        except Exception as e:
            log.warning("discover_surfaces: classify %s failed: %s", uid, e)
            surfaces.append({
                "uuid": uid,
                "title": (r.get("Citation") or {}).get("Title", uid),
                "domain": "unknown",
                "dims": [0, 0],
                "n_nodes": 0,
                "error": str(e),
            })

    return surfaces


# ── Discovery: all RESQML representation types ────────────────────────

# Types discoverable via the RDDMS list_resources endpoint.
RESQML_TYPES = {
    "Grid2d":          "resqml20.obj_Grid2dRepresentation",
    "PolylineSet":     "resqml20.obj_PolylineSetRepresentation",
    "PointSet":        "resqml20.obj_PointSetRepresentation",
    "TriangulatedSet": "resqml20.obj_TriangulatedSetRepresentation",
}


async def discover_all_representations(
    access_token: str,
    ds_path: str,
    *,
    types: Optional[List[str]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Discover multiple RESQML representation types in a dataspace.

    Args:
        access_token: Bearer token.
        ds_path: Dataspace path.
        types: Short type names to discover (default: all known types).
               Valid values: Grid2d, PolylineSet, PointSet, TriangulatedSet.

    Returns dict mapping type name → list of lightweight info dicts:
        {
            "Grid2d": [ {uuid, title, type, interpretation, ...}, ... ],
            "PolylineSet": [ ... ],
            "PointSet": [ ... ],
        }
    """
    if types is None:
        types = list(RESQML_TYPES.keys())

    enc = urllib.parse.quote(ds_path, safe="")
    results: Dict[str, List[Dict[str, Any]]] = {}

    for short_name in types:
        resqml_type = RESQML_TYPES.get(short_name)
        if not resqml_type:
            log.warning("discover_all: unknown type '%s'", short_name)
            continue

        try:
            resources = await osdu.list_resources(access_token, enc, resqml_type) or []
        except Exception as e:
            log.warning("discover_all: list %s failed: %s", short_name, e)
            results[short_name] = []
            continue

        items: List[Dict[str, Any]] = []
        for r in resources:
            uid = r.get("Uuid") or r.get("UUID") or r.get("uuid") or ""
            if not uid:
                uri = r.get("uri", "")
                if "(" in uri:
                    uid = uri.split("(")[-1].rstrip(")")
            if not uid:
                continue

            title = (r.get("Citation") or {}).get("Title", uid)

            # Quick classification from list metadata (no extra fetch)
            interp_ref = r.get("RepresentedInterpretation") or {}
            interp_title = interp_ref.get("Title") or ""
            interp_ct = interp_ref.get("ContentType") or ""
            is_fault = "Fault" in interp_ct

            items.append({
                "uuid": uid,
                "title": title,
                "type": short_name,
                "resqml_type": resqml_type,
                "interpretation": interp_title,
                "is_fault": is_fault,
                "uri": r.get("uri") or f"eml:///dataspace('{ds_path}')/{resqml_type}('{uid}')",
            })

        results[short_name] = items

    return results


# ── Full pipeline: discover + convert depth surfaces to StructureMaps ─

async def generate_structuremaps(
    access_token: str,
    ds_path: str,
    *,
    prefix: str = "dev",
    uuids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Discover Grid2dRepresentations and generate StructureMap:1.0.0 records.

    Args:
        access_token: Bearer token.
        ds_path: Dataspace path (e.g. 'maap/drogon').
        prefix: OSDU namespace prefix.
        uuids: If provided, only convert these Grid2d UUIDs (skip discovery).

    Returns dict:
        {
            "dataspace": "maap/drogon",
            "grid2d_count": 12,
            "depth_count": 8,
            "time_count": 3,
            "skipped_count": 1,
            "surfaces": [ {uuid, title, domain, dims, ...}, ... ],
            "structuremaps": [ {id, kind, acl, legal, data}, ... ],
        }
    """
    # 1. Discover surfaces
    if uuids:
        # Convert specific UUIDs only
        surfaces = []
        for uid in uuids:
            try:
                info = await classify_grid2d(access_token, ds_path, uid)
                surfaces.append(info)
            except Exception as e:
                log.warning("generate_structuremaps: classify %s: %s", uid, e)
    else:
        surfaces = await discover_surfaces(access_token, ds_path)

    # 2. Classify counts
    depth_surfaces = [s for s in surfaces if s.get("domain") == "depth"]
    time_surfaces = [s for s in surfaces if s.get("domain") == "time"]
    unknown = [s for s in surfaces if s.get("domain") not in ("depth", "time")]

    # 3. Fetch full surface data + convert depth surfaces to StructureMaps
    structuremaps: List[Dict[str, Any]] = []

    for info in depth_surfaces:
        uid = info["uuid"]
        try:
            full_surface = await resqml_viz.fetch_grid2d_surface(
                access_token, ds_path, uid
            )
            smap = surface_to_structuremap(full_surface, ds_path, prefix=prefix)
            if smap:
                # Attach source metadata for UI display
                smap["_source"] = {
                    "uuid": uid,
                    "title": info.get("title", ""),
                    "dims": info.get("dims", [0, 0]),
                    "crs": info.get("crs_title", ""),
                    "interpretation": info.get("interpretation", ""),
                }
                structuremaps.append(smap)
        except Exception as e:
            log.warning("generate_structuremaps: convert %s failed: %s", uid, e)

    return {
        "dataspace": ds_path,
        "grid2d_count": len(surfaces),
        "depth_count": len(depth_surfaces),
        "time_count": len(time_surfaces),
        "skipped_count": len(unknown),
        "surfaces": surfaces,
        "structuremaps": structuremaps,
    }


# ── Manifest wrapping ────────────────────────────────────────────────

def wrap_as_manifest(
    structuremaps: List[Dict[str, Any]],
    *,
    dataspace: str = "",
) -> Dict[str, Any]:
    """Wrap StructureMap records into an OSDU manifest envelope.

    This produces a client-side manifest (not from the RDDMS manifest/build
    endpoint) that contains StructureMap:1.0.0 records - ready for ingestion
    via the OSDU Storage or Workflow service.
    """
    return {
        "kind": "osdu:wks:Manifest:1.0.0",
        "Data": {
            "WorkProductComponents": structuremaps,
        },
        "meta": {
            "source": "ORES structuremap module",
            "dataspace": dataspace,
            "schema": "StructureMap:1.0.0 (OSDU M27)",
        },
    }
