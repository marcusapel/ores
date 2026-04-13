#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_volantis_interp.py — Generate a complete OSDU manifest for the
Volantis 2025 Interpretation worked example.

Demonstrates the full M27 interpretation chain:
  LocalBoundaryFeature → HorizonInterpretation → SeismicHorizon:2.1.0 → StructureMap:1.0.0
  with GenericBinGrid:1.0.0 and SeismicInterpretationProject:1.0.0

Output: manifest_volantis_interp.json

Usage:
  python gen_volantis_interp.py
  python gen_volantis_interp.py --prefix dev --dataspace demo/volantis-interp
"""
from __future__ import annotations

import argparse
import json
from collections import OrderedDict
from pathlib import Path

from _shared import (
    stable_uuid,
    wpc_id,
    md_id,
    acl_block,
    legal_block,
    bearing_to_offsets,
    abcd_corners,
    save_json,
)

SCRIPT_DIR = Path(__file__).resolve().parent

# ── Scenario Parameters ────────────────────────────────────────────────
# Volantis field — Norwegian Sea, synthetic but realistic coordinates
# CRS: EPSG:23031 (ED50 / UTM zone 31N)

HORIZONS = OrderedDict([
    ("TopVolantis",  {"desc": "Top Volantis reservoir", "age_ma": 65.0, "strat_role": "Unconformity",  "pse": "Reservoir", "twt_ms": 1850, "depth_m": -2150}),
    ("BaseVolantis", {"desc": "Base Volantis reservoir", "age_ma": 70.0, "strat_role": "Conformable",  "pse": "Reservoir", "twt_ms": 2050, "depth_m": -2380}),
    ("TopTherys",    {"desc": "Top Therys source rock",  "age_ma": 155.0, "strat_role": "MFS",         "pse": "Source",    "twt_ms": 2800, "depth_m": -3100}),
])

# Seismic acquisition grid (Volantis3D survey)
SEIS_GRID = {
    "name":      "Volantis3D",
    "origin_e":  461256.0,
    "origin_n":  6782100.0,
    "il_min":    1000,
    "il_max":    1599,
    "xl_min":    2000,
    "xl_max":    2399,
    "il_spacing": 12.5,
    "xl_spacing": 12.5,
    "il_bearing": 0.0,     # I-axis = north
    "xl_bearing": 90.0,    # J-axis = east
}

# Depth grid (shared GenericBinGrid for depth-converted surfaces)
DEPTH_GRID = {
    "name":      "Volantis Depth 25m",
    "origin_e":  461000.0,
    "origin_n":  6782000.0,
    "width_i":   25.0,
    "width_j":   25.0,
    "bearing_j": 90.0,    # J-axis = east
    "count_i":   300,
    "count_j":   200,
    "transform": 9666,    # right-handed (EPSG 9666)
}

# Therys depth grid — derived from real RDDMS Grid2dRep geometry.
# The RDDMS object (DS_extract_postprocess) uses a rotated 20 m lattice
# with origin at (461500, 5926500) in projected coordinates and a J-axis
# bearing of ~150° (azimuth of the slow-axis direction vector).
THERYS_DEPTH_GRID = {
    "name":      "Therys Depth 20m",
    "origin_e":  461500.0,
    "origin_n":  5926500.0,
    "width_i":   20.0,
    "width_j":   20.0,
    "bearing_j": 150.0,   # J-axis ≈ N30W (from RDDMS offset direction)
    "count_i":   350,
    "count_j":   550,
    "transform": 9666,    # right-handed (EPSG 9666)
}

DATASPACE = "maap/drogon"
RDDMS_HOST = "rddms-1"

# CRS: EPSG 23031 / ED50 UTM31N + EPSG 5714 / MSL height
# NOTE: M27 schemas removed the top-level CrsID property.  The CRS
# is now conveyed only inside ABCDBinGridSpatialLocation ▸
# AsIngestedCoordinates.CoordinateReferenceSystemID.
CRS_REFERENCE = "dev:reference-data--CoordinateReferenceSystem:BoundCRS:EPSG::23031_EPSG::5714:"

# ── Real RDDMS Grid2dRepresentation UUIDs (maap/drogon dataspace) ──────
# These are actual RESQML objects stored in the Reservoir DDMS, exported
# from Aspen SKUA.  The OSDU catalog records point here via DDMSDatasets[].
#
# Depth surfaces (for StructureMap)
RDDMS_DEPTH = {
    "TopVolantis":  "f857c36c-3939-4ff3-9125-a11cf2af105c",  # CRS: obj_LocalDepth3dCrs
    "BaseVolantis": "0c6ab8e7-c793-4ab5-a88c-ccf457d9266d",  # CRS: obj_LocalDepth3dCrs
    "TopTherys":    "0ce9278d-979c-450a-a3db-08ea96517463",  # DS_extract_postprocess, 550×350 @ 20m
}
# TWT surfaces (for SeismicHorizon)
RDDMS_TWT = {
    "TopVolantis":  "9deb9074-c4eb-44ff-990a-229bb545d442",  # TS_interp, CRS: obj_LocalTime3dCrs
    "BaseVolantis": "efcf91f9-6e56-4bed-9e23-f0e9350a0b91",  # TS_interp, CRS: obj_LocalTime3dCrs
}
# TopTherys has no TWT counterpart in RDDMS (only depth)


def ancestry(parents: list[str] | None = None) -> dict:
    """Build data.ancestry block."""
    return {"parents": parents or []}


# ── UUID Generation ────────────────────────────────────────────────────
def uid(name: str) -> str:
    return stable_uuid(name)


# ── Record Builders ────────────────────────────────────────────────────

def make_boundary_feature(prefix: str, hz_key: str, hz: dict) -> dict:
    """LocalBoundaryFeature — the named geologic feature."""
    u = uid(f"feature:{hz_key}")
    return {
        "id": md_id(prefix, "LocalBoundaryFeature", u),
        "kind": "osdu:wks:master-data--LocalBoundaryFeature:1.1.0",
        "acl": acl_block(),
        "legal": legal_block(),
        "data": {
            "Name": hz_key.replace("Top", "Top ").replace("Base", "Base ").strip(),
            "Description": hz["desc"],
            "ancestry": ancestry(),
        },
    }


def make_horizon_interpretation(prefix: str, hz_key: str, hz: dict) -> dict:
    """HorizonInterpretation — geologic meaning of the horizon."""
    u = uid(f"interp:{hz_key}")
    feat_u = uid(f"feature:{hz_key}")
    feat_id = md_id(prefix, "LocalBoundaryFeature", feat_u)
    feat_name = hz_key.replace("Top", "Top ").replace("Base", "Base ").strip()
    return {
        "id": wpc_id(prefix, "HorizonInterpretation", u),
        "kind": "osdu:wks:work-product-component--HorizonInterpretation:1.2.0",
        "acl": acl_block(),
        "legal": legal_block(),
        "data": {
            "Name": f"{hz_key} Interpretation",
            "Description": hz["desc"],
            "FeatureID": feat_id,
            "FeatureName": feat_name,
            "DomainTypeID": f"{prefix}:reference-data--DomainType:Mixed:",
            "StratigraphicRoleTypeID": f"{prefix}:reference-data--StratigraphicRoleType:{hz['strat_role']}:",
            "MeanPossibleAge": hz["age_ma"],
            "ancestry": ancestry([feat_id]),
        },
    }


def make_seismic_bin_grid(prefix: str) -> dict:
    """SeismicBinGrid — acquisition lattice."""
    g = SEIS_GRID
    u = uid("seisbingrid:Volantis3D")
    di = bearing_to_offsets(g["il_bearing"], g["il_spacing"])
    dj = bearing_to_offsets(g["xl_bearing"], g["xl_spacing"])
    ni = g["il_max"] - g["il_min"] + 1
    nj = g["xl_max"] - g["xl_min"] + 1
    corners = abcd_corners(
        g["origin_e"], g["origin_n"],
        g["il_bearing"], g["il_spacing"], ni,
        g["xl_bearing"], g["xl_spacing"], nj,
    )
    return {
        "id": wpc_id(prefix, "SeismicBinGrid", u),
        "kind": "osdu:wks:work-product-component--SeismicBinGrid:1.3.0",
        "acl": acl_block(),
        "legal": legal_block(),
        "data": {
            "Name": g["name"],
            "Description": f"Seismic acquisition grid for Volantis3D survey, {g['il_spacing']}m × {g['xl_spacing']}m",
            "ABCDBinGridSpatialLocation": {
                "AsIngestedCoordinates": {
                    "type": "FeatureCollection",
                    "CoordinateReferenceSystemID": f"{prefix}:reference-data--CoordinateReferenceSystem:BoundCRS:EPSG::23031_EPSG::5714:",
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
            "P6BinGridOriginEasting": g["origin_e"],
            "P6BinGridOriginNorthing": g["origin_n"],
            "P6BinGridOriginI": g["il_min"],
            "P6BinGridOriginJ": g["xl_min"],
            "P6BinNodeIncrementOnIaxis": {"X": di[0], "Y": di[1]},
            "P6BinNodeIncrementOnJaxis": {"X": dj[0], "Y": dj[1]},
            "ancestry": ancestry(),
        },
    }


def make_generic_bin_grid(prefix: str) -> dict:
    """GenericBinGrid:1.0.0 — shared depth grid (M27)."""
    g = DEPTH_GRID
    u = uid("genericbingrid:VolantisDepth25m")
    # I-axis bearing: for right-handed (9666), I = J + 90°
    bearing_i = (g["bearing_j"] + 90.0) % 360
    corners = abcd_corners(
        g["origin_e"], g["origin_n"],
        bearing_i, g["width_i"], g["count_i"],
        g["bearing_j"], g["width_j"], g["count_j"],
    )
    return {
        "id": wpc_id(prefix, "GenericBinGrid", u),
        "kind": "osdu:wks:work-product-component--GenericBinGrid:1.0.0",
        "acl": acl_block(),
        "legal": legal_block(),
        "data": {
            "Name": g["name"],
            "Description": f"Shared depth grid for Volantis interpretation, {g['width_i']}m × {g['width_j']}m, {g['count_i']}×{g['count_j']} nodes",
            "BinGridName": g["name"],
            "ABCDBinGridSpatialLocation": {
                "AsIngestedCoordinates": {
                    "type": "FeatureCollection",
                    "CoordinateReferenceSystemID": f"{prefix}:reference-data--CoordinateReferenceSystem:BoundCRS:EPSG::23031_EPSG::5714:",
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
            "OriginEasting": g["origin_e"],
            "OriginNorthing": g["origin_n"],
            "BinWidthOnIaxis": g["width_i"],
            "BinWidthOnJaxis": g["width_j"],
            "MapGridBearingOfBinGridJaxis": g["bearing_j"],
            "NodeCountOnIAxis": g["count_i"],
            "NodeCountOnJAxis": g["count_j"],
            "TransformationMethod": g["transform"],
            "ancestry": ancestry(),
        },
    }


def make_generic_bin_grid_therys(prefix: str) -> dict:
    """GenericBinGrid:1.0.0 — Therys depth grid (M27).

    Separate from the Volantis 25 m grid because Therys uses a different
    lattice (20 m spacing, 550×350 nodes, rotated ~30°).  Derived from the
    real RDDMS Grid2dRepresentation geometry in maap/drogon.
    """
    g = THERYS_DEPTH_GRID
    u = uid("genericbingrid:TherysDepth20m")
    bearing_i = (g["bearing_j"] + 90.0) % 360
    corners = abcd_corners(
        g["origin_e"], g["origin_n"],
        bearing_i, g["width_i"], g["count_i"],
        g["bearing_j"], g["width_j"], g["count_j"],
    )
    return {
        "id": wpc_id(prefix, "GenericBinGrid", u),
        "kind": "osdu:wks:work-product-component--GenericBinGrid:1.0.0",
        "acl": acl_block(),
        "legal": legal_block(),
        "data": {
            "Name": g["name"],
            "Description": f"Depth grid for Therys horizon, {g['width_i']}m × {g['width_j']}m, {g['count_i']}×{g['count_j']} nodes, bearing {g['bearing_j']}°",
            "BinGridName": g["name"],
            "ABCDBinGridSpatialLocation": {
                "AsIngestedCoordinates": {
                    "type": "FeatureCollection",
                    "CoordinateReferenceSystemID": f"{prefix}:reference-data--CoordinateReferenceSystem:BoundCRS:EPSG::23031_EPSG::5714:",
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
            "OriginEasting": g["origin_e"],
            "OriginNorthing": g["origin_n"],
            "BinWidthOnIaxis": g["width_i"],
            "BinWidthOnJaxis": g["width_j"],
            "MapGridBearingOfBinGridJaxis": g["bearing_j"],
            "NodeCountOnIAxis": g["count_i"],
            "NodeCountOnJAxis": g["count_j"],
            "TransformationMethod": g["transform"],
            "ancestry": ancestry(),
        },
    }


def _ddms_uri(resqml_uuid: str) -> str:
    """Build an EML URI pointing to a Grid2dRepresentation in the RDDMS."""
    return f"eml://{RDDMS_HOST}/dataspace('{DATASPACE}')/resqml20.obj_Grid2dRepresentation('{resqml_uuid}')"


def make_seismic_horizon(prefix: str, hz_key: str, hz: dict) -> dict:
    """SeismicHorizon:2.1.0 — TWT surface pick (metadata only, geometry in RDDMS)."""
    u = uid(f"seishz:{hz_key}")
    interp_u = uid(f"interp:{hz_key}")
    feat_u = uid(f"feature:{hz_key}")
    sbg_u = uid("seisbingrid:Volantis3D")

    interp_id = wpc_id(prefix, "HorizonInterpretation", interp_u)
    feat_id = md_id(prefix, "LocalBoundaryFeature", feat_u)
    sbg_id = wpc_id(prefix, "SeismicBinGrid", sbg_u)

    g = SEIS_GRID
    ni = g["il_max"] - g["il_min"] + 1
    nj = g["xl_max"] - g["xl_min"] + 1
    n_cells = ni * nj
    n_nodes = (ni + 1) * (nj + 1)

    # DDMSDatasets → real Grid2dRep in RDDMS (TWT domain) if available
    ddms = []
    if hz_key in RDDMS_TWT:
        ddms.append(_ddms_uri(RDDMS_TWT[hz_key]))

    return {
        "id": wpc_id(prefix, "SeismicHorizon", u),
        "kind": "osdu:wks:work-product-component--SeismicHorizon:2.1.0",
        "acl": acl_block(),
        "legal": legal_block(),
        "data": {
            "Name": f"{hz_key} TWT",
            "Description": f"TWT horizon pick for {hz['desc']}",
            "InterpretationID": interp_id,
            "InterpretationName": f"{hz_key} Interpretation",
            "BinGridID": sbg_id,
            "DomainTypeID": f"{prefix}:reference-data--DomainType:Time:",
            "RepresentationType": f"{prefix}:reference-data--RepresentationType:Regular2DGrid:",
            "SeismicHorizonTypeID": f"{prefix}:reference-data--SeismicHorizonType:Peak:",
            "PetroleumSystemElementTypeID": f"{prefix}:reference-data--PetroleumSystemElementType:{hz['pse']}:",
            "Interpreter": "Volantis Interpretation Team",
            "Remark": [{"Remark": f"Picked on Volantis3D survey, nominal TWT {hz['twt_ms']} ms"}],
            "IndexableElementCount": [
                {"Count": n_cells, "IndexableElementID": f"{prefix}:reference-data--IndexableElement:Cells:"},
                {"Count": n_nodes, "IndexableElementID": f"{prefix}:reference-data--IndexableElement:Nodes:"},
            ],
            **({
                "DDMSDatasets": ddms,
            } if ddms else {}),
            "ancestry": ancestry([interp_id, sbg_id, feat_id]),
        },
    }


def make_structure_map_external(prefix: str, hz_key: str, hz: dict) -> dict:
    """StructureMap:1.0.0 using external GenericBinGrid ref (M27).

    Metadata only — actual Z-values live in the RDDMS Grid2dRepresentation.
    """
    u = uid(f"smap:{hz_key}")
    interp_u = uid(f"interp:{hz_key}")
    feat_u = uid(f"feature:{hz_key}")
    sh_u = uid(f"seishz:{hz_key}")
    gbg_u = uid("genericbingrid:VolantisDepth25m")

    interp_id = wpc_id(prefix, "HorizonInterpretation", interp_u)
    feat_id = md_id(prefix, "LocalBoundaryFeature", feat_u)
    sh_id = wpc_id(prefix, "SeismicHorizon", sh_u)
    gbg_id = wpc_id(prefix, "GenericBinGrid", gbg_u)

    g = DEPTH_GRID
    n_cells = g["count_i"] * g["count_j"]
    n_nodes = (g["count_i"] + 1) * (g["count_j"] + 1)

    # DDMSDatasets → real Grid2dRep in RDDMS (depth domain) if available
    ddms = []
    if hz_key in RDDMS_DEPTH:
        ddms.append(_ddms_uri(RDDMS_DEPTH[hz_key]))

    return {
        "id": wpc_id(prefix, "StructureMap", u),
        "kind": "osdu:wks:work-product-component--StructureMap:1.0.0",
        "acl": acl_block(),
        "legal": legal_block(),
        "data": {
            "Name": f"{hz_key} Depth Map (shared grid ref)",
            "Description": f"Depth structure map for {hz['desc']} — references the shared 25 m GenericBinGrid via BinGridID (Pattern B)",
            "InterpretationID": interp_id,
            "InterpretationName": f"{hz_key} Interpretation",
            "SeismicHorizonID": sh_id,
            "BinGridID": gbg_id,
            "DomainTypeID": f"{prefix}:reference-data--DomainType:Depth:",
            "IndexableElementCount": [
                {"Count": n_cells, "IndexableElementID": f"{prefix}:reference-data--IndexableElement:Cells:"},
                {"Count": n_nodes, "IndexableElementID": f"{prefix}:reference-data--IndexableElement:Nodes:"},
            ],
            **({
                "DDMSDatasets": ddms,
            } if ddms else {}),
            "ancestry": ancestry([sh_id, interp_id, gbg_id, feat_id]),
        },
    }


def make_structure_map_therys_external(prefix: str, hz_key: str, hz: dict) -> dict:
    """StructureMap:1.0.0 for TopTherys — Pattern B with own GenericBinGrid (M27).

    References the Therys-specific 20 m GenericBinGrid and links to the
    real RDDMS Grid2dRepresentation (DS_extract_postprocess) via DDMSDatasets[].
    """
    g = THERYS_DEPTH_GRID
    u = uid(f"smap:{hz_key}")
    interp_u = uid(f"interp:{hz_key}")
    feat_u = uid(f"feature:{hz_key}")
    sh_u = uid(f"seishz:{hz_key}")
    gbg_u = uid("genericbingrid:TherysDepth20m")

    interp_id = wpc_id(prefix, "HorizonInterpretation", interp_u)
    feat_id = md_id(prefix, "LocalBoundaryFeature", feat_u)
    sh_id = wpc_id(prefix, "SeismicHorizon", sh_u)
    gbg_id = wpc_id(prefix, "GenericBinGrid", gbg_u)

    n_cells = g["count_i"] * g["count_j"]
    n_nodes = (g["count_i"] + 1) * (g["count_j"] + 1)

    # DDMSDatasets → real Grid2dRep in RDDMS (depth domain)
    ddms = []
    if hz_key in RDDMS_DEPTH:
        ddms.append(_ddms_uri(RDDMS_DEPTH[hz_key]))

    return {
        "id": wpc_id(prefix, "StructureMap", u),
        "kind": "osdu:wks:work-product-component--StructureMap:1.0.0",
        "acl": acl_block(),
        "legal": legal_block(),
        "data": {
            "Name": f"{hz_key} Depth Map (own 20 m grid)",
            "Description": f"Depth structure map for {hz['desc']} — references own 20 m GenericBinGrid (Pattern B), real RDDMS depth data",
            "InterpretationID": interp_id,
            "InterpretationName": f"{hz_key} Interpretation",
            "SeismicHorizonID": sh_id,
            "BinGridID": gbg_id,
            "DomainTypeID": f"{prefix}:reference-data--DomainType:Depth:",
            "IndexableElementCount": [
                {"Count": n_cells, "IndexableElementID": f"{prefix}:reference-data--IndexableElement:Cells:"},
                {"Count": n_nodes, "IndexableElementID": f"{prefix}:reference-data--IndexableElement:Nodes:"},
            ],
            **({
                "DDMSDatasets": ddms,
            } if ddms else {}),
            "ancestry": ancestry([sh_id, interp_id, gbg_id, feat_id]),
        },
    }


def make_structure_map_inline_from_depth_grid(prefix: str, hz_key: str, hz: dict) -> dict:
    """StructureMap:1.0.0 with inline grid geometry — Pattern A demo.

    Uses the same DEPTH_GRID parameters but embeds them on the record
    instead of referencing GenericBinGrid.  Demonstrates the alternative
    to make_structure_map_external (Pattern B).
    Metadata only — actual Z-values live in the RDDMS Grid2dRepresentation.
    """
    g = DEPTH_GRID
    u = uid(f"smap:inlineA:{hz_key}")
    interp_u = uid(f"interp:{hz_key}")
    feat_u = uid(f"feature:{hz_key}")
    sh_u = uid(f"seishz:{hz_key}")

    interp_id = wpc_id(prefix, "HorizonInterpretation", interp_u)
    feat_id = md_id(prefix, "LocalBoundaryFeature", feat_u)
    sh_id = wpc_id(prefix, "SeismicHorizon", sh_u)

    n_cells = g["count_i"] * g["count_j"]
    n_nodes = (g["count_i"] + 1) * (g["count_j"] + 1)

    bearing_i = (g["bearing_j"] + 90.0) % 360
    corners = abcd_corners(
        g["origin_e"], g["origin_n"],
        bearing_i, g["width_i"], g["count_i"],
        g["bearing_j"], g["width_j"], g["count_j"],
    )

    return {
        "id": wpc_id(prefix, "StructureMap", u),
        "kind": "osdu:wks:work-product-component--StructureMap:1.0.0",
        "acl": acl_block(),
        "legal": legal_block(),
        "data": {
            "Name": f"{hz_key} Depth Map (inline 25 m grid)",
            "Description": f"Depth structure map for {hz['desc']} — same 25 m grid as the shared GenericBinGrid but embedded inline (Pattern A)",
            "InterpretationID": interp_id,
            "InterpretationName": f"{hz_key} Interpretation",
            "SeismicHorizonID": sh_id,
            "DomainTypeID": f"{prefix}:reference-data--DomainType:Depth:",
            # Inline grid (AbstractGenericBinGrid properties) — no BinGridID
            "BinGridName": f"{hz_key} inline depth grid",
            "ABCDBinGridSpatialLocation": {
                "AsIngestedCoordinates": {
                    "type": "FeatureCollection",
                    "CoordinateReferenceSystemID": f"{prefix}:reference-data--CoordinateReferenceSystem:BoundCRS:EPSG::23031_EPSG::5714:",
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
            "OriginEasting": g["origin_e"],
            "OriginNorthing": g["origin_n"],
            "BinWidthOnIaxis": g["width_i"],
            "BinWidthOnJaxis": g["width_j"],
            "MapGridBearingOfBinGridJaxis": g["bearing_j"],
            "NodeCountOnIAxis": g["count_i"],
            "NodeCountOnJAxis": g["count_j"],
            "TransformationMethod": g["transform"],
            "IndexableElementCount": [
                {"Count": n_cells, "IndexableElementID": f"{prefix}:reference-data--IndexableElement:Cells:"},
                {"Count": n_nodes, "IndexableElementID": f"{prefix}:reference-data--IndexableElement:Nodes:"},
            ],
            # DDMSDatasets → same real Grid2dRep as Pattern B (same underlying data)
            **({
                "DDMSDatasets": [_ddms_uri(RDDMS_DEPTH[hz_key])],
            } if hz_key in RDDMS_DEPTH else {}),
            "ancestry": ancestry([sh_id, interp_id, feat_id]),
        },
    }


def make_interpretation_project(prefix: str, horizon_keys: list) -> dict:
    """SeismicInterpretationProject:1.0.0 — groups all products (proposal)."""
    u = uid("project:Volantis2025")
    sbg_u = uid("seisbingrid:Volantis3D")

    bf_ids = [md_id(prefix, "LocalBoundaryFeature", uid(f"feature:{k}")) for k in horizon_keys]
    hi_ids = [wpc_id(prefix, "HorizonInterpretation", uid(f"interp:{k}")) for k in horizon_keys]
    sh_ids = [wpc_id(prefix, "SeismicHorizon", uid(f"seishz:{k}")) for k in horizon_keys]

    # StructureMap IDs: Pattern B (external grid) for all 3 horizons + Pattern A (inline) for Volantis pair
    sm_ids_b = [
        wpc_id(prefix, "StructureMap", uid("smap:TopVolantis")),
        wpc_id(prefix, "StructureMap", uid("smap:BaseVolantis")),
        wpc_id(prefix, "StructureMap", uid("smap:TopTherys")),
    ]
    sm_ids_a = [
        wpc_id(prefix, "StructureMap", uid("smap:inlineA:TopVolantis")),
        wpc_id(prefix, "StructureMap", uid("smap:inlineA:BaseVolantis")),
    ]
    sm_ids = sm_ids_b + sm_ids_a
    gbg_id = wpc_id(prefix, "GenericBinGrid", uid("genericbingrid:VolantisDepth25m"))
    gbg_therys_id = wpc_id(prefix, "GenericBinGrid", uid("genericbingrid:TherysDepth20m"))
    sbg_id = wpc_id(prefix, "SeismicBinGrid", sbg_u)

    all_parents = bf_ids + hi_ids + sh_ids + sm_ids + [gbg_id, gbg_therys_id, sbg_id]

    return {
        "id": wpc_id(prefix, "SeismicInterpretationProject", u),
        "kind": "dev:wks:work-product-component--SeismicInterpretationProject:1.0.0",
        "acl": acl_block(),
        "legal": legal_block(),
        "data": {
            "Name": "Volantis 2025 Interpretation",
            "Description": "Complete horizon interpretation for the Volantis field — 3 horizons (TWT + depth), seismic + depth grids",
            "HorizonInterpretationIDs": hi_ids,
            "SeismicHorizonIDs": sh_ids,
            "StructureMapIDs": sm_ids,
            "SeismicBinGridID": sbg_id,
            "GenericBinGridID": gbg_id,
            "InterpreterName": "Volantis Interpretation Team",
            "InterpretationDate": "2025-09-15T00:00:00Z",
            "SoftwareUsed": "Petrel 2024.1",
            "ResqmlDataspaceID": f"{prefix}:dataset--ETPDataspace:{uid('dataspace:volantis')}:1",
            "ancestry": ancestry(all_parents),
        },
    }


# ── Main ────────────────────────────────────────────────────────────────

def generate(prefix: str = "dev", dataspace: str | None = None) -> None:
    global DATASPACE
    if dataspace:
        DATASPACE = dataspace

    records: list[dict] = []
    hz_keys = list(HORIZONS.keys())

    # 1. Master data: LocalBoundaryFeature
    print("Generating master data...")
    for k, v in HORIZONS.items():
        records.append(make_boundary_feature(prefix, k, v))

    # 2. HorizonInterpretation
    print("Generating horizon interpretations...")
    for k, v in HORIZONS.items():
        records.append(make_horizon_interpretation(prefix, k, v))

    # 3. SeismicBinGrid
    print("Generating seismic bin grid...")
    records.append(make_seismic_bin_grid(prefix))

    # 4. GenericBinGrid:1.0.0 (M27) — before SeismicHorizon so grids exist first
    print("Generating generic bin grids (depth)...")
    records.append(make_generic_bin_grid(prefix))
    records.append(make_generic_bin_grid_therys(prefix))

    # 5. SeismicHorizon:2.1.0
    print("Generating seismic horizons (TWT)...")
    for k, v in HORIZONS.items():
        records.append(make_seismic_horizon(prefix, k, v))

    # 6. StructureMap:1.0.0 (M27) — Pattern B: external GenericBinGrid ref
    print("Generating structure maps — Pattern B (external BinGridID)...")
    for k in ["TopVolantis", "BaseVolantis"]:
        records.append(make_structure_map_external(prefix, k, HORIZONS[k]))
    # TopTherys — own 20 m GenericBinGrid + real RDDMS depth data
    records.append(make_structure_map_therys_external(prefix, "TopTherys", HORIZONS["TopTherys"]))

    # 7. StructureMap:1.0.0 (M27) — Pattern A: inline grid geometry
    print("Generating structure maps — Pattern A (inline grid)...")
    for k in ["TopVolantis", "BaseVolantis"]:
        records.append(make_structure_map_inline_from_depth_grid(prefix, k, HORIZONS[k]))

    # 8. SeismicInterpretationProject (proposal)
    print("Generating interpretation project...")
    records.append(make_interpretation_project(prefix, hz_keys))

    # Build manifest
    manifest = {
        "kind": "osdu:wks:Manifest:1.0.0",
        "ReferenceData": [],
        "MasterData": [r for r in records if ":master-data--" in r["id"]],
        "Data": {
            "WorkProductComponents": [r for r in records if ":work-product-component--" in r["id"]],
        },
    }

    outpath = SCRIPT_DIR / "manifest_volantis_interp.json"
    save_json(manifest, outpath)

    # Summary
    md_count = len(manifest["MasterData"])
    wpc_count = len(manifest["Data"]["WorkProductComponents"])
    print(f"\n  Summary: {md_count} MasterData + {wpc_count} WPCs = {md_count + wpc_count} records total")
    print("  Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate Volantis interpretation manifest")
    parser.add_argument("--prefix", default="dev", help="OSDU namespace prefix (default: dev)")
    parser.add_argument("--dataspace", default=None, help="RDDMS dataspace name")
    args = parser.parse_args()
    generate(prefix=args.prefix, dataspace=args.dataspace)
