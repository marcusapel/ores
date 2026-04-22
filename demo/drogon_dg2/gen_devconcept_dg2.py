#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_devconcept_dg2.py - Generate a DevelopmentConcept v2 WPC manifest for
Drogon DG2 (Concept Select), aligned with the real Drogon FMU model
(equinor/fmu-drogon, tutorial 24.3.1).

Schema v2 is a pure leaf WPC describing the *physical development concept*
only - what is built, how we drill, how we drain, where we target, how we
manage production.  It is referenced by the BusinessDecision (the hub) via
Parameters[], so economics, production forecast, schedule, risks, documents
and activity links all live on the BD, NOT here.

Structured sub-objects:
  FacilityConcept      - subsea layout, flowlines, host, capacities
  WellPlan             - counts, types, completion, pilot strategy
  DrainageStrategy     - injection, IOR, phased development
  ReservoirTarget      - zones, segments, faults (what we target)
  ProductionTechnology - sand, scale, metering, automation

ConceptID provides version lineage to a prior gate's concept.

Output:
  manifest_devconcept_dg2.json

Usage:
  py demo/drogon_dg2/gen_devconcept_dg2.py
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

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
    ap = argparse.ArgumentParser(description="Generate Drogon DG2 DevelopmentConcept v2 WPC manifest")
    ap.add_argument("--masterwp",    default=str(DG1_DIR / "manifest_masterwp_drogon.json"))
    ap.add_argument("--manifest",    default=str(SCRIPT_DIR / "manifest_devconcept_dg2.json"))
    ap.add_argument("--id-prefix",   default="dev")
    args = ap.parse_args()

    pfx = args.id_prefix

    masterwp = load_json(args.masterwp)
    reservoir_id = _find_id(masterwp, "master-data--Reservoir:")

    # Resolve ReservoirSegment IDs from master data
    segment_ids = []
    for md in masterwp.get("MasterData", []):
        if "master-data--ReservoirSegment:" in md.get("kind", ""):
            segment_ids.append(md["id"])

    # No DG1 concept - DG1 typically has no DevelopmentConcept record

    wpc_id = f"{pfx}:work-product-component--DevelopmentConcept:Drogon-DG2:1"

    wpc_record = {
        "id":    wpc_id,
        "kind":  f"{pfx}:wks:work-product-component--DevelopmentConcept:3.0.0",
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
            "Summary": (
                "Subsea development targeting the Volantis Group (Valysar, Therys, Volon) "
                "across 7 fault-bounded regions at ~1650\u20131690 m TVD MSL. "
                "4 producers (A1\u2013A4) and 2 water injectors (A5\u2013A6) tied back to FPSO "
                "via dual 10\" production flowlines and 6\" gas-lift line. Distance to "
                "FPSO ~8 km. Water depth 108 m. Gas cap in NorthHorst region (GOC ~1640 m). "
                "APS facies model with seismic conditioning drives reservoir property "
                "assignment. FMU workflow: ERT \u2192 RMS (geomodel + Eclipse grid) \u2192 OPM Flow."
            ),
            "DecisionGate": "DG2",

            # ── FacilityConcept - what is being built ──
            "FacilityConcept": {
                "FacilityType": "SubseaTieback",
                "HostFacility": "Drogon FPSO (converted)",
                "HostModifications": (
                    "Brownfield scope: inlet arrangement, start-up heater, "
                    "debottlenecking of LP gas capacity, upgrade of produced "
                    "water system (capacity and efficiency)."
                ),
                "TemplateCount": 2,
                "SlotsPerTemplate": 4,
                "TotalSlots": 10,   # 8 active + 2 contingent
                "Flowlines": [
                    {"Type": "Production", "Diameter_in": 10, "Length_km": 8, "Count": 2},
                    {"Type": "GasLift",    "Diameter_in": 6,  "Length_km": 8, "Count": 1},
                    {"Type": "Umbilical",  "Length_km": 8, "Count": 1},
                ],
                "SubseaBoostingPump": True,
                "ArtificialLift": "GasLift",
                "ProcessingCapacity": {
                    "OilRate_Sm3d": 5500,
                    "WaterTreatment_m3d": 5000,
                    "GasLiftCapacity_MSm3d": 1.5,
                },
                "WaterDepth_m": 108,
                "DistanceToHost_km": 8,
                "Provisions": (
                    "2 contingent well slots in templates for infill. "
                    "Flexibility for future tie-in of additional templates. "
                    "Provision for multiphase pump if HP tie-back needed."
                ),
            },

            # ── WellPlan - how we drill ──
            "WellPlan": {
                "Producers":  4,
                "Injectors":  2,
                "ContingentWells": 2,
                "TotalTargets": 8,
                "MultilateralWells": 0,
                "WellTypes": [
                    {
                        "Type": "HorizontalProducer",
                        "Count": 4,
                        "Names": ["A1", "A2", "A3", "A4"],
                        "TargetZone": "Valysar",
                        "AvgLength_mMD": 3200,
                    },
                    {
                        "Type": "WaterInjector",
                        "Count": 2,
                        "Names": ["A5", "A6"],
                        "TargetZone": "Valysar",
                        "AvgLength_mMD": 3000,
                    },
                    {
                        "Type": "Appraisal",
                        "Count": 1,
                        "Names": ["55_33-1"],
                        "TargetZone": "NorthHorst",
                    },
                ],
                "AvgWellDepth_mMD": 3200,
                "DrillingDuration_days": 45,
                "CompletionType": "Frac-pack + ICD lower completion",
                "SandControl": "Frac-pack with gravel pack screens",
                "InflowControl": "ICD (passive)",
                "PilotStrategy": (
                    "Pre-drilling of landing pilots (12\u00bc\" hole) for all "
                    "producers to map top reservoir. Reservoir pilot (8\u00bd\" hole) "
                    "for NorthHorst appraisal well."
                ),
            },

            # ── DrainageStrategy - how we produce ──
            "DrainageStrategy": {
                "PrimaryRecoveryMechanism": "WaterInjection",
                "InjectionType": "Water",
                "InjectionStrategy": (
                    "Water injection for pressure support via A5 and A6. "
                    "Rate scaling controlled by fmuconfig rate_scaling.yml. "
                    "Phase 2: 2\u20134 additional infill wells depending on "
                    "fault compartmentalisation (contingent slots in template)."
                ),
                "IORStrategy": (
                    "Base case: water injection. Options under evaluation: "
                    "low-salinity water injection, polymer flooding (pending "
                    "lab screening results). Real options provisioned in "
                    "template design."
                ),
                "DevelopmentPhases": [
                    {
                        "Phase": "Phase 1",
                        "Description": "4 producers (A1\u2013A4) + 2 injectors (A5\u2013A6), primary depletion + water injection",
                        "Wells": 6,
                        "StartDate": "2028-H1",
                    },
                    {
                        "Phase": "Phase 2",
                        "Description": "Contingent infill wells targeting isolated fault compartments",
                        "Wells": 2,
                        "StartDate": "2030 (conditional)",
                    },
                ],
                "AquiferSupport": (
                    "Active bottom-water aquifer confirmed by DST in CentralSouth. "
                    "Aquifer influx modelled with Fetkovich analytical model. "
                    "Expected to provide partial pressure support in southern regions."
                ),
            },

            # ── ReservoirTarget - what we target (not reservoir state) ──
            "ReservoirTarget": {
                "FormationName": "Heimdal Formation (Drogon analogue: Valysar)",
                "GroupName": "Volantis Group (Valysar, Therys, Volon)",
                "Age": "Palaeocene",
                "FieldArea": "Drogon",
                "DepthRange_mTVDMSL": {"Min": 1650, "Max": 1690},
                "Zones": ["Valysar", "Therys", "Volon"],
                "ReservoirSegmentIDs": segment_ids,
            },

            # ── ProductionTechnology ──
            "ProductionTechnology": {
                "SandManagement": (
                    "Frac-pack with gravel pack screens. Standalone screens "
                    "as fallback for clean Valysar sand intervals."
                ),
                "ScaleRisk": (
                    "BaSO4 scale risk identified from formation water analysis. "
                    "Scale squeeze programme planned from year 3."
                ),
                "EmulsionRisk": "Low - clean oil, low asphaltene content.",
                "CorrosionStrategy": "Corrosion-resistant alloy (CRA) in well tubulars, chemical inhibition in flowlines.",
                "MeteringStrategy": (
                    "Subsea multiphase flow meters (MPFM) per well. "
                    "Topside allocation via test separator."
                ),
                "WellAutomation": "Subsea choke control with remote optimisation from onshore.",
                "WaterManagement": (
                    "Produced water treatment to <30 mg/L oil-in-water. "
                    "Enhanced treatment module on FPSO for environmental compliance. "
                    "Re-injection considered for Phase 2."
                ),
            },

            "ParentObjectID": reservoir_id,
            "ancestry": {
                "parents": [reservoir_id] if reservoir_id else [],
                "children": [],
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
    print(f"DevelopmentConcept v2 WPC manifest written \u2192 {out}")
    print(f"  WPC ID        : {wpc_id}")
    print(f"  Reservoir     : {reservoir_id}")


if __name__ == "__main__":
    main()
