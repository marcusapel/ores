# Drogon Demo – RESQML v2.2

Curated Drogon structural model exported in **RESQML 2.2** format for OSDU Reservoir-DDMS integration testing.

**Dataspace:** `maap/drogon22`  
**RESQML version:** 2.2 (Energistics EML Common 2.3)  
**OSDU manifest:** `manifest_drogon22_opendes.json` (173 records)

---

## Package Contents

| File | Description |
|------|-------------|
| `drogon_demo_22.epc` | RESQML 2.2 EPC container (418 objects) |
| `drogon_demo_22.h5` | HDF5 array backing store |
| `manifest_drogon22_opendes.json` | OSDU manifest (173 records, opendes partition) |
| `README.md` | This file |

---

## Dataset Summary

| Component | RDDMS (EPC) | Catalog (Manifest) |
|---|---|---|
| Total objects/records | 418 | 173 |
| Boundary features | 12 (`BoundaryFeature`) | 12 `LocalBoundaryFeature` |
| Horizons (interpretations) | 6 `HorizonInterpretation` | 6 `HorizonInterpretation` |
| Faults (interpretations) | 6 `FaultInterpretation` | 6 `FaultInterpretation` |
| Structural model | 1 `StructuralOrganizationInterpretation` | 1 `StructuralModel` |
| Grid | 1 `IjkGridRepresentation` (92×146×69) | 1 `IjkGridRepresentation` |
| Grid properties | 189 Continuous + 65 Discrete | 32 `GenericProperty` (grid-attached) |
| Surfaces | 15 `Grid2dRepresentation` | 13 `StructureMap` + 2 `SeismicHorizon` |
| Wells | 12 `WellboreFeature` | 12 `Wellbore` (master-data) |
| Trajectories | 12+12 (trajectory + deviation) | 12 `WellboreTrajectory` |
| Well logs | 9 frames (222 curves) | 9 `WellLog` |
| Wellbore markers | 9 marker frames | 9 `WellboreMarkerSet` |
| Stratigraphy | Column + Rank + 5 Units | 1 `StratigraphicColumn` + 1 Rank + 5 Units |
| Rock volumes | 5 `RockVolumeFeature` | 5 `LocalRockVolumeFeature` |
| CRS | 2 `LocalEngineeringCompoundCrs` | 2 `LocalModelCompoundCrs` |
| Fault representations | 6 polyline + 23 point sets | 29 `GenericRepresentation` |

---

## RESQML 2.2 vs 2.0.1 – OSDU Alignment

This dataset demonstrates the improved alignment between RESQML 2.2 and the OSDU data model.
The OSDU schemas were designed alongside RESQML 2.2, resulting in significantly better 1:1 mappings:

| Mapping Quality | 2.0.1 | 2.2 | Notes |
|----------------|-------|-----|-------|
| **Direct 1:1** | 7 types | 12 types | +5 direct mappings |
| **Many-to-one collapse** | 5 | 2 | 3 fewer lossy collapses |
| **Context-dependent** | 1 | 1 | Grid2d → depth/time (unchanged) |
| **Metadata-only** | 5 types | 4 types | Fewer objects lost |

### Key improvements

| RESQML 2.0.1 | RESQML 2.2 | OSDU Kind | Improvement |
|---|---|---|---|
| `GeneticBoundaryFeature` + `TectonicBoundaryFeature` | `BoundaryFeature` | `LocalBoundaryFeature` | Unified → 1:1 (was 2:1 collapse) |
| `StratigraphicUnitFeature` | `RockVolumeFeature` | `LocalRockVolumeFeature` | Renamed → 1:1 (semantic match) |
| `OrganizationFeature` | `Model` | `LocalModelFeature` | Renamed → 1:1 |
| `LocalDepth3dCrs` + `LocalTime3dCrs` | `LocalEngineeringCompoundCrs` | `LocalModelCompoundCrs` | Unified → 1:1 |
| Custom `PropertyKind` XML objects | `PropertyKindIndex` (standard dict) | `reference-data--PropertyKind` | Standard dictionary alignment |

### DDMSDataset URI format

```
RESQML 2.0.1: eml://reservoir-ddms1/dataspace('maap/drogon')/resqml20.obj_GeneticBoundaryFeature(uuid)
RESQML 2.2:   eml://reservoir-ddms1/dataspace('maap/drogon22')/resqml22.BoundaryFeature(uuid)
```

---

## OSDU Schema Kinds Used

```
osdu:wks:dataset--ETPDataspace:1.0.1
osdu:wks:master-data--LocalBoundaryFeature:1.1.0
osdu:wks:master-data--Wellbore:1.3.0
osdu:wks:work-product-component--LocalModelCompoundCrs:1.2.0
osdu:wks:work-product-component--GenericBinGrid:1.0.0
osdu:wks:work-product-component--HorizonInterpretation:1.2.0
osdu:wks:work-product-component--FaultInterpretation:1.3.0
osdu:wks:work-product-component--StructuralModel:1.0.0
osdu:wks:work-product-component--StructureMap:1.0.0
osdu:wks:work-product-component--SeismicHorizon:2.1.0
osdu:wks:work-product-component--GenericRepresentation:1.2.0
osdu:wks:work-product-component--IjkGridRepresentation:1.1.0
osdu:wks:work-product-component--GenericProperty:1.2.0
osdu:wks:work-product-component--WellboreTrajectory:1.3.0
osdu:wks:work-product-component--WellLog:1.2.0
osdu:wks:work-product-component--WellboreMarkerSet:1.2.0
osdu:wks:work-product-component--StratigraphicColumn:1.2.0
osdu:wks:work-product-component--StratigraphicColumnRankInterpretation:1.3.0
osdu:wks:work-product-component--LocalRockVolumeFeature:1.2.0
osdu:wks:work-product-component--StratigraphicUnitInterpretation:1.3.0
osdu:wks:work-product-component--LocalModelFeature:1.2.0
```

---

## Ingestion

```bash
# Full pipeline (create dataspace → ETP import → manifest push)
python ingest_drogon22.py interop

# Manifest only (skip ETP, use Storage API)
python ingest_drogon22.py interop --skip-etp --storage

# Build manifest from 2.0.1 EPC (type conversion)
python build_full_manifest_22.py --from-201
```

---

## FIRP Data Model

The OSDU subsurface model follows **Feature → Interpretation → Representation → Property** (FIRP):

```
BoundaryFeature (horizon/fault)
  └── HorizonInterpretation / FaultInterpretation
        └── Grid2dRepresentation → StructureMap / SeismicHorizon
        └── PolylineSetRepresentation → GenericRepresentation (fault sticks)
        └── PointSetRepresentation → GenericRepresentation (fault picks)

Model
  └── StructuralOrganizationInterpretation → StructuralModel
        ├── references all HorizonInterpretations
        └── references all FaultInterpretations

WellboreFeature → Wellbore (master-data)
  └── WellboreInterpretation
        └── WellboreTrajectoryRepresentation → WellboreTrajectory
        └── WellboreFrameRepresentation → WellLog (with nested curves)
        └── WellboreMarkerFrameRepresentation → WellboreMarkerSet

RockVolumeFeature → LocalRockVolumeFeature
  └── StratigraphicUnitInterpretation

IjkGridRepresentation
  └── ContinuousProperty / DiscreteProperty → GenericProperty
```

---

## Source

- **Field:** Drogon (synthetic, Norwegian Continental Shelf)
- **Original data:** Equinor Drogon benchmark dataset
- **Converted by:** ORES build_full_manifest_22.py (from RESQML 2.0.1 → 2.2 type mapping)
- **Date:** May 2026
