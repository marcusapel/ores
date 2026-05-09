#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_markers_strat_drogon.py  –  Generate WellboreMarkerSet WPCs and a
lithostratigraphic StratigraphicColumn (with RankInterpretation + UnitInterpretation)
for the Drogon and Volve wells.

Creates:
  - WellboreMarkerSet per wellbore (formation tops with MD/TVD)
  - StratigraphicColumn  (Drogon-Volve lithostratigraphy)
  - StratigraphicColumnRankInterpretation  (Formation / Group / Member ranks)
  - StratigraphicUnitInterpretation  (per formation unit)

Output:
  manifest_markers_drogon.json     – WellboreMarkerSet records
  manifest_litho_strat_drogon.json – StratColumn + Rank + Unit records

Usage:
    python demo/drogon/gen_markers_strat_drogon.py
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent

# ── OSDU envelope defaults (same as gen_wells_drogon.py) ────────────────
ID_PREFIX   = "dev"
OWNERS      = ["data.default.owners@dev.dataservices.energy"]
VIEWERS     = ["data.office.global.viewers@dev.dataservices.energy"]
LEGAL_TAGS  = ["dev-equinor-private-default"]
COUNTRY     = ["NO"]

# Schema kinds
MARKERSET_KIND  = "osdu:wks:work-product-component--WellboreMarkerSet:1.2.0"
STRATCOL_KIND   = "osdu:wks:work-product-component--StratigraphicColumn:1.2.0"
RANK_KIND       = "osdu:wks:work-product-component--StratigraphicColumnRankInterpretation:1.3.0"
UNIT_KIND       = "osdu:wks:work-product-component--StratigraphicUnitInterpretation:1.3.0"


def _uid() -> str:
    return str(uuid.uuid4())


def _acl() -> Dict[str, Any]:
    return {"owners": OWNERS, "viewers": VIEWERS}


def _legal() -> Dict[str, Any]:
    return {"legaltags": LEGAL_TAGS, "otherRelevantDataCountries": COUNTRY}


# ═════════════════════════════════════════════════════════════════════════
#  Wellbore IDs from the generated wells manifest
#  (must match manifest_wells_drogon.json)
# ═════════════════════════════════════════════════════════════════════════
def _load_wellbore_ids() -> Dict[str, str]:
    """Load wellbore name → ID mapping from the wells manifest."""
    manifest_path = SCRIPT_DIR / "manifest_wells_drogon.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Run gen_wells_drogon.py first: {manifest_path}")
    with open(manifest_path) as f:
        manifest = json.load(f)
    mapping: Dict[str, str] = {}
    for rec in manifest.get("MasterData", []):
        if "Wellbore:" in rec.get("kind", ""):
            name = rec["data"].get("Name", rec["data"].get("FacilityName", ""))
            mapping[name] = rec["id"]
    return mapping


# ═════════════════════════════════════════════════════════════════════════
#  Formation tops per wellbore
#  (synthetic but geologically plausible depths)
# ═════════════════════════════════════════════════════════════════════════

# Drogon formations (Valysar zone, North-Sea-like Jurassic play)
DROGON_FORMATIONS = [
    # (MarkerName, typical MD range min, max, TVD offset from MD)
    ("Seabed",           400,  450,  0),
    ("TopNordland",     1200, 1350, -5),
    ("TopShetland",     1800, 1950, -10),
    ("TopTherys",       2650, 2800, -15),
    ("TopVolon",        2850, 3000, -18),
    ("TopVolantis",     3050, 3200, -20),
    ("BaseVolantis",    3250, 3400, -22),
    ("BaseVolon",       3350, 3500, -24),
    ("TopPre-Jurassic", 3600, 3800, -28),
]

# Volve formations (Hugin Fm, North Sea – 15/9 area)
VOLVE_FORMATIONS = [
    ("Seabed",          100,  120,   0),
    ("TopNordland",     900, 1000,  -3),
    ("TopShetland",    2400, 2550,  -8),
    ("TopDraupne",     2800, 2900, -12),
    ("TopHeather",     2950, 3050, -14),
    ("TopHugin",       3100, 3200, -16),
    ("BaseHugin",      3250, 3350, -18),
    ("TopSleipner",    3350, 3450, -20),
    ("TopSkagerrak",   3500, 3600, -22),
    ("TopBasement",    3700, 3850, -25),
]

# Per-wellbore depth offsets (to make each well unique)
WELLBORE_DEPTH_SEEDS = {
    # Drogon – deeper target = deeper structure
    "55/33-A-1":     0,
    "55/33-A-2":    40,
    "55/33-A-2 T2": 80,
    "55/33-A-3":   -20,
    # Volve – slight depth variations per slot
    "15/9-F-1 C":    0,
    "15/9-F-4":     15,
    "15/9-F-5":    -10,
    "15/9-F-7":     25,
    "15/9-F-11 A":  35,
    "15/9-F-15 D":  50,
    "15/9-F-15 S":  65,
}


def _markers_for_wellbore(
    wb_name: str,
    formations: List[tuple],
    depth_seed: int,
) -> List[Dict[str, Any]]:
    """Generate a Markers array for a wellbore."""
    import hashlib
    markers: List[Dict[str, Any]] = []
    for i, (name, md_min, md_max, tvd_offset) in enumerate(formations):
        # Deterministic pseudo-random depth based on wellbore name + formation
        h = int(hashlib.md5(f"{wb_name}-{name}".encode()).hexdigest()[:8], 16)
        frac = (h % 1000) / 1000.0
        md = round(md_min + depth_seed + frac * (md_max - md_min), 1)
        tvd = round(md + tvd_offset, 1)
        markers.append({
            "MarkerName": name,
            "MarkerMeasuredDepth": md,
            "MarkerSubSeaVerticalDepth": tvd,
            "MarkerObservationNumber": i + 1,
            "Missing": "",
            "MarkerTypeID": "",
            "InterpretationID": "",
            "MarkerInterpreter": "gen_markers_strat_drogon.py",
            "GeologicalAge": "",
        })
    return markers


def _markerset_record(
    wb_name: str,
    wb_id: str,
    markers: List[Dict[str, Any]],
    strat_col_id: str,
) -> Dict[str, Any]:
    uid = _uid()
    rec_id = f"{ID_PREFIX}:work-product-component--WellboreMarkerSet:{uid}:"
    return {
        "id": rec_id,
        "kind": MARKERSET_KIND,
        "acl": _acl(),
        "legal": _legal(),
        "data": {
            "Name": f"{wb_name} – Formation Tops",
            "Description": f"Formation top picks for wellbore {wb_name}",
            "WellboreID": wb_id,
            "Markers": markers,
            "StratigraphicColumnID": strat_col_id,
            "StratigraphicColumnRankInterpretationID": "",
        },
    }


# ═════════════════════════════════════════════════════════════════════════
#  Lithostratigraphic Column  (Drogon + Volve combined)
# ═════════════════════════════════════════════════════════════════════════

# Unit definitions: (unit_name, rank, older_age_Ma, younger_age_Ma, description)
LITHO_UNITS = [
    # Group-level units
    ("Nordland Group",       "Group",     23.0,    0.0,  "Neogene–Quaternary overburden, North Sea"),
    ("Shetland Group",       "Group",     66.0,   23.0,  "Palaeocene–Eocene chalk and marl"),
    # Drogon formations
    ("Therys Formation",     "Formation", 170.0, 163.0,  "Source rock unit – organic-rich shale, Drogon area"),
    ("Volon Formation",      "Formation", 163.0, 157.0,  "Lower reservoir unit, Drogon field (Valysar zone)"),
    ("Volantis Formation",   "Formation", 157.0, 145.0,  "Main reservoir unit, Drogon field (Valysar zone)"),
    # Volve formations
    ("Draupne Formation",    "Formation", 155.0, 145.0,  "Late Jurassic source/cap rock, Viking Graben"),
    ("Heather Formation",    "Formation", 164.0, 155.0,  "Marine shale, mid-Jurassic, Viking Graben"),
    ("Hugin Formation",      "Formation", 170.0, 164.0,  "Reservoir sandstone, Volve field – Bajocian–Bathonian"),
    ("Sleipner Formation",   "Formation", 175.0, 170.0,  "Lower Jurassic marine limestone/sandstone"),
    ("Skagerrak Formation",  "Formation", 250.0, 200.0,  "Triassic red beds and evaporites"),
    # Member-level (Drogon sub-units of Volantis Fm)
    ("Upper Volantis Member",    "Member",  157.0, 152.0, "Upper reservoir sand, Drogon – good porosity"),
    ("Lower Volantis Member",    "Member",  152.0, 145.0, "Lower reservoir sand, Drogon – cemented intervals"),
]


def _generate_strat_column() -> tuple:
    """Generate StratColumn + Rank + Unit records.

    Returns (strat_col_id, list_of_records).
    """
    records: List[Dict[str, Any]] = []

    # ── Unit records ────────────────────────────────────────────────
    unit_ids: Dict[str, str] = {}
    rank_units: Dict[str, List[str]] = {"Group": [], "Formation": [], "Member": []}

    for unit_name, rank, older_age, younger_age, desc in LITHO_UNITS:
        uid = _uid()
        unit_id = f"{ID_PREFIX}:work-product-component--StratigraphicUnitInterpretation:Drogon-Volve-{unit_name.replace(' ', '')}:"
        unit_ids[unit_name] = unit_id
        rank_units[rank].append(unit_id)

        records.append({
            "id": unit_id,
            "kind": UNIT_KIND,
            "acl": _acl(),
            "legal": _legal(),
            "data": {
                "Name": unit_name,
                "Description": desc,
                "StratigraphicRoleTypeID": "",
                "ChronoStratigraphyID": "",
                "OlderPossibleAge": older_age,
                "YoungerPossibleAge": younger_age,
                "ColumnStratigraphicHorizonTopID": "",
                "ColumnStratigraphicHorizonBaseID": "",
            },
        })

    # ── Rank records ────────────────────────────────────────────────
    rank_ids: Dict[str, str] = {}
    for rank_name in ["Group", "Formation", "Member"]:
        rank_id = f"{ID_PREFIX}:work-product-component--StratigraphicColumnRankInterpretation:Drogon-Volve-Litho-{rank_name}:"
        rank_ids[rank_name] = rank_id

        unit_set = []
        for u_id in rank_units[rank_name]:
            unit_set.append({"StratigraphicUnitInterpretationID": u_id})

        records.append({
            "id": rank_id,
            "kind": RANK_KIND,
            "acl": _acl(),
            "legal": _legal(),
            "data": {
                "Name": f"Drogon-Volve Lithostratigraphy – {rank_name}",
                "RankName": rank_name,
                "StratigraphicRoleType": "",
                "StratigraphicUnitInterpretationSet": unit_set,
            },
        })

    # ── Column record ───────────────────────────────────────────────
    strat_col_id = f"{ID_PREFIX}:work-product-component--StratigraphicColumn:Drogon-Volve-Lithostratigraphy:"

    rank_set = []
    for rank_name in ["Group", "Formation", "Member"]:
        rank_set.append({
            "StratigraphicColumnRankInterpretationID": rank_ids[rank_name],
        })

    records.append({
        "id": strat_col_id,
        "kind": STRATCOL_KIND,
        "acl": _acl(),
        "legal": _legal(),
        "data": {
            "Name": "Drogon-Volve Lithostratigraphy",
            "Description": "Lithostratigraphic column for the Drogon (Valysar zone) and Volve (Hugin Fm) fields. Covers North Sea Jurassic–Cretaceous stratigraphy from Seabed through Nordland, Shetland, Draupne/Heather, Hugin, Therys, Volon, Volantis, Sleipner and Skagerrak formations.",
            "StratigraphicColumnRankInterpretationSet": rank_set,
            "StratigraphicColumnValidityAreaType": "",
            "ValueChainStatusType": "",
        },
    })

    return strat_col_id, records


# ═════════════════════════════════════════════════════════════════════════
#  Main
# ═════════════════════════════════════════════════════════════════════════
def generate() -> None:
    wb_ids = _load_wellbore_ids()

    # ── 1. Generate StratColumn ─────────────────────────────────────
    strat_col_id, strat_records = _generate_strat_column()
    strat_manifest = {
        "kind": "osdu:wks:Manifest:1.0.0",
        "ReferenceData": [],
        "MasterData": [],
        "Data": {
            "WorkProduct": None,
            "WorkProductComponents": strat_records,
        },
    }
    strat_out = SCRIPT_DIR / "manifest_litho_strat_drogon.json"
    with open(strat_out, "w", encoding="utf-8") as f:
        json.dump(strat_manifest, f, indent=2)

    n_units = sum(1 for r in strat_records if "UnitInterpretation" in r["kind"])
    n_ranks = sum(1 for r in strat_records if "RankInterpretation" in r["kind"])
    print(f"Generated {strat_out.name}:")
    print(f"  1 StratigraphicColumn")
    print(f"  {n_ranks} RankInterpretations (Group, Formation, Member)")
    print(f"  {n_units} UnitInterpretations")
    for r in strat_records:
        if "UnitInterpretation" in r["kind"]:
            d = r["data"]
            print(f"    {d['Name']:30s}  {d['OlderPossibleAge']:6.1f}–{d['YoungerPossibleAge']:6.1f} Ma")

    # ── 2. Generate WellboreMarkerSets ──────────────────────────────
    marker_records: List[Dict[str, Any]] = []

    for wb_name, wb_id in sorted(wb_ids.items()):
        # Determine which formation set to use
        if wb_name.startswith("55/33"):
            formations = DROGON_FORMATIONS
        elif wb_name.startswith("15/9"):
            formations = VOLVE_FORMATIONS
        else:
            continue

        depth_seed = WELLBORE_DEPTH_SEEDS.get(wb_name, 0)
        markers = _markers_for_wellbore(wb_name, formations, depth_seed)
        rec = _markerset_record(wb_name, wb_id, markers, strat_col_id)
        marker_records.append(rec)

    marker_manifest = {
        "kind": "osdu:wks:Manifest:1.0.0",
        "ReferenceData": [],
        "MasterData": [],
        "Data": {
            "WorkProduct": None,
            "WorkProductComponents": marker_records,
        },
    }
    markers_out = SCRIPT_DIR / "manifest_markers_drogon.json"
    with open(markers_out, "w", encoding="utf-8") as f:
        json.dump(marker_manifest, f, indent=2)

    print(f"\nGenerated {markers_out.name}:")
    print(f"  {len(marker_records)} WellboreMarkerSet records:")
    for r in marker_records:
        d = r["data"]
        n_markers = len(d["Markers"])
        print(f"    {d['Name']:40s}  {n_markers} markers")


if __name__ == "__main__":
    generate()
