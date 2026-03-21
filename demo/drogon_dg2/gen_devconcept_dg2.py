#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_devconcept_dg2.py — Generate a DevelopmentConcept WPC manifest for
Drogon DG2, aligned with the real Drogon FMU model (equinor/fmu-drogon,
tutorial 24.3.1).

The concept is derived from the actual model structure:
  - Wells: 4 producers (A1-A4), 2 water injectors (A5-A6), 1 appraisal (55_33-1)
  - Formations: Volantis Group (Valysar, Therys, Volon)
  - 7 fault-bounded regions (FIPNUM 1-7)
  - ERT workflow: DESIGN2PARAMS → RMS (geomodel) → OPM Flow (simulation)
  - 250 realisations, one-by-one sensitivity design
  - Seismic conditioning (APS) for facies modelling

Output:
  manifest_devconcept_dg2.json

Usage:
  py demo/drogon_dg2/gen_devconcept_dg2.py
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

SCRIPT_DIR = Path(__file__).resolve().parent
DG1_DIR    = SCRIPT_DIR.parent / "drogon"

if str(DG1_DIR) not in sys.path:
    sys.path.insert(0, str(DG1_DIR))

from _shared import load_json  # noqa: E402

DEFAULT_ACL = {
    "owners":  ["data.default.owners@dev.dataservices.energy"],
    "viewers": ["data.office.global.viewers@dev.dataservices.energy"],
}
DEFAULT_LEGAL = {
    "legaltags": ["dev-equinor-private-default"],
    "otherRelevantDataCountries": ["NO"],
}


def _find_id(manifest: Dict, kind_fragment: str) -> str:
    for md in manifest.get("MasterData", []):
        if kind_fragment in md.get("kind", ""):
            return md["id"]
    for wpc in manifest.get("Data", {}).get("WorkProductComponents", []):
        if kind_fragment in wpc.get("kind", ""):
            return wpc["id"]
    return ""


def main():
    ap = argparse.ArgumentParser(description="Generate Drogon DG2 DevelopmentConcept WPC manifest")
    ap.add_argument("--masterwp", default=str(DG1_DIR / "manifest_masterwp_drogon.json"))
    ap.add_argument("--manifest", default=str(SCRIPT_DIR / "manifest_devconcept_dg2.json"))
    ap.add_argument("--id-prefix", default="dev")
    args = ap.parse_args()

    pfx = args.id_prefix

    masterwp = load_json(args.masterwp)
    reservoir_id = _find_id(masterwp, "master-data--Reservoir:")

    wpc_id = f"{pfx}:work-product-component--DevelopmentConcept:Drogon-DG2:1"

    wpc_record = {
        "id":    wpc_id,
        "kind":  f"{pfx}:wks:work-product-component--DevelopmentConcept:1.0.0",
        "acl":   DEFAULT_ACL,
        "legal": DEFAULT_LEGAL,
        "data": {
            "Name": "Drogon DG2 \u2014 Development Concept (FMU-aligned)",
            "Description": (
                "Development concept for Drogon DG2 Concept Select, aligned with the "
                "official Drogon FMU model (equinor/fmu-drogon tutorial 24.3.1). "
                "Subsea tie-back to FPSO with 4 producers (A1\u2013A4), 2 water injectors "
                "(A5\u2013A6), targeting the Volantis Group (Valysar, Therys, Volon formations) "
                "across 7 fault-bounded reservoir regions. 250 FMU realisations with "
                "one-by-one sensitivity design and OPM Flow dynamic simulation."
            ),
            "ParentObjectID": reservoir_id,
            "ancestry": {
                "parents": [reservoir_id] if reservoir_id else [],
                "children": [],
            },

            # ── DevelopmentConcept fields ──
            "Summary": (
                "Subsea development targeting the Volantis Group (Valysar, Therys, Volon) "
                "across 7 fault-bounded regions at ~1650\u20131690 m TVD MSL. "
                "4 producers (A1\u2013A4) and 2 water injectors (A5\u2013A6) tied back to FPSO "
                "via dual 10\" production flowlines and 6\" gas-lift line. Distance to "
                "FPSO ~8 km. Water depth 108 m. Gas cap in NorthHorst region (GOC ~1640 m). "
                "APS facies model with seismic conditioning drives reservoir property "
                "assignment. FMU workflow: ERT \u2192 RMS (geomodel + Eclipse grid) \u2192 OPM Flow."
            ),
            "WellCount": 6,
            "ContingentWells": 2,
            "TemplateSlots": 10,
            "DrillingCentres": 2,
            "ReservoirFormation": "Volantis Group (Valysar, Therys, Volon)",
            "FieldArea": "Drogon",
            "WaterDepth_m": 108,
            "DistanceToHost_km": 8,
            "HostFacility": "Drogon FPSO (converted)",
            "TargetStartUp": "2028-H1",
            "FlowlineSpec": "2\u00d710\" production + 6\" gas lift",
            "SubseaBoostingPump": True,
            "WaterTreatmentCapacity_m3d": 5000,
            "InjectionStrategy": (
                "Water injection for pressure support via A5 and A6. "
                "Rate scaling controlled by fmuconfig rate_scaling.yml. "
                "Phase 2: 2\u20134 additional infill wells depending on "
                "fault compartmentalisation (contingent slots in template)."
            ),
            "WellPlan": {
                "Producers":  4,
                "Injectors":  2,
                "ContingentInfill": 2,
                "AppraisalWells": ["55_33-1"],
                "RFT_Wells": ["R_A2", "R_A3", "R_A4", "R_A5", "R_A6"],
                "AvgWellDepth_mMD": 3200,
                "DrillingDuration_days_avg": 45,
                "CompletionType": "Frac-pack + ICD lower completion",
                "ProducerNames": ["A1", "A2", "A3", "A4"],
                "InjectorNames": ["A5", "A6"],
            },
            "ext": {
                "equinor": {
                    "Stratigraphy": {
                        "MSL": {"stratigraphic": False},
                        "TopVolantis": {"stratigraphic": True, "name": "VOLANTIS GP. Top"},
                        "TopTherys":   {"stratigraphic": True, "name": "Therys Fm. Top"},
                        "TopVolon":    {"stratigraphic": True, "name": "Volon Fm. Top"},
                        "BaseVolantis": {"stratigraphic": True, "name": "VOLANTIS GP. Base"},
                    },
                    "Zones": ["Valysar", "Therys", "Volon"],
                    "Regions": {
                        "WestLowland":  {"FIPNUM": 1, "OWC": 1660.0},
                        "CentralSouth": {"FIPNUM": 2, "OWC": 1677.0},
                        "CentralNorth": {"FIPNUM": 3, "OWC": 1677.0},
                        "NorthHorst":   {"FIPNUM": 4, "OWC": 1660.0, "GOC": 1640.0},
                        "CentralRamp":  {"FIPNUM": 5, "OWC": 1677.0},
                        "CentralHorst": {"FIPNUM": 6, "OWC": 1677.0},
                        "EastLowland":  {"FIPNUM": 7, "OWC": 1660.0},
                    },
                    "FmuModel": {
                        "Name": "Drogon (equinor/fmu-drogon)",
                        "Version": "24.3.1",
                        "ErtConfig": "drogon_design.ert",
                        "RmsProject": "drogon.rms14.2.1",
                        "RmsWorkflow": "MAIN",
                        "Simulator": "OPM_FLOW",
                        "NumRealizations": 250,
                        "DesignType": "one-by-one sensitivity",
                        "DesignMatrix": "design_matrix_one_by_one.xlsx",
                    },
                    "ModelSwitches": {
                        "DCONV_ALTERNATIVE": 2,
                        "PETROMODEL_ALTERNATIVE": 1,
                        "FACIESMODEL_ALTERNATIVE": 1,
                        "FACIES_VALYSAR_SEISCOND": 1,
                    },
                    "Facies": {
                        "Valysar": ["Channel", "Crevasse", "Floodplain"],
                        "Therys":  ["Upper shoreface", "Lower shoreface", "Offshore"],
                        "Volon":   ["Channel", "Floodplain"],
                    },
                    "SeismicInput": {
                        "Cubes": [
                            "seismic--amplitude_near_time--20180101.segy",
                            "seismic--amplitude_far_time--20180101.segy",
                            "seismic--relai_near_time--20180101.segy",
                            "seismic--relai_far_time--20180101.segy",
                        ],
                        "Conditioning": "APS facies model with near+far angle stacks",
                    },
                    "HorizonInput": {
                        "Depth": ["TopVolantis.poi", "TopTherys.poi", "TopVolon.poi", "BaseVolantis.poi"],
                        "Time":  ["TopVolantis.poi", "BaseVolantis.poi"],
                    },
                    "FaultInput": {
                        "DepthPolygons": ["F1.pol", "F2.pol", "F3.pol", "F4.pol", "F5.pol", "F6.pol"],
                        "F2_PointSet": "F2.poi",
                    },
                },
            },
        },
    }

    manifest = {
        "kind": "osdu:wks:Manifest:1.0.0",
        "ReferenceData": [],
        "MasterData": [],
        "Data": {
            "Datasets": [],
            "WorkProductComponents": [wpc_record],
            "WorkProducts": [],
        },
    }

    out = Path(args.manifest)
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"DevelopmentConcept WPC manifest written \u2192 {out}")
    print(f"  WPC ID      : {wpc_id}")
    print(f"  Reservoir   : {reservoir_id}")


if __name__ == "__main__":
    main()
