#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_structuremap_from_resqml.py - Demonstrate the bidirectional mapping
between RESQML 2.2 Grid2dRepresentation and OSDU StructureMap:1.0.0.

Two modes:
  1. RESQML → OSDU: Read a RESQML JSON document, extract depth
     Grid2dRepresentations, emit OSDU StructureMap:1.0.0 records.
  2. OSDU → RESQML: Read an OSDU StructureMap record, emit a
     RESQML 2.2 Grid2dRepresentation JSON.

Usage:
  # RESQML → OSDU (from test JSON in references/)
  python gen_structuremap_from_resqml.py --from-resqml references/testHorizonEverythingIncluded.json

  # OSDU → RESQML (from generated manifest)
  python gen_structuremap_from_resqml.py --from-osdu manifest_volantis_interp.json --horizon TopVolantis

  # Both directions (round-trip demo)
  python gen_structuremap_from_resqml.py --round-trip
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from _shared import (
    stable_uuid,
    wpc_id,
    acl_block,
    legal_block,
    bearing_to_offsets,
    offsets_to_bearing,
    abcd_corners,
    save_json,
)

SCRIPT_DIR = Path(__file__).resolve().parent
REFERENCES = SCRIPT_DIR / "references"

# ── RESQML type constants ──────────────────────────────────────────────
RESQML_GRID2D = "resqml22.Grid2dRepresentation"
RESQML_HORIZON_INTERP = "resqml22.HorizonInterpretation"
RESQML_BOUNDARY_FEATURE = "resqml22.BoundaryFeature"
RESQML_LOCAL_CRS = "eml23.LocalEngineeringCompoundCrs"
RESQML_LATTICE = "resqml22.Point3dLatticeArray"
RESQML_FROM_REP_LATTICE = "resqml22.Point3dFromRepresentationLatticeArray"

# ── Defaults ───────────────────────────────────────────────────────────
DEFAULT_PREFIX = "dev"
DEFAULT_RDDMS = "rddms-1"
DEFAULT_DATASPACE = "demo/volantis-interp"


# =====================================================================
# Direction 1: RESQML → OSDU StructureMap
# =====================================================================

def _is_depth_crs(crs_obj: dict) -> bool:
    """Check if a LocalEngineeringCompoundCrs represents depth (not time)."""
    vert_axis = crs_obj.get("VerticalAxis", {})
    return vert_axis.get("IsTime") is False


def _find_objects_by_type(doc: list[dict], qtype: str) -> list[dict]:
    """Find all objects of a given qualified type in a RESQML document."""
    return [obj for obj in doc if obj.get("$type") == qtype]


def _resolve_ref(doc: list[dict], uuid: str) -> Optional[dict]:
    """Resolve a DataObjectReference UUID to the actual object in the document."""
    for obj in doc:
        if obj.get("Uuid") == uuid:
            return obj
    return None


def _extract_lattice_grid(geometry: dict) -> Optional[Dict[str, Any]]:
    """Extract grid parameters from a Point3dLatticeArray geometry.

    Returns dict with: origin_e, origin_n, bearing_j, width_i, width_j,
    count_i, count_j, transform_method, or None if not a lattice.
    """
    points = geometry.get("Points", {})
    support = points.get("SupportingGeometry", {})
    stype = support.get("$type", "")

    if stype == RESQML_LATTICE:
        origin = support.get("Origin", {})
        dims = support.get("Dimension", [])
        if len(dims) < 2:
            return None

        # Dimension[0] = slow axis (J in OSDU convention)
        # Dimension[1] = fast axis (I in OSDU convention)
        dim_slow = dims[0]
        dim_fast = dims[1]

        dir_slow = dim_slow.get("Direction", {})
        dir_fast = dim_fast.get("Direction", {})

        # Spacing - can be constant or variable
        slow_spacing = dim_slow.get("Spacing", {})
        fast_spacing = dim_fast.get("Spacing", {})

        width_slow = slow_spacing.get("Value", 0)
        width_fast = fast_spacing.get("Value", 0)
        count_slow = slow_spacing.get("Count", 0) + 1  # Count = n_steps → n_nodes = n_steps + 1
        count_fast = fast_spacing.get("Count", 0) + 1

        # Direction vectors → bearing
        # Direction is a Point3d: Coordinate1=X(easting), Coordinate2=Y(northing), Coordinate3=Z
        dx_slow = dir_slow.get("Coordinate1", 0)
        dy_slow = dir_slow.get("Coordinate2", 0)
        dx_fast = dir_fast.get("Coordinate1", 0)
        dy_fast = dir_fast.get("Coordinate2", 0)

        # Compute bearings from direction vectors
        # bearing = clockwise angle from north = atan2(dx, dy) in degrees
        if abs(dx_slow) < 1e-10 and abs(dy_slow) < 1e-10:
            # Slow axis is vertical (Z-only) - not a standard 2D grid for mapping
            # Use fast axis as the J-axis and derive I from orthogonality
            bearing_j, _ = offsets_to_bearing(dx_fast, dy_fast)
            bearing_i = (bearing_j + 90.0) % 360
            width_j = width_fast
            width_i = width_slow
            count_j = count_fast
            count_i = count_slow
        else:
            # Standard case: slow=J, fast=I
            bearing_j, _ = offsets_to_bearing(dx_slow, dy_slow)
            bearing_i, _ = offsets_to_bearing(dx_fast, dy_fast)
            width_j = width_slow
            width_i = width_fast
            count_j = count_slow
            count_i = count_fast

        # Determine handedness (TransformationMethod)
        # If I-axis = J-axis + 90° → right-handed (EPSG 9666)
        # If I-axis = J-axis - 90° → left-handed (EPSG 1049)
        expected_rh = (bearing_j + 90.0) % 360
        expected_lh = (bearing_j - 90.0) % 360
        is_orthogonal = support.get("AllDimensionsAreOrthogonal", False)

        if is_orthogonal:
            diff_rh = abs(bearing_i - expected_rh) % 360
            diff_lh = abs(bearing_i - expected_lh) % 360
            transform = 9666 if diff_rh < 1.0 or diff_rh > 359.0 else 1049
        else:
            transform = 9666  # default assumption

        return {
            "origin_e": origin.get("Coordinate1", 0),
            "origin_n": origin.get("Coordinate2", 0),
            "bearing_j": bearing_j,
            "width_i": width_i,
            "width_j": width_j,
            "count_i": count_i,
            "count_j": count_j,
            "transform_method": transform,
            "grid_type": "inline",
        }

    elif stype == RESQML_FROM_REP_LATTICE:
        # External supporting representation → extract UUID
        sup_rep = support.get("SupportingRepresentation", {})
        return {
            "supporting_rep_uuid": sup_rep.get("Uuid"),
            "supporting_rep_title": sup_rep.get("Title"),
            "supporting_rep_type": sup_rep.get("QualifiedType"),
            "grid_type": "external",
        }

    return None


def resqml_to_structuremap(
    resqml_doc: list[dict],
    prefix: str = DEFAULT_PREFIX,
    rddms_host: str = DEFAULT_RDDMS,
    dataspace: str = DEFAULT_DATASPACE,
) -> list[dict]:
    """Convert RESQML Grid2dRepresentations (depth domain) to OSDU StructureMap records.

    Args:
        resqml_doc: List of RESQML objects (the 'document' array).
        prefix: OSDU namespace prefix.
        rddms_host: RDDMS host name for DDMSDatasets URI.
        dataspace: RDDMS dataspace name.

    Returns:
        List of OSDU StructureMap:1.0.0 records.
    """
    grid2ds = _find_objects_by_type(resqml_doc, RESQML_GRID2D)
    crs_objects = {
        obj["Uuid"]: obj
        for obj in resqml_doc
        if obj.get("$type") == RESQML_LOCAL_CRS
    }

    structure_maps = []

    for g2d in grid2ds:
        # Check if this is SurfaceRole=map (skip lattice-only grids with no Z values)
        role = g2d.get("SurfaceRole", "")
        if role and role != "map":
            continue

        # Check CRS domain
        geom = g2d.get("Geometry", {})
        local_crs_ref = geom.get("LocalCrs", {})
        crs_uuid = local_crs_ref.get("Uuid", "")
        crs_obj = crs_objects.get(crs_uuid)

        # If we can check the CRS, verify it's depth
        is_depth = True  # default assumption if CRS not resolvable
        if crs_obj:
            is_depth = _is_depth_crs(crs_obj)

        if not is_depth:
            print(f"  Skipping TWT Grid2dRepresentation: {g2d.get('Citation', {}).get('Title', '?')}")
            continue

        # Extract basic properties
        uuid = g2d.get("Uuid", "")
        title = g2d.get("Citation", {}).get("Title", "Unnamed")
        fastest = g2d.get("FastestAxisCount", 0)
        slowest = g2d.get("SlowestAxisCount", 0)

        # Represented interpretation
        rep_obj = g2d.get("RepresentedObject", {})
        interp_uuid = rep_obj.get("Uuid", "")
        interp_type = rep_obj.get("QualifiedType", "")
        interp_title = rep_obj.get("Title", "")

        # Resolve interpretation to OSDU ID
        interp_id = ""
        if interp_uuid and "HorizonInterpretation" in interp_type:
            interp_osdu_uuid = stable_uuid(f"resqml-interp:{interp_uuid}")
            interp_id = wpc_id(prefix, "HorizonInterpretation", interp_osdu_uuid)

        # Extract grid geometry
        grid_info = _extract_lattice_grid(geom)

        # Build the StructureMap record
        smap_uuid = stable_uuid(f"resqml-smap:{uuid}")
        smap_data: Dict[str, Any] = {
            "Name": title,
            "Description": f"StructureMap generated from RESQML Grid2dRepresentation {uuid}",
            "DomainTypeID": f"{prefix}:reference-data--DomainType:Depth:",
            "DDMSDatasets": [
                f"eml://{rddms_host}/dataspace('{dataspace}')/resqml22.Grid2dRepresentation('{uuid}')"
            ],
        }

        if interp_id:
            smap_data["InterpretationID"] = interp_id

        # Populate grid properties based on pattern
        if grid_info:
            if grid_info["grid_type"] == "inline":
                # Inline lattice → populate AbstractGenericBinGrid properties
                bearing_i = (grid_info["bearing_j"] + 90.0) % 360
                corners = abcd_corners(
                    grid_info["origin_e"], grid_info["origin_n"],
                    bearing_i, grid_info["width_i"], grid_info["count_i"],
                    grid_info["bearing_j"], grid_info["width_j"], grid_info["count_j"],
                )
                smap_data.update({
                    "BinGridName": f"{title} grid",
                    "OriginEasting": grid_info["origin_e"],
                    "OriginNorthing": grid_info["origin_n"],
                    "BinWidthOnIaxis": grid_info["width_i"],
                    "BinWidthOnJaxis": grid_info["width_j"],
                    "MapGridBearingOfBinGridJaxis": grid_info["bearing_j"],
                    "NodeCountOnIAxis": grid_info["count_i"],
                    "NodeCountOnJAxis": grid_info["count_j"],
                    "TransformationMethod": grid_info["transform_method"],
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
                })
            elif grid_info["grid_type"] == "external":
                # External supporting representation → BinGridID
                sup_uuid = grid_info["supporting_rep_uuid"]
                if sup_uuid:
                    grid_osdu_uuid = stable_uuid(f"resqml-grid:{sup_uuid}")
                    # Could be GenericBinGrid or SeismicBinGrid - default to GenericBinGrid
                    smap_data["BinGridID"] = wpc_id(prefix, "GenericBinGrid", grid_osdu_uuid)

        smap_record = {
            "id": wpc_id(prefix, "StructureMap", smap_uuid),
            "kind": "osdu:wks:work-product-component--StructureMap:1.0.0",
            "acl": acl_block(),
            "legal": legal_block(),
            "data": smap_data,
        }

        structure_maps.append(smap_record)
        print(f"  Generated StructureMap: {title}  (grid={grid_info['grid_type'] if grid_info else 'unknown'})")

    return structure_maps


# =====================================================================
# Direction 2: OSDU StructureMap → RESQML Grid2dRepresentation
# =====================================================================

def structuremap_to_resqml(
    smap_record: dict,
    z_values: Optional[list[float]] = None,
) -> list[dict]:
    """Convert an OSDU StructureMap:1.0.0 record to RESQML 2.2 JSON objects.

    Args:
        smap_record: OSDU StructureMap record (with 'data' block).
        z_values: Optional Z values array. If None, generates synthetic values.

    Returns:
        List of RESQML JSON objects (Grid2dRepresentation + CRS + Feature + Interpretation).
    """
    data = smap_record.get("data", {})
    name = data.get("Name", "Unnamed StructureMap")

    # Generate deterministic UUIDs for RESQML objects
    smap_id = smap_record.get("id", "")
    g2d_uuid = stable_uuid(f"osdu-to-resqml:g2d:{smap_id}")
    crs_uuid = stable_uuid(f"osdu-to-resqml:crs:{smap_id}")
    vert_crs_uuid = stable_uuid(f"osdu-to-resqml:vertcrs:{smap_id}")
    eng2d_uuid = stable_uuid(f"osdu-to-resqml:eng2d:{smap_id}")
    proj_crs_uuid = stable_uuid(f"osdu-to-resqml:projcrs:{smap_id}")

    objects: list[dict] = []

    # ── Determine grid geometry ──
    has_inline_grid = data.get("OriginEasting") is not None
    bin_grid_id = data.get("BinGridID")

    if has_inline_grid:
        # Inline grid → Point3dLatticeArray
        origin_e = data.get("OriginEasting", 0)
        origin_n = data.get("OriginNorthing", 0)
        width_i = data.get("BinWidthOnIaxis", 25.0)
        width_j = data.get("BinWidthOnJaxis", 25.0)
        bearing_j = data.get("MapGridBearingOfBinGridJaxis", 90.0)
        count_i = data.get("NodeCountOnIAxis", 10)
        count_j = data.get("NodeCountOnJAxis", 10)
        transform = data.get("TransformationMethod", 9666)

        # Compute direction vectors
        dj = bearing_to_offsets(bearing_j, 1.0)  # unit direction for J axis
        if transform == 9666:
            bearing_i = (bearing_j + 90.0) % 360
        else:
            bearing_i = (bearing_j - 90.0) % 360
        di = bearing_to_offsets(bearing_i, 1.0)  # unit direction for I axis

        is_orthogonal = True
        fastest = count_i
        slowest = count_j

        # Generate synthetic Z values if not provided
        if z_values is None:
            n_total = count_i * count_j
            z_values = [round(-2000.0 - i * 0.5, 1) for i in range(n_total)]

        supporting_geometry = {
            "$type": RESQML_LATTICE,
            "AllDimensionsAreOrthogonal": is_orthogonal,
            "Origin": {
                "$type": "resqml22.Point3d",
                "Coordinate1": origin_e,
                "Coordinate2": origin_n,
                "Coordinate3": 0,
            },
            "Dimension": [
                {
                    "$type": "resqml22.Point3dLatticeDimension",
                    "Direction": {
                        "$type": "resqml22.Point3d",
                        "Coordinate1": dj[0],
                        "Coordinate2": dj[1],
                        "Coordinate3": 0,
                    },
                    "Spacing": {
                        "$type": "eml23.FloatingPointConstantArray",
                        "Value": width_j,
                        "Count": count_j - 1,
                    },
                },
                {
                    "$type": "resqml22.Point3dLatticeDimension",
                    "Direction": {
                        "$type": "resqml22.Point3d",
                        "Coordinate1": di[0],
                        "Coordinate2": di[1],
                        "Coordinate3": 0,
                    },
                    "Spacing": {
                        "$type": "eml23.FloatingPointConstantArray",
                        "Value": width_i,
                        "Count": count_i - 1,
                    },
                },
            ],
        }

    elif bin_grid_id:
        # External bin grid → Point3dFromRepresentationLatticeArray
        # Extract UUID from BinGridID (format: prefix:wpc--Entity:uuid:version)
        parts = bin_grid_id.split(":")
        sup_uuid = parts[-2] if len(parts) >= 3 else stable_uuid(f"bingrid:{bin_grid_id}")

        fastest = 0  # Not known without the grid
        slowest = 0

        if z_values is None:
            z_values = [-2000.0, -2001.0, -2002.0, -2003.0]
            fastest = 2
            slowest = 2

        supporting_geometry = {
            "$type": RESQML_FROM_REP_LATTICE,
            "NodeIndicesOnSupportingRepresentation": {
                "$type": "eml23.IntegerLatticeArray",
                "StartValue": 0,
                "Offset": [
                    {"$type": "eml23.IntegerConstantArray", "Value": 1, "Count": slowest - 1},
                    {"$type": "eml23.IntegerConstantArray", "Value": 1, "Count": fastest - 1},
                ],
            },
            "SupportingRepresentation": {
                "$type": "eml23.DataObjectReference",
                "Uuid": sup_uuid,
                "QualifiedType": RESQML_GRID2D,
                "Title": f"BinGrid for {name}",
            },
        }
    else:
        print(f"  WARNING: No grid info for {name}, using minimal geometry")
        fastest = 2
        slowest = 2
        z_values = z_values or [-2000.0, -2001.0, -2002.0, -2003.0]
        supporting_geometry = {
            "$type": RESQML_LATTICE,
            "AllDimensionsAreOrthogonal": True,
            "Origin": {"$type": "resqml22.Point3d", "Coordinate1": 0, "Coordinate2": 0, "Coordinate3": 0},
            "Dimension": [
                {"$type": "resqml22.Point3dLatticeDimension",
                 "Direction": {"$type": "resqml22.Point3d", "Coordinate1": 0, "Coordinate2": 1, "Coordinate3": 0},
                 "Spacing": {"$type": "eml23.FloatingPointConstantArray", "Value": 25.0, "Count": 1}},
                {"$type": "resqml22.Point3dLatticeDimension",
                 "Direction": {"$type": "resqml22.Point3d", "Coordinate1": 1, "Coordinate2": 0, "Coordinate3": 0},
                 "Spacing": {"$type": "eml23.FloatingPointConstantArray", "Value": 25.0, "Count": 1}},
            ],
        }

    # ── Build the Grid2dRepresentation ──
    grid2d = {
        "$type": RESQML_GRID2D,
        "SchemaVersion": "2.2",
        "Uuid": g2d_uuid,
        "Citation": {
            "$type": "eml23.Citation",
            "Title": name,
            "Originator": "ORES gen_structuremap_from_resqml.py",
            "Creation": "2026-04-07T00:00:00.000Z",
            "Format": "ORES:gen_structuremap_from_resqml:1.0",
        },
        "SurfaceRole": "map",
        "FastestAxisCount": fastest,
        "SlowestAxisCount": slowest,
        "Geometry": {
            "$type": "resqml22.PointGeometry",
            "LocalCrs": {
                "$type": "eml23.DataObjectReference",
                "Uuid": crs_uuid,
                "QualifiedType": RESQML_LOCAL_CRS,
                "Title": f"Depth CRS for {name}",
            },
            "Points": {
                "$type": "resqml22.Point3dZValueArray",
                "SupportingGeometry": supporting_geometry,
                "ZValues": {
                    "$type": "eml23.FloatingPointXmlArray",
                    "CountPerValue": 1,
                    "Values": z_values[:fastest * slowest] if fastest and slowest else z_values,
                },
            },
        },
    }

    # Add OSDU integration metadata
    interp_id = data.get("InterpretationID", "")
    if interp_id:
        grid2d["OSDUIntegration"] = {
            "OSDULineageAssertion": {
                "$type": "eml23.OSDULineageAssertion",
                "ID": smap_record.get("id", ""),
                "LineageRelationshipKind": "direct",
            },
        }

    # Add ExtraMetadata for OSDU round-tripping
    extra_meta = []
    if data.get("SeismicHorizonID"):
        extra_meta.append({"Name": "osdu:SeismicHorizonID", "Value": data["SeismicHorizonID"]})
    domain = data.get("DomainTypeID", "")
    if domain:
        extra_meta.append({"Name": "osdu:DomainTypeID", "Value": domain})
    transform = data.get("TransformationMethod")
    if transform:
        extra_meta.append({"Name": "osdu:TransformationMethod", "Value": str(transform)})

    if extra_meta:
        grid2d["ExtraMetadata"] = [
            {"$type": "eml23.NameValuePair", **em} for em in extra_meta
        ]

    # Add RepresentedObject if we have an interpretation ref
    if interp_id:
        interp_uuid_val = stable_uuid(f"osdu-to-resqml:interp:{interp_id}")
        grid2d["RepresentedObject"] = {
            "$type": "eml23.DataObjectReference",
            "Uuid": interp_uuid_val,
            "QualifiedType": RESQML_HORIZON_INTERP,
            "Title": data.get("Name", "").replace(" Depth Map", " Interpretation"),
        }

    objects.append(grid2d)

    # ── Build Depth CRS ──
    depth_crs = {
        "$type": RESQML_LOCAL_CRS,
        "SchemaVersion": "2.3",
        "Uuid": crs_uuid,
        "Citation": {
            "$type": "eml23.Citation",
            "Title": f"Depth CRS for {name}",
            "Originator": "ORES",
            "Creation": "2026-04-07T00:00:00.000Z",
            "Format": "ORES:gen_structuremap_from_resqml:1.0",
        },
        "VerticalCrs": {
            "$type": "eml23.DataObjectReference",
            "Uuid": vert_crs_uuid,
            "QualifiedType": "eml23.VerticalCrs",
            "Title": f"Vertical CRS for {name}",
        },
        "OriginVerticalCoordinate": 0,
        "VerticalAxis": {
            "$type": "eml23.VerticalAxis",
            "Direction": "down",
            "Uom": "m",
            "IsTime": False,  # DEPTH domain - this is the key flag
        },
        "LocalEngineering2dCrs": {
            "$type": "eml23.DataObjectReference",
            "Uuid": eng2d_uuid,
            "QualifiedType": "eml23.LocalEngineering2dCrs",
            "Title": f"2D CRS for {name}",
        },
    }
    objects.append(depth_crs)

    # ── Build VerticalCrs ──
    vert_crs = {
        "$type": "eml23.VerticalCrs",
        "SchemaVersion": "2.3",
        "Uom": "m",
        "Uuid": vert_crs_uuid,
        "Citation": {
            "$type": "eml23.Citation",
            "Title": f"Vertical CRS for {name}",
            "Originator": "ORES",
            "Creation": "2026-04-07T00:00:00.000Z",
            "Format": "ORES:gen_structuremap_from_resqml:1.0",
        },
        "Direction": "down",
        "AbstractVerticalCrs": {
            "$type": "eml23.VerticalUnknownCrs",
            "Unknown": "Unknown",
        },
    }
    objects.append(vert_crs)

    return objects


# =====================================================================
# Round-trip demonstration
# =====================================================================

def demo_resqml_to_osdu():
    """Demo: Read testHorizonEverythingIncluded.json → OSDU StructureMap."""
    print("\n" + "=" * 70)
    print("DIRECTION 1: RESQML → OSDU StructureMap")
    print("=" * 70)

    json_path = REFERENCES / "testHorizonEverythingIncluded.json"
    if not json_path.exists():
        print(f"  ERROR: {json_path} not found")
        return []

    doc = json.loads(json_path.read_text("utf-8"))
    resqml_objects = doc.get("document", doc if isinstance(doc, list) else [doc])

    print(f"  Loaded {len(resqml_objects)} RESQML objects from {json_path.name}")
    print(f"  Types: {', '.join(set(o.get('$type', '?') for o in resqml_objects))}")

    smaps = resqml_to_structuremap(resqml_objects)
    print(f"\n  Generated {len(smaps)} StructureMap record(s)")

    if smaps:
        outpath = SCRIPT_DIR / "structuremap_from_resqml.json"
        save_json(smaps, outpath)

    return smaps


def demo_osdu_to_resqml():
    """Demo: Read manifest_volantis_interp.json → RESQML JSON."""
    print("\n" + "=" * 70)
    print("DIRECTION 2: OSDU StructureMap → RESQML Grid2dRepresentation")
    print("=" * 70)

    manifest_path = SCRIPT_DIR / "manifest_volantis_interp.json"
    if not manifest_path.exists():
        print(f"  ERROR: {manifest_path} not found - run gen_volantis_interp.py first")
        return []

    manifest = json.loads(manifest_path.read_text("utf-8"))
    wpcs = manifest.get("Data", {}).get("WorkProductComponents", [])

    # Find StructureMap records
    smaps = [r for r in wpcs if "StructureMap" in r.get("kind", "")]
    print(f"  Found {len(smaps)} StructureMap record(s) in manifest")

    all_resqml = []
    for smap in smaps:
        name = smap.get("data", {}).get("Name", "?")
        print(f"\n  Converting: {name}")
        resqml_objs = structuremap_to_resqml(smap)
        all_resqml.extend(resqml_objs)
        print(f"    → {len(resqml_objs)} RESQML objects")

    if all_resqml:
        resqml_doc = {"document": all_resqml}
        outpath = SCRIPT_DIR / "resqml_from_structuremap.json"
        save_json(resqml_doc, outpath)

    return all_resqml


def demo_round_trip():
    """Demo: RESQML → OSDU → RESQML round-trip."""
    print("\n" + "=" * 70)
    print("ROUND-TRIP: RESQML → OSDU StructureMap → RESQML")
    print("=" * 70)

    # Step 1: RESQML → OSDU
    print("\n── Step 1: RESQML → OSDU ──")
    json_path = REFERENCES / "testHorizonEverythingIncluded.json"
    doc = json.loads(json_path.read_text("utf-8"))
    resqml_objects = doc.get("document", doc if isinstance(doc, list) else [doc])

    smaps = resqml_to_structuremap(resqml_objects)
    if not smaps:
        print("  No StructureMap generated - cannot round-trip")
        return

    smap = smaps[0]
    print(f"  OSDU StructureMap: {smap['data']['Name']}")
    print(f"  Grid type: {'inline' if smap['data'].get('OriginEasting') else 'external'}")

    # Step 2: OSDU → RESQML
    print("\n── Step 2: OSDU → RESQML ──")
    resqml_rt = structuremap_to_resqml(smap)
    g2d = [o for o in resqml_rt if o.get("$type") == RESQML_GRID2D][0]

    print(f"  RESQML Grid2dRepresentation: {g2d['Citation']['Title']}")
    print(f"  SurfaceRole: {g2d.get('SurfaceRole')}")
    print(f"  CRS IsTime: {[o for o in resqml_rt if o.get('$type') == RESQML_LOCAL_CRS][0]['VerticalAxis']['IsTime']}")

    # Compare
    print("\n── Comparison ──")
    orig_g2d = [o for o in resqml_objects if o.get("$type") == RESQML_GRID2D][0]
    print(f"  Original Title:     {orig_g2d['Citation']['Title']}")
    print(f"  Round-trip Title:   {g2d['Citation']['Title']}")
    print(f"  Original UUID:      {orig_g2d['Uuid']}")
    print(f"  Round-trip UUID:    {g2d['Uuid']} (new - deterministic from OSDU ID)")
    print(f"  RepresentedObject match: {g2d.get('RepresentedObject', {}).get('QualifiedType') == orig_g2d.get('RepresentedObject', {}).get('QualifiedType')}")

    if g2d.get("ExtraMetadata"):
        print(f"  ExtraMetadata (OSDU round-trip data):")
        for em in g2d["ExtraMetadata"]:
            print(f"    {em['Name']} = {em['Value']}")

    outpath = SCRIPT_DIR / "resqml_roundtrip.json"
    save_json({"document": resqml_rt}, outpath)
    print(f"\n  Round-trip complete. Output: {outpath.name}")


# =====================================================================
# CLI
# =====================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Bidirectional RESQML ↔ OSDU StructureMap mapping"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--from-resqml", metavar="FILE",
                       help="Convert RESQML JSON → OSDU StructureMap")
    group.add_argument("--from-osdu", metavar="FILE",
                       help="Convert OSDU manifest → RESQML JSON")
    group.add_argument("--round-trip", action="store_true",
                       help="Run round-trip demo")
    parser.add_argument("--horizon", help="Horizon name filter (for --from-osdu)")
    parser.add_argument("--prefix", default="dev", help="OSDU namespace prefix")
    args = parser.parse_args()

    if args.from_resqml:
        path = Path(args.from_resqml)
        doc = json.loads(path.read_text("utf-8"))
        objs = doc.get("document", doc if isinstance(doc, list) else [doc])
        smaps = resqml_to_structuremap(objs, prefix=args.prefix)
        if smaps:
            save_json(smaps, SCRIPT_DIR / "structuremap_from_resqml.json")
        print(f"Generated {len(smaps)} StructureMap record(s)")

    elif args.from_osdu:
        path = Path(args.from_osdu)
        manifest = json.loads(path.read_text("utf-8"))
        wpcs = manifest.get("Data", {}).get("WorkProductComponents", [])
        smaps = [r for r in wpcs if "StructureMap" in r.get("kind", "")]
        if args.horizon:
            smaps = [r for r in smaps if args.horizon in r.get("data", {}).get("Name", "")]
        all_resqml = []
        for smap in smaps:
            all_resqml.extend(structuremap_to_resqml(smap))
        if all_resqml:
            save_json({"document": all_resqml}, SCRIPT_DIR / "resqml_from_structuremap.json")
        print(f"Generated {len(all_resqml)} RESQML object(s) from {len(smaps)} StructureMap(s)")

    elif args.round_trip:
        demo_round_trip()

    else:
        # Default: run all demos
        demo_resqml_to_osdu()
        demo_osdu_to_resqml()
        demo_round_trip()


if __name__ == "__main__":
    main()
