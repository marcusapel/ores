from __future__ import annotations

import io
import math
import os
import json
import logging
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse  
import httpx
import numpy as np

log = logging.getLogger("rddms-admin.osdu")

# ----------------------------------------------------------------------
# Environment & defaults
# ----------------------------------------------------------------------

# Base DNS name of your ADME/OSDU instance (no scheme).
OSDU_BASE_URL: str = os.getenv("OSDU_BASE_URL", "equinordev.energy.azure.com")

# Required header for all ADME/OSDU calls.
DATA_PARTITION_ID: str = os.getenv("DATA_PARTITION_ID", "").strip()

def _partition_suffix() -> str:
    # e.g., "dp1.dataservices.energy"
    return f"{DATA_PARTITION_ID}.dataservices.energy" if DATA_PARTITION_ID else "partition.dataservices.energy"

# Sensible defaults for the "Create Dataspace" form (can be overridden in env)
DEFAULT_LEGAL_TAG: str = os.getenv("DEFAULT_LEGAL_TAG", f"{DATA_PARTITION_ID}-RDDMS-Legal-Tag" if DATA_PARTITION_ID else "dp1-RDDMS-Legal-Tag")

_default_owners = os.getenv("DEFAULT_OWNERS", f"data.default.owners@{_partition_suffix()}")
DEFAULT_OWNERS: List[str] = [x.strip() for x in _default_owners.split(",") if x.strip()]

_default_viewers = os.getenv("DEFAULT_VIEWERS", f"data.default.viewers@{_partition_suffix()}")
DEFAULT_VIEWERS: List[str] = [x.strip() for x in _default_viewers.split(",") if x.strip()]

_default_countries = os.getenv("DEFAULT_COUNTRIES", "US")
DEFAULT_COUNTRIES: List[str] = [x.strip() for x in _default_countries.split(",") if x.strip()]

# ----------------------------------------------------------------------
# HTTP utils
# ----------------------------------------------------------------------

def headers(access_token: str) -> Dict[str, str]:
    if not DATA_PARTITION_ID:
        log.warning("DATA_PARTITION_ID env var is not set; calls may fail")
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "data-partition-id": DATA_PARTITION_ID,
    }

# ----------------------------------------------------------------------
# Dataspaces
# ----------------------------------------------------------------------

async def list_dataspaces(access_token: str) -> List[Dict[str, Any]]:
    """GET /api/reservoir-ddms/v2/dataspaces"""
    url = f"https://{OSDU_BASE_URL}/api/reservoir-ddms/v2/dataspaces"
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(url, headers=headers(access_token))
        r.raise_for_status()
        return r.json() or []

async def create_dataspace(
    access_token: str,
    path: str,
    *,
    legal_tag: str,
    owners: List[str],
    viewers: List[str],
    countries: List[str],
    extra_custom: Optional[Dict[str, Any]] = None,
) -> Any:
    """POST /api/reservoir-ddms/v2/dataspaces"""
    url = f"https://{OSDU_BASE_URL}/api/reservoir-ddms/v2/dataspaces"

    custom: Dict[str, Any] = {
        "legaltags": [legal_tag],
        "otherRelevantDataCountries": countries,
        "viewers": viewers,
        "owners": owners,
    }
    if extra_custom:
        # Do not let extra keys override reserved compliance ACL fields
        for k in ("legaltags", "otherRelevantDataCountries", "viewers", "owners"):
            extra_custom.pop(k, None)
        custom.update(extra_custom)

    payload = [
        {
            "DataspaceId": path,
            "Path": path,
            "CustomData": custom,
        }
    ]

    hdr = headers(access_token)
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, headers=hdr, json=payload)

    try:
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        corr = r.headers.get("x-correlation-id") or r.headers.get("x-request-id")
        log.error(
            "Dataspace create failed (%s) corr=%s\nURL=%s\nHeaders=%s\nPayload=%s\nResponseHeaders=%s\nBody=%s",
            r.status_code, corr, url, hdr, json.dumps(payload, indent=2),
            dict(r.headers), r.text
        )
        raise
    return r.json()

# ----------------------------------------------------------------------
# Types & resources
# ----------------------------------------------------------------------

async def list_types(access_token: str, ds_enc: str) -> List[Dict[str, Any]]:
    """GET /dataspaces/{dataspaceId}/resources -> list of {'name','count'}"""
    url = f"https://{OSDU_BASE_URL}/api/reservoir-ddms/v2/dataspaces/{ds_enc}/resources"
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(url, headers=headers(access_token))
        r.raise_for_status()
        return r.json() or []

async def list_resources(access_token: str, ds_enc: str, typ: str) -> List[Dict[str, Any]]:
    """GET /dataspaces/{dataspaceId}/resources/{dataObjectType}"""
    url = f"https://{OSDU_BASE_URL}/api/reservoir-ddms/v2/dataspaces/{ds_enc}/resources/{typ}"
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(url, headers=headers(access_token))
        r.raise_for_status()
        return r.json() or []

async def get_resource(
    access_token: str,
    ds_enc: str,
    typ: str,
    uuid: str,
    *,
    include_refs: bool = False,  # reserved for future expansion
) -> Dict[str, Any]:
    """GET /dataspaces/{dataspaceId}/resources/{dataObjectType}/{guid}"""
    url = f"https://{OSDU_BASE_URL}/api/reservoir-ddms/v2/dataspaces/{ds_enc}/resources/{typ}/{uuid}"
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(url, headers=headers(access_token))
        r.raise_for_status()
        return r.json() or {}

async def list_arrays(access_token: str, ds_enc: str, typ: str, uuid: str) -> List[Dict[str, Any]]:
    """GET arrays metadata list for an object."""
    url = f"https://{OSDU_BASE_URL}/api/reservoir-ddms/v2/dataspaces/{ds_enc}/resources/{typ}/{uuid}/arrays"
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(url, headers=headers(access_token))
        r.raise_for_status()
        return r.json() or []

async def read_array(
    access_token: str,
    ds_enc: str,
    typ: str,
    uuid: str,
    *,
    path_in_resource: str,
) -> Dict[str, Any]:
    """GET content of an array."""
    url = f"https://{OSDU_BASE_URL}/api/reservoir-ddms/v2/dataspaces/{ds_enc}/resources/{typ}/{uuid}/arrays/{path_in_resource}"
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(url, headers=headers(access_token))
        r.raise_for_status()
        return r.json() or {}

# ----------------------------------------------------------------------
# Helpers for UI features
# ----------------------------------------------------------------------

def extract_refs(obj: Dict[str, Any]) -> List[Dict[str, str]]:
    """Very lightweight scan for DataObjectReference-like dicts."""
    edges: List[Dict[str, str]] = []

    def _walk(x: Any):
        if isinstance(x, dict):
            ct = x.get("ContentType")
            uid = x.get("UUID") or x.get("Uuid")
            if ct and uid:
                edges.append({"contentType": ct, "uuid": str(uid)})
            for v in x.values():
                _walk(v)
        elif isinstance(x, list):
            for v in x:
                _walk(v)

    _walk(obj)
    return edges

def extract_grid2d_geometry(obj: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract minimal visualization metadata from a Grid2dRepresentation."""
    if not (obj.get("$type", "") or "").endswith("Grid2dRepresentation"):
        return None
    try:
        patch = obj["Grid2dPatch"]
        fast = int(patch["FastestAxisCount"])
        slow = int(patch["SlowestAxisCount"])
        geom = patch["Geometry"]
        pts = geom["Points"]
        origin = pts["Origin"]
        offsets = pts["Offset"]
        u = offsets[0]
        v = offsets[1]
        return {
            "fast": fast,
            "slow": slow,
            "origin": {
                "x": origin.get("Coordinate1", 0.0),
                "y": origin.get("Coordinate2", 0.0),
                "z": origin.get("Coordinate3", 0.0),
            },
            "u": {
                "dx": (u.get("Offset") or {}).get("Coordinate1", 0.0),
                "dy": (u.get("Offset") or {}).get("Coordinate2", 0.0),
                "spacing": ((u.get("Spacing") or {}).get("Value", 1.0)),
            },
            "v": {
                "dx": (v.get("Offset") or {}).get("Coordinate1", 0.0),
                "dy": (v.get("Offset") or {}).get("Coordinate2", 0.0),
                "spacing": ((v.get("Spacing") or {}).get("Value", 1.0)),
            },
        }
    except Exception:
        return None


# --- add these helpers to app/osdu.py ---

async def lock_dataspace(access_token: str, path: str) -> None:
    """
    POST /api/reservoir-ddms/v2/dataspaces/{dataspaceId}/lock
    """
    enc = urllib.parse.quote(path, safe="")
    url = f"https://{OSDU_BASE_URL}/api/reservoir-ddms/v2/dataspaces/{enc}/lock"
    hdr = headers(access_token)
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, headers=hdr)
    try:
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        corr = r.headers.get("x-correlation-id") or r.headers.get("x-request-id")
        log.error("Dataspace lock failed (%s) corr=%s path=%s body=%s",
                  r.status_code, corr, path, r.text)
        raise

async def unlock_dataspace(access_token: str, path: str) -> None:
    """
    DELETE /api/reservoir-ddms/v2/dataspaces/{dataspaceId}/lock
    """
    enc = urllib.parse.quote(path, safe="")
    url = f"https://{OSDU_BASE_URL}/api/reservoir-ddms/v2/dataspaces/{enc}/lock"
    hdr = headers(access_token)
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.delete(url, headers=hdr)
    try:
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        corr = r.headers.get("x-correlation-id") or r.headers.get("x-request-id")
        log.error("Dataspace unlock failed (%s) corr=%s path=%s body=%s",
                  r.status_code, corr, path, r.text)
        raise

async def delete_dataspace(access_token: str, path: str) -> None:
    """
    DELETE /api/reservoir-ddms/v2/dataspaces/{dataspaceId}
    """
    enc = urllib.parse.quote(path, safe="")
    url = f"https://{OSDU_BASE_URL}/api/reservoir-ddms/v2/dataspaces/{enc}"
    hdr = headers(access_token)
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.delete(url, headers=hdr)
    try:
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        corr = r.headers.get("x-correlation-id") or r.headers.get("x-request-id")
        log.error("Dataspace delete failed (%s) corr=%s path=%s body=%s",
                  r.status_code, corr, path, r.text)
        raise

def _dataspace_uri(path: str) -> str:
    # Canonical form seen in responses: eml:///dataspace('demo/Volve')
    return f"eml:///dataspace('{path}')"

async def build_manifest(
    access_token: str,
    path: str,
    *,
    legal_tag: str | None = None,
    owners: list[str] | None = None,
    viewers: list[str] | None = None,
    countries: list[str] | None = None,
    create_missing_refs: bool = True,
) -> dict:
    """
    POST /api/reservoir-ddms/v2/manifests/build
    Body typically includes: uris[], acl{}, legal{}, createMissingReferences
    """
    url = f"https://{OSDU_BASE_URL}/api/reservoir-ddms/v2/manifests/build"
    hdr = headers(access_token)

    # Use sensible defaults if not provided
    legal_tag = legal_tag or DEFAULT_LEGAL_TAG
    owners = owners or DEFAULT_OWNERS
    viewers = viewers or DEFAULT_VIEWERS
    countries = countries or DEFAULT_COUNTRIES

    body = {
        "uris": [ _dataspace_uri(path) ],
        "acl": {
            "owners": owners,
            "viewers": viewers,
        },
        "legal": {
            "legaltags": [legal_tag],
            "otherRelevantDataCountries": countries,
        },
        "createMissingReferences": bool(create_missing_refs),
    }

    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(url, headers=hdr, json=body)
    try:
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        corr = r.headers.get("x-correlation-id") or r.headers.get("x-request-id")
        log.error("Build manifest failed (%s) corr=%s path=%s body=%s",
                  r.status_code, corr, path, r.text)
        raise
    return r.json() or {}


# --- RDDMS v2 helpers (ADD at end of app/osdu.py) ---

async def list_all_resources(access_token: str, ds_enc: str) -> list[dict]:
    """GET /dataspaces/{dataspaceId}/resources/all"""
    url = f"https://{OSDU_BASE_URL}/api/reservoir-ddms/v2/dataspaces/{ds_enc}/resources/all"
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.get(url, headers=headers(access_token))
        r.raise_for_status()
        return r.json() or []

async def list_sources(access_token: str, ds_enc: str, typ: str, uuid: str) -> list[dict]:
    """GET /dataspaces/{dataspaceId}/resources/{type}/{uuid}/sources"""
    url = f"https://{OSDU_BASE_URL}/api/reservoir-ddms/v2/dataspaces/{ds_enc}/resources/{typ}/{uuid}/sources"
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.get(url, headers=headers(access_token))
        r.raise_for_status()
        return r.json() or []

async def list_targets(access_token: str, ds_enc: str, typ: str, uuid: str) -> list[dict]:
    """GET /dataspaces/{dataspaceId}/resources/{type}/{uuid}/targets"""
    url = f"https://{OSDU_BASE_URL}/api/reservoir-ddms/v2/dataspaces/{ds_enc}/resources/{typ}/{uuid}/targets"
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.get(url, headers=headers(access_token))
        r.raise_for_status()
        return r.json() or []

async def put_resources(
    access_token: str,
    ds_path: str,
    typ: str,
    objects: list[dict],
) -> dict:
    """PUT RESQML objects into a Reservoir DDMS v2 dataspace.

    PUT /api/reservoir-ddms/v2/dataspaces/{dataspaceId}/resources/{dataObjectType}
    Body: JSON array of RESQML objects.
    """
    enc = urllib.parse.quote(ds_path, safe="")
    url = f"https://{OSDU_BASE_URL}/api/reservoir-ddms/v2/dataspaces/{enc}/resources/{typ}"
    hdr = headers(access_token)
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.put(url, headers=hdr, json=objects)
    try:
        r.raise_for_status()
    except httpx.HTTPStatusError:
        corr = r.headers.get("x-correlation-id") or r.headers.get("x-request-id")
        log.error(
            "PUT resources failed (%s) corr=%s ds=%s type=%s body=%s",
            r.status_code, corr, ds_path, typ, r.text[:2000],
        )
        raise
    try:
        return r.json() or {}
    except Exception:
        return {"status": r.status_code, "text": r.text[:500]}


def _eml_uri_from_parts(path: str, typ: str, uuid: str) -> str:
    """Canonical EML URI fallback if object lacks 'uri'."""
    return f"eml:///dataspace('{path}')/{typ}('{uuid}')"

async def build_manifest_for_uris(
    access_token: str,
    uris: list[str],
    *,
    legal_tag: str | None = None,
    owners: list[str] | None = None,
    viewers: list[str] | None = None,
    countries: list[str] | None = None,
    create_missing_refs: bool = True,
) -> dict:
    """POST /api/reservoir-ddms/v2/manifests/build for arbitrary URIs."""
    url = f"https://{OSDU_BASE_URL}/api/reservoir-ddms/v2/manifests/build"
    hdr = headers(access_token)
    legal_tag = legal_tag or DEFAULT_LEGAL_TAG
    owners = owners or DEFAULT_OWNERS
    viewers = viewers or DEFAULT_VIEWERS
    countries = countries or DEFAULT_COUNTRIES
    body = {
        "uris": list(uris),
        "acl": {"owners": owners, "viewers": viewers},
        "legal": {"legaltags": [legal_tag], "otherRelevantDataCountries": countries},
        "createMissingReferences": bool(create_missing_refs),
    }
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(url, headers=hdr, json=body)
        r.raise_for_status()
        return r.json() or {}


# ======================================================================
# Grid2dRepresentation — full surface fetch + CRS-aware PNG rendering
# ======================================================================

def _normalize_obj(raw: Any, uuid: str) -> Dict[str, Any]:
    """Pick the right dict when the RDDMS returns a list."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        for it in raw:
            if isinstance(it, dict):
                uid = it.get("Uuid") or it.get("UUID") or it.get("uuid")
                if uid and str(uid).lower() == uuid.lower():
                    return it
        for it in raw:
            if isinstance(it, dict):
                return it
    return {}


async def fetch_grid2d_surface(
    access_token: str,
    ds: str,
    uuid: str,
) -> Dict[str, Any]:
    """
    Fetch a Grid2dRepresentation object, its z-values array, and its
    referenced LocalDepth3dCrs — everything needed to render a map.

    Returns a dict with:
      grid     – the Grid2d RESQML object (dict)
      zvalues  – flat list[float] of depth values
      dims     – [n_slow, n_fast]
      crs      – the LocalDepth3dCrs object (dict) or None
      geometry – parsed lattice geometry dict (origin, offsets, spacing)
    """
    enc = urllib.parse.quote(ds, safe="")
    typ = "resqml20.obj_Grid2dRepresentation"
    hdr = headers(access_token)

    async with httpx.AsyncClient(timeout=90) as client:
        # 1. Fetch the Grid2d metadata
        url_obj = f"https://{OSDU_BASE_URL}/api/reservoir-ddms/v2/dataspaces/{enc}/resources/{typ}/{uuid}"
        r1 = await client.get(url_obj, headers=hdr)
        r1.raise_for_status()
        grid = _normalize_obj(r1.json(), uuid)

        # 2. Parse lattice geometry from Grid2dPatch
        patch = grid.get("Grid2dPatch") or {}
        n_fast = int(patch.get("FastestAxisCount", 0))
        n_slow = int(patch.get("SlowestAxisCount", 0))
        geom = patch.get("Geometry") or {}
        points = geom.get("Points") or {}

        # Handle Point3dZValueArray (z separate from x-y lattice)
        supporting = points.get("SupportingGeometry") or {}
        origin_d = supporting.get("Origin") or points.get("Origin") or {}
        offsets = supporting.get("Offset") or points.get("Offset") or []

        geometry = _parse_lattice(origin_d, offsets, n_slow, n_fast)

        # 3. Resolve CRS (may be inline via _data or need separate fetch)
        crs_ref = geom.get("LocalCrs") or {}
        crs = crs_ref.get("_data")  # RDDMS often inlines it
        if not crs:
            crs_uuid = crs_ref.get("UUID") or crs_ref.get("Uuid")
            if crs_uuid:
                ct = crs_ref.get("ContentType", "")
                crs_typ = "resqml20.obj_LocalTime3dCrs" if "LocalTime3dCrs" in ct else "resqml20.obj_LocalDepth3dCrs"
                try:
                    url_crs = f"https://{OSDU_BASE_URL}/api/reservoir-ddms/v2/dataspaces/{enc}/resources/{crs_typ}/{crs_uuid}"
                    r_crs = await client.get(url_crs, headers=hdr)
                    r_crs.raise_for_status()
                    crs = _normalize_obj(r_crs.json(), crs_uuid)
                except Exception as e:
                    log.warning("fetch_grid2d_surface: CRS fetch failed: %s", e)
                    crs = None

        # 4. Discover array path and fetch z-values
        url_arrays = f"{url_obj}/arrays"
        r_al = await client.get(url_arrays, headers=hdr)
        r_al.raise_for_status()
        arr_list = r_al.json() or []

        arr_path = ""
        for a in arr_list:
            uid = a.get("uid") or {}
            pir = uid.get("pathInResource", "")
            if "points_patch" in pir or "zvalues" in pir:
                arr_path = pir
                break
        if not arr_path and arr_list:
            arr_path = (arr_list[0].get("uid") or {}).get("pathInResource", "")

        zvalues: List[float] = []
        if arr_path:
            arr_enc = urllib.parse.quote(arr_path, safe="")
            url_arr = f"{url_obj}/arrays/{arr_enc}"
            r_arr = await client.get(url_arr, headers=hdr, timeout=120)
            r_arr.raise_for_status()
            arr_body = r_arr.json() or {}
            inner = arr_body.get("data") or arr_body
            if isinstance(inner, dict):
                zvalues = inner.get("data") or inner.get("values") or []
            elif isinstance(inner, list):
                zvalues = inner

    return {
        "grid": grid,
        "zvalues": zvalues,
        "dims": [n_slow, n_fast],
        "crs": crs,
        "geometry": geometry,
    }


def _parse_lattice(
    origin_d: Dict[str, Any],
    offsets: List[Dict[str, Any]],
    n_slow: int,
    n_fast: int,
) -> Dict[str, Any]:
    """
    Parse a RESQML Point3dLatticeArray into a geometry dict.

    Returns:
      origin   – (x, y, z)
      u_vec    – (dx, dy) unit direction for slow axis
      v_vec    – (dx, dy) unit direction for fast axis
      u_space  – spacing along slow axis (metres)
      v_space  – spacing along fast axis (metres)
      n_slow, n_fast
    """
    ox = float(origin_d.get("Coordinate1", 0))
    oy = float(origin_d.get("Coordinate2", 0))
    oz = float(origin_d.get("Coordinate3", 0))

    def _offset_parts(off: Dict[str, Any]) -> Tuple[float, float, float]:
        o = off.get("Offset") or {}
        return (
            float(o.get("Coordinate1", 0)),
            float(o.get("Coordinate2", 0)),
            float(o.get("Coordinate3", 0)),
        )

    def _spacing(off: Dict[str, Any]) -> float:
        s = off.get("Spacing") or {}
        return float(s.get("Value", 1.0))

    u_dx, u_dy, _ = (0.0, 0.0, 0.0)
    v_dx, v_dy, _ = (0.0, 0.0, 0.0)
    u_space = 1.0
    v_space = 1.0

    if len(offsets) >= 1:
        u_dx, u_dy, _ = _offset_parts(offsets[0])
        u_space = _spacing(offsets[0])
    if len(offsets) >= 2:
        v_dx, v_dy, _ = _offset_parts(offsets[1])
        v_space = _spacing(offsets[1])

    return {
        "origin": (ox, oy, oz),
        "u_vec": (u_dx, u_dy),
        "v_vec": (v_dx, v_dy),
        "u_space": u_space,
        "v_space": v_space,
        "n_slow": n_slow,
        "n_fast": n_fast,
    }


def _apply_crs_rotation(
    geometry: Dict[str, Any],
    crs: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Apply the CRS ArealRotation + XOffset/YOffset to the geometry.

    RESQML LocalDepth3dCrs defines:
      - XOffset, YOffset   — translation of local origin w.r.t. projected CRS
      - ArealRotation      — counter-clockwise angle (degrees) from projected
                             CRS north to local CRS Y-axis

    The grid's origin and offset vectors are in local CRS coordinates.
    To map to projected coordinates:
      P_proj = R(θ) · P_local + (XOffset, YOffset)

    If ArealRotation is 0 and offsets are 0 (common case for RMS exports
    where rotation is baked into the offset vectors), this is a no-op.
    """
    if not crs:
        return geometry

    x_off = float(crs.get("XOffset", 0) or 0)
    y_off = float(crs.get("YOffset", 0) or 0)

    # ArealRotation
    rot_obj = crs.get("ArealRotation") or {}
    angle_deg = float(rot_obj.get("_", 0) or rot_obj.get("Value", 0) or 0)
    uom = (rot_obj.get("Uom") or "dega").lower()
    if "rad" in uom:
        angle_rad = angle_deg  # already radians
    else:
        angle_rad = math.radians(angle_deg)

    if abs(angle_rad) < 1e-12 and abs(x_off) < 1e-6 and abs(y_off) < 1e-6:
        return geometry  # nothing to do

    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)

    ox, oy, oz = geometry["origin"]
    # Rotate origin
    new_ox = cos_a * ox - sin_a * oy + x_off
    new_oy = sin_a * ox + cos_a * oy + y_off

    # Rotate offset vectors
    ux, uy = geometry["u_vec"]
    new_ux = cos_a * ux - sin_a * uy
    new_uy = sin_a * ux + cos_a * uy

    vx, vy = geometry["v_vec"]
    new_vx = cos_a * vx - sin_a * vy
    new_vy = sin_a * vx + cos_a * vy

    return {
        **geometry,
        "origin": (new_ox, new_oy, oz),
        "u_vec": (new_ux, new_uy),
        "v_vec": (new_vx, new_vy),
    }


def build_xy_mesh(
    geometry: Dict[str, Any],
    crs: Optional[Dict[str, Any]] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Build 2-D X and Y coordinate arrays (n_slow × n_fast) in projected CRS,
    correctly handling RESQML offset-vector rotation.

    Returns (X, Y) ndarrays suitable for matplotlib pcolormesh.
    """
    geo = _apply_crs_rotation(geometry, crs)

    ox, oy, _ = geo["origin"]
    ux, uy = geo["u_vec"]
    vx, vy = geo["v_vec"]
    u_sp = geo["u_space"]
    v_sp = geo["v_space"]
    n_slow = geo["n_slow"]
    n_fast = geo["n_fast"]

    # Row indices (slow axis) and col indices (fast axis)
    i = np.arange(n_slow, dtype=np.float64)
    j = np.arange(n_fast, dtype=np.float64)
    II, JJ = np.meshgrid(i, j, indexing="ij")  # shape (n_slow, n_fast)

    X = ox + II * (ux * u_sp) + JJ * (vx * v_sp)
    Y = oy + II * (uy * u_sp) + JJ * (vy * v_sp)
    return X, Y


def render_grid2d_png(
    zvalues: List[float],
    dims: List[int],
    geometry: Dict[str, Any],
    crs: Optional[Dict[str, Any]] = None,
    *,
    title: str = "",
    cmap: str = "viridis_r",
    figsize: Tuple[int, int] = (10, 8),
    dpi: int = 120,
    nan_sentinel: float = 1e30,
    unit: str = "m",
    show_crs_info: bool = True,
) -> bytes:
    """
    Render a Grid2dRepresentation depth surface as a PNG image.

    Handles:
      - RESQML offset-vector rotation (any angle)
      - CRS ArealRotation + XOffset/YOffset
      - Colour bar with depth range
      - UTM coordinate axes with grid lines
      - NaN masking

    Returns PNG bytes.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import Normalize
    from matplotlib.ticker import FuncFormatter

    n_slow, n_fast = dims[0], dims[1]
    total = n_slow * n_fast
    if len(zvalues) < total:
        # pad with NaN
        zvalues = list(zvalues) + [float("nan")] * (total - len(zvalues))

    Z = np.array(zvalues[:total], dtype=np.float64).reshape(n_slow, n_fast)
    # Mask sentinel and very large values
    Z[np.abs(Z) > nan_sentinel] = np.nan

    X, Y = build_xy_mesh(geometry, crs)

    # Determine if Z should be negated (ZIncreasingDownward means positive Z is deeper)
    z_down = True
    if crs and crs.get("ZIncreasingDownward") is False:
        z_down = False
    # For color mapping: shallower = lighter, deeper = darker  (viridis_r default)
    Z_plot = Z.copy()

    fig, ax = plt.subplots(1, 1, figsize=figsize, dpi=dpi)

    # Use pcolormesh for irregular/rotated grids
    valid = np.isfinite(Z_plot)
    if valid.any():
        vmin = float(np.nanmin(Z_plot))
        vmax = float(np.nanmax(Z_plot))
    else:
        vmin, vmax = 0, 1

    pcm = ax.pcolormesh(X, Y, Z_plot, cmap=cmap, shading="auto",
                        norm=Normalize(vmin=vmin, vmax=vmax))

    # Color bar
    cbar = fig.colorbar(pcm, ax=ax, shrink=0.85, pad=0.02)
    depth_label = f"Depth ({unit})"
    if z_down:
        depth_label += " — increasing downward"
    cbar.set_label(depth_label, fontsize=10)

    # Axis labels
    ax.set_xlabel("Easting (m)", fontsize=10)
    ax.set_ylabel("Northing (m)", fontsize=10)
    ax.set_aspect("equal")
    ax.grid(True, alpha=0.3, linewidth=0.5)
    ax.tick_params(labelsize=8)

    # Format ticks as km if range > 5000
    x_range = X.max() - X.min()
    y_range = Y.max() - Y.min()

    def _fmt_km(val, _):
        return f"{val / 1000:.1f}"

    if x_range > 5000 or y_range > 5000:
        ax.xaxis.set_major_formatter(FuncFormatter(_fmt_km))
        ax.yaxis.set_major_formatter(FuncFormatter(_fmt_km))
        ax.set_xlabel("Easting (km)", fontsize=10)
        ax.set_ylabel("Northing (km)", fontsize=10)

    # Title
    if title:
        ax.set_title(title, fontsize=12, fontweight="bold")

    # CRS annotation
    if show_crs_info and crs:
        crs_title = (crs.get("Citation") or {}).get("Title", "")
        rot_obj = crs.get("ArealRotation") or {}
        rot_val = rot_obj.get("_", 0) or rot_obj.get("Value", 0) or 0
        # Check for WKT in ExtraMetadata
        wkt_short = ""
        for em in (crs.get("ExtraMetadata") or []):
            if isinstance(em, dict) and "Wkt" in (em.get("Name") or ""):
                wkt = em.get("Value", "")
                # Extract projection name
                import re as _re
                m = _re.search(r'PROJCS\["([^"]+)"', wkt)
                if m:
                    wkt_short = m.group(1)
                break
        info_parts = []
        if crs_title:
            info_parts.append(crs_title)
        if wkt_short:
            info_parts.append(wkt_short)
        if abs(float(rot_val)) > 0.001:
            info_parts.append(f"rot={rot_val}°")
        if info_parts:
            ax.annotate(
                " | ".join(info_parts),
                xy=(0.01, 0.01), xycoords="axes fraction",
                fontsize=7, color="gray", alpha=0.8,
            )

    # Compute rotation angle from offset vectors for annotation
    ux, uy = geometry.get("u_vec", (1, 0))
    angle = math.degrees(math.atan2(ux, uy))  # angle from north
    if abs(angle) > 0.1:
        ax.annotate(
            f"Grid rotation: {angle:.1f}° from N",
            xy=(0.99, 0.01), xycoords="axes fraction",
            fontsize=7, color="gray", alpha=0.8, ha="right",
        )

    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=dpi)
    plt.close(fig)
    buf.seek(0)
    return buf.read()
