# OSDU Schemas for Seismic Interpretation — M27 Landscape & Worked Example

## Table of Contents

- [1) Executive Summary](#1-executive-summary)
- [2) M27 Official Schemas](#2-m27-official-schemas)
- [3) Schema Inheritance Architecture](#3-schema-inheritance-architecture)
- [4) Interpretation Chain — Seed to Surface](#4-interpretation-chain--seed-to-surface)
- [5) StructureMap:1.0.0 — Detailed Properties](#5-structuremap100--detailed-properties)
- [6) GenericBinGrid:1.0.0 & AbstractGenericBinGrid:1.0.0](#6-genericbingrid100--abstractgenericbingrid100)
- [7) HorizonControlPoints:1.0.0](#7-horizoncontrolpoints100)
- [8) SeismicHorizon:2.1.0](#8-seismichorizon210)
- [9) Field Alignment Across Schemas](#9-field-alignment-across-schemas)
- [10) Supplementary Proposal: SeismicInterpretationProject](#10-supplementary-proposal-seismicinterpretationproject)
- [11) Generating OSDU Records from RDDMS Content](#11-generating-osdu-records-from-rddms-content)
- [12) Demo Implementation — Volantis Worked Example](#12-demo-implementation--volantis-worked-example)
- [13) Community Context & Open Questions](#13-community-context--open-questions)
- [14) xlsx Proposals vs Official M27 Model](#14-xlsx-proposals-vs-official-m27-model)
- [15) Duplication Argument: StructureMap vs GenericRepresentation + HorizonInterpretation](#15-duplication-argument-structuremap-vs-genericrepresentation--horizoninterpretation)
- [16) References](#16-references)

---

## 1) Executive Summary

Seismic interpretation workflows produce **horizon surfaces**, **fault interpretations**, **velocity models**, and **bin grid definitions**. These objects live as RESQML content in the Reservoir DDMS (RDDMS), where they are accessed computationally. To make them **discoverable** — searchable by name, domain, spatial area, petroleum system element, interpreter — they must also be registered as OSDU catalog records (WPCs) in the search index.

### What changed with M27

The OSDU Data Definitions **M27 release** (tag v0.30.0, February 2026) shipped four new schemas that close the most critical gaps:

| New M27 Schema | What it catalogs |
|---|---|
| **`StructureMap:1.0.0`** | Depth/time gridded surfaces on a GenericBinGrid — the "depth structure map" |
| **`GenericBinGrid:1.0.0`** | Standalone reusable lattice grid, independent of seismic acquisition |
| **`HorizonControlPoints:1.0.0`** | Seed picks for horizon interpretation — the "control points" WPC |
| **`SeismicHorizon:2.1.0`** | Updated to link back to HorizonControlPoints via `HorizonControlPointsID` |

Together with the existing schemas, the M27 set provides a **complete interpretation chain**:

```
HorizonControlPoints  →  SeismicHorizon  →  StructureMap
   (seed picks)           (TWT surface)      (depth/time grid)
         │                     │                    │
         └──── all link to ────┘──── same ─────────►  HorizonInterpretation
                                                         (geologic meaning)
```

### What remains

| Gap | Status | Our Contribution |
|---|---|---|
| No project-level grouping of interpretation products | **Not yet addressed** by community | `SeismicInterpretationProject:1.0.0` proposal in [`demo/seisint/`](../demo/seisint/) |
| Worked example demonstrating the full chain | **Requested** ([Issue #31 note_100547](https://gitlab.opengroup.org/osdu/subcommittees/data-def/projects/seismic/docs/-/issues/31#note_100547): "a robust worked example would be valuable") | Volantis worked example in `demo/seisint/` |
| SeismicSurfaceGeneration activity template | **In progress** on [branch 822](https://gitlab.opengroup.org/osdu/data/data-definitions/-/tree/822) (Chris Hough + JFR) | Tracked, not implemented here |

### Existing Schemas (pre-M27)

| OSDU Schema | What it catalogs |
|---|---|
| `HorizonInterpretation:1.2.0` | Geologic meaning of a horizon (the "what") |
| `SeismicHorizon:2.0.0` | A TWT or depth surface pick on seismic geometry |
| `SeismicBinGrid:1.3.0` | Seismic acquisition lattice geometry |
| `SeismicFault:2.0.0` / `FaultInterpretation:1.1.0` | Fault picks and geologic interpretation |
| `GenericRepresentation:1.2.0` | Catch-all for any representation |
| `VelocityModeling:1.4.0` | Velocity model metadata |
| `LocalBoundaryFeature:1.1.0` | Geologic feature — the named "thing" (e.g. "Top Volantis") |

---

## 2) M27 Official Schemas

### 2.1 StructureMap:1.0.0

**Kind**: `osdu:wks:work-product-component--StructureMap:1.0.0`
**Status**: PUBLISHED — First deployed M27.0 (v0.30.0)
**Governance**: OSDU (Subsurface Geophysics domain)
**Consuming domains**: Subsurface GeologyPetrophysics, Subsurface Reservoir

**Description**: "A structure map representation is a support for properties based on a GenericBinGrid. Consequently, its type is always a Regular2DGrid. It is often associated to some Z values either in depth or time domain."

**Inherits**: `AbstractRepresentation:1.0.0` + `AbstractGenericBinGrid:1.0.0`

The dual inheritance is the key design decision — StructureMap gets **both** representation metadata (InterpretationID, CRS, DDMSDatasets) **and** inline grid geometry (Origin, Bearing, Width, NodeCount). When using an external grid, populate `BinGridID` instead of the inline AbstractGenericBinGrid properties.

**Individual properties** (beyond inherited):

| Property | Type | Target | Description |
|---|---|---|---|
| `BinGridID` | string | → GenericBinGrid:1.0.0 \| SeismicBinGrid:1.3.0 | Reference to existing bin grid. Mutually exclusive with inline grid. |
| `SeismicHorizonID` | string | → SeismicHorizon:2.1.0 | The seismic horizon from which this structure map was computed |
| `DomainTypeID` | string | → DomainType ref-data | Depth / Time / Mixed — "added to be human friendly and support search" |
| `ExtensionProperties` | object | — | Catch-all for operator-specific extensions |

### 2.2 GenericBinGrid:1.0.0

**Kind**: `osdu:wks:work-product-component--GenericBinGrid:1.0.0`
**Status**: PUBLISHED — First deployed M27.0
**Inherits**: `AbstractGenericBinGrid:1.0.0`

**Role**: Standalone, referenceable lattice grid independent of seismic acquisition. The non-seismic counterpart to `SeismicBinGrid:1.3.0`. Referenced by StructureMap via `BinGridID`.

No additional individual properties — all grid geometry comes from `AbstractGenericBinGrid:1.0.0` (see §6).

### 2.3 HorizonControlPoints:1.0.0

**Kind**: `osdu:wks:work-product-component--HorizonControlPoints:1.0.0`
**Status**: PUBLISHED — First deployed M27.0
**Inherits**: `AbstractRepresentation:1.0.0`

**Role**: Seed picks used for horizon interpretation. Links to seismic input data, well markers, and carries tabular control point data.

**Individual properties**:

| Property | Type | Description |
|---|---|---|
| `RepresentationRole` | ref-data | Role of the representation |
| `RepresentationType` | ref-data | Type (PointSet, etc.) |
| `SeismicTraceDataIDs[]` | rel → SeismicTraceData | Seismic cubes used for picking |
| `BinGridID` | rel → GenericBinGrid \| SeismicBinGrid | Grid context for picks |
| `SeismicLineGeometryIDs[]` | rel → SeismicLineGeometry | 2D line geometry refs |
| `Seismic3DInterpretationSetID` | rel → Seismic3DInterpretationSet | 3D survey context |
| `Seismic2DInterpretationSetID` | rel → Seismic2DInterpretationSet | 2D survey context |
| `DomainTypeID` | ref-data → DomainType | Depth / Time |
| `HorizontalCRSID` | rel → CoordinateReferenceSystem | CRS for pick coordinates |
| `VerticalDatum` | AbstractFacilityVerticalMeasurement | Vertical reference |
| `WellboreMarkerSetIDs[]` | rel → WellboreMarkerSet | Well tie markers |
| `HorizonControlPoints` | AbstractColumnBasedTable | Tabular pick data (I, J, X, Y, Z) |
| `ExtensionProperties` | object | Operator extensions |

### 2.4 SeismicHorizon:2.1.0

**Kind**: `osdu:wks:work-product-component--SeismicHorizon:2.1.0`
**Status**: PUBLISHED — First deployed M27.0

**Change from 2.0.0**: Added `HorizonControlPointsID` (→ HorizonControlPoints:1.0.0).

This single addition creates the **traceability link** from the interpolated surface back to the seed picks that generated it, completing the lineage chain.

---

## 3) Schema Inheritance Architecture

```
AbstractCommonResources:1.0.1          (id, kind, acl, legal, meta, tags)
    └─ AbstractWPCGroupType:1.2.0      (Datasets[], DDMSDatasets[], Artefacts[], NameAliases[])
        └─ AbstractWorkProductComponent:1.1.0  (Name, SpatialArea, SpatialPoint, GeoContexts[],
        │                                        LineageAssertions[], AuthorIDs[])
        ├─ AbstractInterpretation:1.1.0        (DomainTypeID, FeatureID, FeatureName)
        │   ├─ HorizonInterpretation:1.2.0     (StratigraphicRoleTypeID, BoundaryRelationTypeID, ...)
        │   └─ FaultInterpretation:1.1.0       (FaultThrowDescriptions[], IsListric, ...)
        │
        ├─ AbstractRepresentation:1.0.0        (InterpretationID, LocalModelCompoundCrsID,
        │   │                                    TimeSeries, RealizationIndex, IndexableElementCount[])
        │   ├─ SeismicHorizon:2.1.0            (DomainTypeID, SeismicHorizonTypeID, Interpreter, ...)
        │   ├─ SeismicFault:2.0.0              (DomainTypeID, BinGridID, Interpreter, ...)
        │   ├─ GenericRepresentation:1.2.0     (Role, Type)
        │   ├─ VelocityModeling:1.4.0          (...)
        │   ├─ HorizonControlPoints:1.0.0      (seed picks — M27)
        │   └─ StructureMap:1.0.0              (depth/time grid surface — M27)
        │       └─ also inherits AbstractGenericBinGrid:1.0.0 (dual inheritance)
        │
        ├─ AbstractBinGrid:1.1.0               (ABCDBinGridSpatialLocation)
        │   └─ SeismicBinGrid:1.3.0            (P6 properties, inline/crossline ranges)
        │
        └─ AbstractGenericBinGrid:1.0.0        (Origin, Bearing, Width, NodeCount — M27)
            └─ GenericBinGrid:1.0.0            (standalone grid entity — M27)
```

**Key design principles**:
- Schemas inheriting **AbstractInterpretation** carry geologic meaning (the "what") — no geometry data
- Schemas inheriting **AbstractRepresentation** carry surface/geometry metadata (the "how") — linked to an interpretation via `InterpretationID`
- Schemas inheriting **AbstractBinGrid** define seismic acquisition lattice geometry
- Schemas inheriting **AbstractGenericBinGrid** define non-seismic lattice geometry (new in M27)
- StructureMap has **dual inheritance**: AbstractRepresentation + AbstractGenericBinGrid (can define grid inline or reference via BinGridID)
- `DDMSDatasets[]` (from AbstractWPCGroupType) links the OSDU catalog record to the RDDMS object where the actual data lives

### AbstractGenericBinGrid vs AbstractBinGrid

M27 introduces `AbstractGenericBinGrid:1.0.0` as a **separate abstract** from the existing `AbstractBinGrid:1.1.0`. Key differences:

| Aspect | AbstractBinGrid:1.1.0 | AbstractGenericBinGrid:1.0.0 |
|---|---|---|
| Used by | SeismicBinGrid | GenericBinGrid, StructureMap |
| Direction | I & J axis via P6 vector increments | J axis bearing only (MapGridBearingOfBinGridJaxis) |
| Node counts | InlineMin/Max, CrosslineMin/Max (seismic terminology) | NodeCountOnIAxis, NodeCountOnJAxis (generic) |
| I-axis orientation | Explicit via P6BinNodeIncrementOnIaxis | Implicit: perpendicular to J, direction set by TransformationMethod (EPSG 9666 right-handed / 1049 left-handed) |
| ABCD corners | ABCDBinGridSpatialLocation | ABCDBinGridSpatialLocation (same) |
| Additional | — | ScaleFactor, TransformationMethod, BinGridName |

---

## 4) Interpretation Chain — Seed to Surface

The M27 schemas establish a complete, traceable chain from raw picks to final depth surface:

```mermaid
graph TD
    subgraph MasterData
        BF[LocalBoundaryFeature]
    end

    subgraph Interpretations
        HI[HorizonInterpretation]
    end

    subgraph SeedPicks
        HCP[HorizonControlPoints]
    end

    subgraph TWT
        SH[SeismicHorizon]
    end

    subgraph DepthSurface
        SM[StructureMap]
    end

    subgraph Grids
        SBG[SeismicBinGrid]
        GBG[GenericBinGrid]
    end

    subgraph DataStorage
        DS[DDMSDatasets]
    end

    HI -->|FeatureID| BF
    SH -->|InterpretationID| HI
    SH -->|HorizonControlPointsID| HCP
    SH -->|SeismicBinGridID| SBG
    SM -->|InterpretationID| HI
    SM -->|SeismicHorizonID| SH
    SM -->|BinGridID| GBG
    SM -->|BinGridID| SBG
    HCP -->|InterpretationID| HI
    SH -.->|DDMSDatasets| DS
    SM -.->|DDMSDatasets| DS
    HCP -.->|DDMSDatasets| DS
```

**The complete chain** for a single horizon:

```
LocalBoundaryFeature  →  HorizonInterpretation  →  HorizonControlPoints  →  SeismicHorizon (TWT)  →  StructureMap (Depth)
   "Top Volantis"          "Top Volantis"            "Top Volantis picks"     "Top Volantis TWT"       "Top Volantis Depth"
```

Each arrow represents a schema reference (FeatureID, InterpretationID, HorizonControlPointsID, SeismicHorizonID). The chain provides full provenance from named geologic feature through to the final depth map.

---

## 5) StructureMap:1.0.0 — Detailed Properties

### 5.1 Grid Sourcing Strategy

StructureMap supports two mutually exclusive approaches to grid definition:

| Approach | When to use | Properties populated |
|---|---|---|
| **Inline grid** | Surface has its own unique grid | `OriginEasting`, `OriginNorthing`, `BinWidthOnI/Jaxis`, `MapGridBearingOfBinGridJaxis`, `NodeCountOnI/JAxis`, `TransformationMethod`, `ABCDBinGridSpatialLocation` (from AbstractGenericBinGrid) |
| **External grid ref** | Multiple surfaces share a grid | `BinGridID` → GenericBinGrid:1.0.0 or SeismicBinGrid:1.3.0 |

The schema explicitly states: *"Mutually exclusive with inline bin grid definition via the AbstractGenericBinGrid properties. Only one approach should be populated."*

### 5.2 Complete Property Summary

| Source | Property | Type | Description |
|---|---|---|---|
| AbstractRepresentation | `InterpretationID` | rel → HorizonInterpretation (and others) | Geologic interpretation link |
| AbstractRepresentation | `InterpretationName` | string (derived) | Name from linked interpretation |
| AbstractRepresentation | `LocalModelCompoundCrsID` | rel → LocalModelCompoundCrs | CRS context |
| AbstractRepresentation | `TimeSeries` | object | Time-step reference for geomechanics |
| AbstractRepresentation | `RealizationIndex` | integer | Stochastic realization index |
| AbstractRepresentation | `IndexableElementCount[]` | array | Element counts |
| AbstractGenericBinGrid | `BinGridName` | string | Name of the bin grid |
| AbstractGenericBinGrid | `ABCDBinGridSpatialLocation` | AbstractSpatialLocation | ABCD corner coordinates |
| AbstractGenericBinGrid | `OriginEasting` | number | Grid origin easting |
| AbstractGenericBinGrid | `OriginNorthing` | number | Grid origin northing |
| AbstractGenericBinGrid | `ScaleFactor` | number | Scale factor (default 1) |
| AbstractGenericBinGrid | `BinWidthOnIaxis` | number | Node spacing on I axis |
| AbstractGenericBinGrid | `BinWidthOnJaxis` | number | Node spacing on J axis |
| AbstractGenericBinGrid | `MapGridBearingOfBinGridJaxis` | number (0–360°) | Clockwise from grid north to J axis |
| AbstractGenericBinGrid | `NodeCountOnIAxis` | number | Number of nodes on I axis |
| AbstractGenericBinGrid | `NodeCountOnJAxis` | number | Number of nodes on J axis |
| AbstractGenericBinGrid | `TransformationMethod` | integer | EPSG code: 9666 (right-handed) or 1049 (left-handed) |
| **Individual** | **`BinGridID`** | rel → GenericBinGrid \| SeismicBinGrid | External grid reference (mutex with inline) |
| **Individual** | **`SeismicHorizonID`** | rel → SeismicHorizon:2.1.0 | Source TWT surface |
| **Individual** | **`DomainTypeID`** | ref-data → DomainType | Depth / Time / Mixed |
| **Individual** | **`ExtensionProperties`** | object | Operator-specific extensions |

### 5.3 Design Notes

- **No Interpreter field**: Unlike SeismicHorizon:2.0.0, StructureMap does not have `Interpreter` or `Remarks[]`. This metadata can be carried in the inherited `AuthorIDs[]` (from AbstractWorkProductComponent) or in `ExtensionProperties`.
- **No RepresentationType**: The description states the type is "always Regular2DGrid", so there is no explicit property.
- **No PetroleumSystemElementTypeID**: Can be derived from the linked HorizonInterpretation / BoundaryFeature or placed in ExtensionProperties.
- **DomainTypeID note**: The schema description says it's "added to be human friendly and support search" and to "keep both properties synchronised" with HorizonInterpretation.

---

## 6) GenericBinGrid:1.0.0 & AbstractGenericBinGrid:1.0.0

### 6.1 AbstractGenericBinGrid Properties

All properties below are inherited by both `GenericBinGrid:1.0.0` and `StructureMap:1.0.0`:

| Property | Type | Description |
|---|---|---|
| `BinGridName` | string | Name of the bin grid |
| `ABCDBinGridSpatialLocation` | AbstractSpatialLocation:1.1.0 | Corner coordinates: A=(i=0,j=0), B=(i=0,j=jMax), C=(i=Imax,j=0), D=(i=Imax,j=Jmax) |
| `OriginEasting` | number | Easting of origin point (A point) |
| `OriginNorthing` | number | Northing of origin point (A point) |
| `ScaleFactor` | number | Scale factor for bin grid (default 1) |
| `BinWidthOnIaxis` | number | Distance between nodes on I axis |
| `BinWidthOnJaxis` | number | Distance between nodes on J axis |
| `MapGridBearingOfBinGridJaxis` | number (0–360°) | Clockwise angle from grid north to J axis direction |
| `NodeCountOnIAxis` | number | Count of nodes on I axis |
| `NodeCountOnJAxis` | number | Count of nodes on J axis |
| `TransformationMethod` | integer | EPSG 9666 (right-handed) or 1049 (left-handed) |

### 6.2 ABCD Corner Convention

```
A = (i=0, j=0)       origin
B = (i=0, j=jMax)    end of J axis from origin
C = (i=Imax, j=0)    end of I axis from origin  
D = (i=Imax, j=Jmax) far corner
```

**Note**: This ABCD convention differs from some earlier documents. The official schema description states: `A = (i=0, j=0), B = (i=0, j=jMax), C = (i=Imax, j=0) and D = (i=Imax, j=Jmax)`.

### 6.3 Conversion: GenericBinGrid ↔ SeismicBinGrid

Bidirectional conversion is supported (see `_shared.py` helpers):

| SeismicBinGrid | GenericBinGrid | Conversion |
|---|---|---|
| `P6BinGridOriginEasting` | `OriginEasting` | Direct copy |
| `P6BinGridOriginNorthing` | `OriginNorthing` | Direct copy |
| `P6BinNodeIncrementOnJaxis {X,Y}` | `BinWidthOnJaxis` + `MapGridBearingOfBinGridJaxis` | width = √(X²+Y²), bearing = atan2(X,Y) |
| `P6BinNodeIncrementOnIaxis {X,Y}` | computed from TransformationMethod | I-axis perpendicular to J, handedness sets direction |
| `InlineMax - InlineMin + 1` | `NodeCountOnIAxis` | Direct |
| `CrosslineMax - CrosslineMin + 1` | `NodeCountOnJAxis` | Direct |

### 6.4 TransformationMethod — Handedness

| EPSG Code | Name | I-axis relative to J-axis |
|---|---|---|
| 9666 | P6 Seismic Bin Grid Transformation (right-handed) | I-axis = J-axis bearing + 90° |
| 1049 | General polynomial transformation (left-handed) | I-axis = J-axis bearing - 90° |

Reference: IOGP Guidance Note 373-07-2 and 483-6.

---

## 7) HorizonControlPoints:1.0.0

### 7.1 Role in the Chain

HorizonControlPoints represents the **seed data** used to create an interpreted surface. This includes:
- Manual picks on seismic sections
- Auto-tracked picks
- Well markers used as tie points
- Any other control input

The `HorizonControlPoints` tabular data uses `AbstractColumnBasedTable` — a column-oriented storage format where columns can represent inline, crossline, X, Y, Z, confidence, etc.

### 7.2 Key Relationships

```
HorizonControlPoints
    ├─ InterpretationID    → HorizonInterpretation  (same geologic meaning)
    ├─ SeismicTraceDataIDs → SeismicTraceData[]      (input cubes)
    ├─ BinGridID           → GenericBinGrid | SeismicBinGrid  (grid context)
    ├─ WellboreMarkerSetIDs → WellboreMarkerSet[]    (well tie points)
    └─ Seismic3D/2DInterpretationSetID → survey context
```

### 7.3 Downstream Link

SeismicHorizon:2.1.0 references HorizonControlPoints via `HorizonControlPointsID`, creating full lineage:

```
Picks (HorizonControlPoints) → Surface (SeismicHorizon) → Depth Map (StructureMap)
```

---

## 8) SeismicHorizon:2.1.0

### 8.1 Change from 2.0.0

Only one addition:

| New Property | Type | Description |
|---|---|---|
| `HorizonControlPointsID` | rel → HorizonControlPoints:1.0.0 | Links the interpolated surface back to its seed picks |

All other properties remain the same as 2.0.0:

| Property | Type | Description |
|---|---|---|
| `DomainTypeID` | ref-data → DomainType | Depth / Time / Mixed |
| `RepresentationType` | ref-data → RepresentationType | Regular2DGrid, PolylineSet, etc. |
| `SeismicHorizonTypeID` | ref-data | Peak, Trough, Zero Crossing |
| `PetroleumSystemElementTypeID` | ref-data | Reservoir, Source, Seal |
| `Interpreter` | string | Person/team name |
| `Remarks[]` | array | Annotation remarks |
| `HorizonControlPointsID` | rel → HorizonControlPoints | **NEW in 2.1.0** — seed picks link |

### 8.2 DomainTypeID vs SeismicDomainTypeID

Issue [#12 (Seismic Domain vs Domain)](https://gitlab.opengroup.org/osdu/subcommittees/data-def/projects/seismic/home/-/issues/12) resolved the naming: `SeismicDomainTypeID` was migrated to `DomainTypeID` for consistency across all representation schemas. This was treated as a minor version bump (1.6.0 → 1.7.0 → 2.0.0) rather than a patch.

---

## 9) Field Alignment Across Schemas

### 9.1 Representation Schemas

| Field | SeismicHorizon:2.1.0 | SeismicFault:2.0.0 | StructureMap:1.0.0 | GenericRep:1.2.0 |
|---|---|---|---|---|
| `InterpretationID` | ✓ (inherited) | ✓ (inherited) | ✓ (inherited) | ✓ (inherited) |
| `DomainTypeID` | ✓ | ✓ | ✓ | ✗ |
| `RepresentationType` | ✓ | ✓ | ✗ (always Regular2DGrid) | ✓ (as `Type`) |
| `Interpreter` | ✓ | ✓ | ✗ | ✗ |
| `Remarks[]` | ✓ | ✓ | ✗ | ✗ |
| `PetroleumSystemElementTypeID` | ✓ | ✗ | ✗ | ✗ |
| `BinGridID` ref | ✗ (SeismicBinGridID) | ✓ | ✓ (GenericBinGrid \| SeismicBinGrid) | ✗ |
| `SeismicHorizonID` | ✗ | ✗ | ✓ | ✗ |
| `HorizonControlPointsID` | ✓ (M27) | ✗ | ✗ | ✗ |
| Inline grid properties | ✗ | ✗ | ✓ (AbstractGenericBinGrid) | ✗ |
| `LocalModelCompoundCrsID` | ✓ (inherited) | ✓ (inherited) | ✓ (inherited) | ✓ (inherited) |
| `DDMSDatasets[]` | ✓ (inherited) | ✓ (inherited) | ✓ (inherited) | ✓ (inherited) |
| `ExtensionProperties` | ✗ | ✗ | ✓ | ✗ |

### 9.2 Grid Schemas

| Field | SeismicBinGrid:1.3.0 | GenericBinGrid:1.0.0 | StructureMap:1.0.0 (inline mode) |
|---|---|---|---|
| `ABCDBinGridSpatialLocation` | ✓ | ✓ | ✓ |
| Origin Easting/Northing | ✓ (P6BinGridOrigin...) | ✓ (Origin...) | ✓ (Origin...) |
| Axis widths | ✓ (P6BinNodeIncrement vector) | ✓ (BinWidthOnI/Jaxis scalar) | ✓ (BinWidthOnI/Jaxis scalar) |
| Axis direction | ✓ (P6 increment vectors) | ✓ (MapGridBearingOfBinGridJaxis + TransformationMethod) | ✓ (same) |
| Node/range count | ✓ (InlineMin/Max, CrosslineMin/Max) | ✓ (NodeCountOnI/JAxis) | ✓ (NodeCountOnI/JAxis) |
| `BinGridName` | ✗ | ✓ | ✓ |
| `ScaleFactor` | ✗ | ✓ | ✓ |
| `TransformationMethod` | P6TransformationMethod | TransformationMethod (EPSG 9666/1049) | TransformationMethod |

### 9.3 Consistent Reference Data

| Ref-Data Type | Used By |
|---|---|
| `DomainType` (Depth/Time/Mixed) | HorizonInterpretation, SeismicHorizon, SeismicFault, StructureMap, HorizonControlPoints |
| `RepresentationType` | SeismicHorizon, SeismicFault, GenericRepresentation, HorizonControlPoints |
| `StratigraphicRoleType` | HorizonInterpretation |
| `BoundaryRelationType` | HorizonInterpretation |
| `PetroleumSystemElementType` | SeismicHorizon |

---

## 10) Supplementary Proposal: SeismicInterpretationProject

### 10.1 The Gap

A 2025 interpretation session produces 5 horizons, 3 faults, and a velocity model. There is no OSDU record linking them into a coherent project. Users rely on naming conventions or ad-hoc ancestry to find related objects.

The existing `Seismic3DInterpretationSet` (master-data) groups seismic **surveys** (geometry + trace data). It does not group interpretation **products** (horizons, faults, maps).

### 10.2 Proposal

**Kind**: `dev:wks:work-product-component--SeismicInterpretationProject:1.0.0`
**Inherits**: `AbstractWorkProductComponent:1.1.0` (NOT AbstractRepresentation — it's a grouping record)
**Schema**: [`demo/seisint/schema_seismicinterpretationproject.json`](../demo/seisint/schema_seismicinterpretationproject.json)

| Property | Type | Description |
|---|---|---|
| `HorizonInterpretationIDs[]` | rel → HorizonInterpretation | All horizons |
| `SeismicHorizonIDs[]` | rel → SeismicHorizon | TWT picks |
| `StructureMapIDs[]` | rel → StructureMap | Depth surfaces |
| `FaultInterpretationIDs[]` | rel → FaultInterpretation | Fault interpretations |
| `SeismicFaultIDs[]` | rel → SeismicFault | Fault representations |
| `SeismicTraceDataIDs[]` | rel → SeismicTraceData | Cubes interpreted |
| `SeismicBinGridID` | rel → SeismicBinGrid | Primary bin grid |
| `VelocityModelingID` | rel → VelocityModeling | Velocity model |
| `InterpreterName` | string | Person/team |
| `InterpretationDate` | datetime | When |
| `SoftwareUsed` | string | Application name + version |
| `ResqmlDataspaceID` | rel → ETPDataspace | RDDMS dataspace link |

---

## 11) Generating OSDU Records from RDDMS Content

OSDU catalog records are generated from RESQML content already stored in the RDDMS. This is a **metadata extraction + registration** pipeline, not a data copy.

### 11.1 Pipeline Pattern

```
RDDMS Dataspace
    │
    ├─ obj_GeneticBoundaryFeature      ──►  LocalBoundaryFeature (master-data)
    ├─ obj_HorizonInterpretation       ──►  HorizonInterpretation (WPC)
    ├─ seed picks / markers            ──►  HorizonControlPoints (WPC)       ◄── M27
    ├─ obj_Grid2dRepresentation (TWT)  ──►  SeismicHorizon (WPC)
    ├─ obj_Grid2dRepresentation (Depth)──►  StructureMap (WPC)               ◄── M27
    ├─ obj_FaultInterpretation         ──►  FaultInterpretation (WPC)
    ├─ obj_TriangulatedSetRep (fault)  ──►  SeismicFault (WPC)
    └─ (lattice geometry, non-seismic) ──►  GenericBinGrid (WPC)             ◄── M27
```

### 11.2 Key Mapping Rules

| RDDMS Source | OSDU Target | How |
|---|---|---|
| `Citation.Title` | `data.Name` | Direct copy |
| `Uuid` | `DDMSDatasets[].DatasetURI` | `eml://rddms/{dataspace}/{type}('{uuid}')` |
| `RepresentedInterpretation.UUID` | `data.InterpretationID` | Resolve to OSDU HorizonInterpretation ID |
| `LocalCrs` type (Depth3d vs Time3d) | `data.DomainTypeID` | Map to DomainType ref-data |
| `Grid2dPatch` axis counts | `NodeCountOnI/JAxis` (StructureMap inline) | Direct from RDDMS grid geometry |
| `Grid2dPatch` origin/offset | `OriginEasting/Northing` + bearing/width | Compute from RDDMS supporting representation |
| `BoundaryRelation[]` | `data.BoundaryRelationTypeID` | Map array → single most-specific value |
| `SequenceStratigraphySurface` | `data.StratigraphicRoleTypeID` | Enum → ref-data mapping |

### 11.3 DDMSDatasets[] — The RDDMS Link

Every OSDU representation WPC links to its RDDMS counterpart:

```json
{
  "DDMSDatasets": [{
    "SchemaFormatTypeID": "dev:reference-data--SchemaFormatType:RESQML20:Grid2dRepresentation:",
    "DatasetURI": "eml://rddms-1/dataspace('demo/volantis-interp')/resqml20.obj_Grid2dRepresentation('aabbccdd-1122-3344-5566-778899aabb01')"
  }]
}
```

---

## 12) Demo Implementation — Volantis Worked Example

Working example records and scripts are in [`demo/seisint/`](../demo/seisint/):

| File | Description |
|---|---|
| `_shared.py` | Shared helpers: deterministic UUIDs, ID builders, grid geometry, ABCD corners |
| `gen_volantis_interp.py` | Python generator script — produces the full manifest |
| `manifest_volantis_interp.json` | Complete worked example: full chain for a Volantis interpretation |
| `schema_seismicinterpretationproject.json` | SeismicInterpretationProject:1.0.0 schema (supplementary proposal) |
| `references/` | Test horizon JSONs, discussion docs from OSDU GitLab |

### 12.1 Scenario

The manifest demonstrates the **Volantis 2025 Interpretation** — a synthetic scenario using realistic parameters for the Volantis field (Norwegian Sea):

| Layer | Records | Schema |
|---|---|---|
| Features | 3 (Top Volantis, Base Volantis, Top Therys) | `LocalBoundaryFeature:1.1.0` |
| Interpretations | 3 horizon interpretations | `HorizonInterpretation:1.2.0` |
| Seismic grid | 1 (Volantis3D, 12.5m × 12.5m) | `SeismicBinGrid:1.3.0` |
| TWT picks | 3 horizons | `SeismicHorizon:2.1.0` |
| Depth grid | 1 shared 25m grid | **`GenericBinGrid:1.0.0`** (M27) |
| Depth surfaces | 3 (2 on shared grid, 1 inline) | **`StructureMap:1.0.0`** (M27) |
| Project grouping | 1 | **`SeismicInterpretationProject:1.0.0`** (proposal) |
| **Total** | **~18 records** | |

### 12.2 Grid Strategy Demonstrated

The manifest shows both grid approaches:

| StructureMap Record | Grid Approach | Details |
|---|---|---|
| Top Volantis Depth Map | **External ref** → GenericBinGrid | `BinGridID` points to shared 25m grid |
| Base Volantis Depth Map | **External ref** → GenericBinGrid | Same shared grid |
| Top Therys Depth Map | **Inline grid** | Grid properties embedded directly on StructureMap |

### 12.3 Running the Generator

```bash
cd demo/seisint
python gen_volantis_interp.py
```

Output: `manifest_volantis_interp.json` — a complete OSDU manifest ready for ingestion via the Storage Service.

---

## 13) Community Context & Open Questions

### 13.1 Key Decisions (from 2026 Meeting Minutes)

| Date | Decision |
|---|---|
| 2026-02-16 | **StructureMap, AbstractGenericBinGrid, GenericBinGrid approved for M27** |
| 2026-02-16 | **HorizonControlPoints approved for M27** with AbstractColumnBasedTable for tabular data |
| 2026-02-09 | SeismicHorizon:2.1.0 adds `HorizonControlPointsID` link |
| 2026-01-26 | Oslo F2F workshop dates confirmed: April 13–17, 2026 |

### 13.2 SeismicSurfaceGeneration Activity Template

[Issue #863](https://gitlab.opengroup.org/osdu/data/data-definitions/-/issues/863) tracks the creation of a `SeismicSurfaceGeneration` activity template on branch 822. This template defines the seed-to-surface workflow:

- **Inputs**: SeismicTraceData, SeismicBinGrid/GenericBinGrid, HorizonControlPoints, VelocityModel
- **Outputs**: SeismicHorizon, StructureMap
- **Parameters**: Grid parameterization, algorithm selection, domain type

When approved, it will provide standardized Activity records linking inputs to outputs — complementary to the schema-level references documented here.

### 13.3 Oslo F2F Workshop (April 2026)

The Oslo F2F (April 13–17, 2026) plans two MVP workshops:
- **MVP1**: Structure Map end-to-end demonstration (the [ddm_mvp1_structuremap](https://gitlab.opengroup.org/osdu/subcommittees/data-def/projects/seismic/ddm_mvp1_structuremap) repo)
- **MVP2**: Expanded scope (horizons + faults + activities)

Our Volantis worked example is positioned as a contribution to MVP1.

### 13.4 Open Questions

| Question | Status | Notes |
|---|---|---|
| Should StructureMap carry `Interpreter` / `Remarks[]`? | Open | SeismicHorizon has them; StructureMap relies on inherited AuthorIDs[] or ExtensionProperties |
| Multi-Z surfaces (structure map as 2D multi-z) | Deferred | Risk of duplicating what already exists in RESQML normalized model in the RDDMS |
| SeismicInterpretationProject as official schema | Not yet proposed | Our demo includes it; could be submitted after M27 |
| Generic Property WPCs for Z values | Under discussion | Properties on a StructureMap may be stored as separate GenericProperty WPCs |

---

## 14) xlsx Proposals vs Official M27 Model

Three xlsx proposal workbooks were developed during the Seismic 2.0 design phase. This section maps their proposed properties against the official M27 schemas that were ultimately approved.

### 14.1 SeismicHorizon Proposals Template (p586)

The `SeismicHorizon_Proposals_Template.xlsx` proposed a `SeismicHorizon:2.0.0` with 21 individual properties. Here is how each maps to the official M27 schema set:

| xlsx Proposed Property | Official M27 Location | Notes |
|---|---|---|
| `allOf → AbstractRepresentation` | SeismicHorizon:2.1.0 (inherited) | Same — no change |
| `Seismic3DInterpretationSetID` | SeismicHorizon:2.1.0 | Was not adopted on SeismicHorizon; moved to **HorizonControlPoints:1.0.0** |
| `Seismic2DInterpretationSetID` | SeismicHorizon:2.1.0 | Same — moved to **HorizonControlPoints:1.0.0** |
| `RepresentationRole` | SeismicHorizon:2.1.0 | Not on SeismicHorizon; adopted on **HorizonControlPoints:1.0.0** |
| `RepresentationType` | SeismicHorizon:2.1.0 ✓ | Retained |
| `DomainTypeID` | SeismicHorizon:2.1.0 ✓ | Retained (renamed from SeismicDomainTypeID) |
| `SeismicTraceDataIDs[]` | SeismicHorizon:2.1.0 | Not on SeismicHorizon; moved to **HorizonControlPoints:1.0.0** |
| `BinGridID` | SeismicHorizon:2.1.0 | Not on SeismicHorizon; available on **HorizonControlPoints:1.0.0** and **StructureMap:1.0.0** |
| `SeismicLineGeometryIDs[]` | Not on SeismicHorizon | Moved to **HorizonControlPoints:1.0.0** |
| `SeismicHorizonTypeID` | SeismicHorizon:2.1.0 ✓ | Retained |
| `VelocityModelID` | Not adopted | Not on any M27 schema; would need to go on StructureMap or Activity |
| `SeismicAttribute` | Not adopted | Proposed as nested object, parked — no ref-data defined yet |
| `SubjectiveClassificationRatingIDs` | Not adopted | Quality info handled by inherited `TechnicalAssurances[]` (AbstractWPCGroupType) |
| `InlineMin/Max, CrosslineMin/Max` | Not on SeismicHorizon | Grid extent info moved to **HorizonControlPoints:1.0.0** / BinGrid |
| `GeologicalUnitName` | Not adopted | Available via `InterpretationID` → HorizonInterpretation → FeatureID chain |
| `PetroleumSystemElementTypeID` | SeismicHorizon:2.1.0 ✓ | Retained |
| `Interpreter` | SeismicHorizon:2.1.0 ✓ | Retained |
| `Remark` | SeismicHorizon:2.1.0 ✓ (as `Remarks[]`) | Retained |

**Key observation**: The xlsx proposed a "fat" SeismicHorizon with 21+ properties. The M27 design instead **split responsibilities** across three schemas:
- **HorizonControlPoints** got the input-side properties (SeismicTraceDataIDs, BinGridID, survey refs, line geometry)
- **SeismicHorizon** kept the surface-side properties (DomainType, Type, Interpreter)
- **StructureMap** got the depth-output properties (BinGridID, DomainType, SeismicHorizonID)

This split aligns with the RESQML pattern of separating picks (PointSetRepresentation) from surfaces (Grid2dRepresentation).

### 14.2 Properties Not Present in Any M27 Schema

Several xlsx-proposed properties have **no direct home** in the official M27 schemas:

| Proposed Property | Gap Analysis | Improvement Suggestion |
|---|---|---|
| `VelocityModelID` | No link from StructureMap to velocity model | Add to StructureMap via ExtensionProperties, or propose as individual property for StructureMap:1.1.0 |
| `SeismicAttribute` | No ref-data type defined yet | Propose as `SeismicAttributeTypeID` ref-data; useful for search "show me all amplitude maps" |
| `GeologicalUnitName` | Derivable from HorizonInterpretation → FeatureID → Name | Could add as `x-osdu-is-derived` on SeismicHorizon for search convenience |
| `SubjectiveClassificationRatingIDs` | Subsumed by TechnicalAssurances[] | Already covered by inherited abstract — no gap |

### 14.3 Consistency Improvements for RESQML-Derived Schemas

Comparing the xlsx proposals against the final M27 schemas, and considering RESQML lossless mapping, we identify these improvements:

#### HorizonInterpretation improvements

The current `HorizonInterpretation:1.2.0` has some mapping gaps with RESQML `obj_HorizonInterpretation`:

| RESQML Property | Current OSDU Mapping | Improvement |
|---|---|---|
| `BoundaryRelation[]` (array of enums) | `BoundaryRelationTypeID` (single ref-data) | **Upgrade to `BoundaryRelationTypeIDs[]`** (array) — RESQML allows multiple relations on one horizon |
| `DomainTypeID` on interpretation | Single value | Consider **`DomainTypeIDs[]`** (array) for mixed-domain interpretations — matches RESQML's domain tagging at interpretation level |
| `SequenceStratigraphySurface` (enum) | `StratigraphicRoleTypeID` | Mapping works but RESQML has richer enum values (e.g. `transgressive`, `maximum flooding`) — need complete ref-data |
| Cultural feature support | Only `FeatureID` → LocalBoundaryFeature | RESQML supports `GeneticBoundaryFeature` with `IsConformable` flag — OSDU has `IsConformableAbove/Below` which is richer |

#### SeismicHorizon → StructureMap consistency

Properties that exist on SeismicHorizon but are absent from StructureMap, creating asymmetry:

| Property | SeismicHorizon | StructureMap | Impact |
|---|---|---|---|
| `Interpreter` | ✓ | ✗ | Cannot search "depth maps by interpreter" without traversing SeismicHorizonID |
| `Remarks[]` | ✓ | ✗ | Annotations lost on depth conversion |
| `PetroleumSystemElementTypeID` | ✓ | ✗ | Cannot filter depth maps by reservoir/source/seal |
| `RepresentationType` | ✓ | ✗ (implied Regular2DGrid) | Fine for now, but limits future extension to triangulated depth maps |

**Recommendation**: Propose `StructureMap:1.1.0` adding `Interpreter`, `Remarks[]`, and `PetroleumSystemElementTypeID` as optional individual properties (non-breaking minor version bump). This makes search consistent across TWT and depth representations.

#### Cross-schema field consistency

Ideal end-state for lossless RESQML round-tripping:

| Field | Should Be On | Currently On | Status |
|---|---|---|---|
| `DomainTypeID` | All representations | SeismicHorizon, SeismicFault, StructureMap, HorizonControlPoints | ✓ (Good — M27 extended this) |
| `InterpretationID` | All representations | All (via AbstractRepresentation) | ✓ |
| `BinGridID` | All grid-based representations | SeismicFault, StructureMap, HorizonControlPoints | SeismicHorizon still uses `SeismicBinGridID` — inconsistent name |
| `Interpreter` | All representations | SeismicHorizon, SeismicFault only | **Gap** — StructureMap, HorizonControlPoints lack it |
| `DDMSDatasets[]` | All WPCs | All (via AbstractWPCGroupType) | ✓ |

---

## 15) Duplication Argument: StructureMap vs GenericRepresentation + HorizonInterpretation

A common counter-argument to StructureMap: *"We already have GenericRepresentation (which inherits AbstractRepresentation) and HorizonInterpretation. Can't we just store a depth map as GenericRepresentation with InterpretationID → HorizonInterpretation, and avoid creating a new schema?"*

This section evaluates the argument systematically.

### 15.1 Arguments **for** using GenericRepresentation (against StructureMap)

| # | Argument | Weight |
|---|---|---|
| 1 | **Fewer schemas to maintain** — every new schema adds governance burden, migration cost, and complexity | Strong |
| 2 | **GenericRepresentation already exists** — proven, deployed, indexed | Strong |
| 3 | **`ExtensionProperties` suffices** — any operator-specific metadata (grid params, depth range) can go in ExtensionProperties on GenericRepresentation | Medium |
| 4 | **RDDMS holds the data** — the OSDU record is just a catalog pointer, so thin metadata is acceptable | Medium |
| 5 | **Avoid proliferation** — creating StructureMap now may invite FaultMap, IsochoreMap, IsopachMap next | Medium |
| 6 | **HorizonInterpretation carries the semantics** — the "what" is already properly modeled; the representation just needs to be a pointer | Medium |

### 15.2 Arguments **for** StructureMap (against GenericRepresentation)

| # | Argument | Weight |
|---|---|---|
| 1 | **Search precision** — GenericRepresentation returns all 1D/2D representations (fault networks, arbitrary polylines, well paths). "Show me all depth maps" requires filtering by convention, not by `kind`. StructureMap gives a **type-safe search target**: `kind:*StructureMap*` | **Critical** |
| 2 | **Grid geometry as first-class data** — StructureMap inherits AbstractGenericBinGrid, giving it inline grid properties (origin, bearing, spacing, node count) or a BinGridID reference. GenericRepresentation has neither — grid context is completely opaque. | **Critical** |
| 3 | **Typed relationships** — StructureMap has `SeismicHorizonID` (provenance) and `DomainTypeID` (search). GenericRepresentation has only `Role` and `Type` (free-text-like, no ref-data enforcement) | Strong |
| 4 | **Consistent with OSDU patterns** — SeismicHorizon is a specialized representation (not GenericRepresentation). StructureMap follows the same pattern: a purpose-built schema for a specific domain object. GenericRepresentation is explicitly described as a "catch-all" — using it for a well-defined domain concept contradicts its purpose | Strong |
| 5 | **RESQML alignment** — RESQML has distinct types for Grid2dRepresentation (structure maps) vs PointSetRepresentation (picks) vs TriangulatedSetRepresentation (faults). A 1:1 mapping to distinct OSDU schemas preserves type information for round-tripping | Strong |
| 6 | **Grid reuse pattern** — StructureMap's BinGridID enables the pattern where N maps share one grid. With GenericRepresentation you'd need custom conventions for this | Medium |
| 7 | **Community consensus** — StructureMap was approved through the formal OSDU governance process (M27.0) after multi-year discussion across operators. The community explicitly chose not to use GenericRepresentation | Medium |

### 15.3 The Duplication Concern in Detail

The diagram below shows what is actually "duplicated":

```
GenericRepresentation (existing)
      inherits: AbstractRepresentation
      individual: Role (string), Type (string)
      → total individual properties: 2 (both free-text-like)

StructureMap (M27)
      inherits: AbstractRepresentation + AbstractGenericBinGrid
      individual: BinGridID, SeismicHorizonID, DomainTypeID, ExtensionProperties
      → total individual properties: 4 (all typed/referenced)
```

**What overlaps**: Both inherit `AbstractRepresentation` (InterpretationID, CRS, DDMSDatasets). This is by design — all OSDU representations share a common base. This is inheritance, not duplication.

**What does NOT overlap**:
- StructureMap adds `AbstractGenericBinGrid` (10 grid properties) — GenericRepresentation has nothing equivalent
- StructureMap adds `SeismicHorizonID` (typed provenance) — GenericRepresentation has no provenance link
- StructureMap adds `DomainTypeID` (ref-data search) — GenericRepresentation has no domain filtering
- GenericRepresentation's `Role`/`Type` are generic strings; StructureMap's type is implicit (always Regular2DGrid)

### 15.4 The HorizonInterpretation Angle

HorizonInterpretation is an **interpretation** (the "what"), not a **representation** (the "how"). The OSDU model explicitly separates these:

```
Interpretation (1) ──► Representations (N)
   "Top Volantis"        "TWT SeismicHorizon"  +  "Depth StructureMap"  +  "TriangulatedSet GenericRep"
```

HorizonInterpretation already links to StructureMap via the inherited `InterpretationID` on StructureMap. Creating StructureMap does not duplicate HorizonInterpretation — it provides the **representation-side record** that HorizonInterpretation references.

The question is whether this representation should be typed (StructureMap) or generic (GenericRepresentation). The M27 decision was: **typed**, because the additional grid geometry and typed references justify the dedicated schema.

### 15.5 Verdict

| Concern | Assessment |
|---|---|
| Schema count increases | True, but justified by search precision and grid geometry needs |
| Overlap with GenericRepresentation | Minimal — only shared AbstractRepresentation base (by design) |
| Overlap with HorizonInterpretation | None — different abstraction layer (interpretation vs representation) |
| Migration burden | Low — no existing data needs migration; StructureMap is additive |
| Future proliferation risk | Mitigated by ExtensionProperties and the StructureMap description explicitly stating "type is always Regular2DGrid" |

**Bottom line**: StructureMap is not a duplication of GenericRepresentation + HorizonInterpretation. It fills a genuine gap — **searchable, typed, grid-aware depth surface catalog records** — that the existing schemas do not provide. The community governance process validated this conclusion.

---

## 16) References

### OSDU Data Definitions — M27 Schemas

| Ref | Description |
|---|---|
| [StructureMap:1.0.0](https://community.opengroup.org/osdu/data/data-definitions/-/blob/master/E-R/work-product-component/StructureMap.1.0.0.md) | Official M27 schema |
| [GenericBinGrid:1.0.0](https://community.opengroup.org/osdu/data/data-definitions/-/blob/master/E-R/work-product-component/GenericBinGrid.1.0.0.md) | Official M27 schema |
| [AbstractGenericBinGrid:1.0.0](https://community.opengroup.org/osdu/data/data-definitions/-/blob/master/E-R/abstract/AbstractGenericBinGrid.1.0.0.md) | Official M27 abstract |
| [HorizonControlPoints:1.0.0](https://community.opengroup.org/osdu/data/data-definitions/-/blob/master/E-R/work-product-component/HorizonControlPoints.1.0.0.md) | Official M27 schema |
| [SeismicHorizon:2.1.0](https://community.opengroup.org/osdu/data/data-definitions/-/blob/master/E-R/work-product-component/SeismicHorizon.2.1.0.md) | Updated with HorizonControlPointsID |

### OSDU Data Definitions — Existing Schemas

| Ref | Description |
|---|---|
| [HorizonInterpretation:1.2.0](https://community.opengroup.org/osdu/data/data-definitions/-/blob/master/E-R/work-product-component/HorizonInterpretation.1.2.0.md) | Geologic meaning |
| [SeismicBinGrid:1.3.0](https://community.opengroup.org/osdu/data/data-definitions/-/blob/master/E-R/work-product-component/SeismicBinGrid.1.3.0.md) | Acquisition grid |
| [GenericRepresentation:1.2.0](https://community.opengroup.org/osdu/data/data-definitions/-/blob/master/E-R/work-product-component/GenericRepresentation.1.2.0.md) | Catch-all |
| [SeismicFault:2.0.0](https://community.opengroup.org/osdu/data/data-definitions/-/blob/master/E-R/work-product-component/SeismicFault.2.0.0.md) | Fault representation |
| [AbstractRepresentation:1.0.0](https://community.opengroup.org/osdu/data/data-definitions/-/blob/master/E-R/abstract/AbstractRepresentation.1.0.0.md) | Shared representation abstract |

### GitLab Project Resources

| Ref | Description |
|---|---|
| [Issue #31 — Support Depth Structure Map Use Case](https://gitlab.opengroup.org/osdu/subcommittees/data-def/projects/seismic/docs/-/issues/31) | Structure Map discussion + worked example request |
| [Issue #12 — Seismic Domain vs Domain](https://gitlab.opengroup.org/osdu/subcommittees/data-def/projects/seismic/home/-/issues/12) | DomainTypeID naming resolution |
| [Issue #863 — SeismicSurfaceGeneration Activity Template](https://gitlab.opengroup.org/osdu/data/data-definitions/-/issues/863) | Activity template in progress |
| [Seismic 2.0 ReadMe](https://gitlab.opengroup.org/osdu/subcommittees/data-def/projects/seismic/docs/-/blob/main/ReadMe.md) | Three-track roadmap |
| [Horizon Discussion Wrapup (Oct 2024)](https://gitlab.opengroup.org/osdu/subcommittees/data-def/projects/seismic/docs/-/blob/main/Seismic-Horizon-discussion-wrapup-Oct2024.md) | Architectural scenarios for SeismicHorizon evolution |

### ORES Workspace

| Doc | Description |
|---|---|
| [CrsGuide.md](CrsGuide.md) | CRS mapping guide |
| [StratColumn.md](StratColumn.md) | Stratigraphic column mapping |
| [FmuOsdu.md](FmuOsdu.md) | FMU ↔ OSDU mapping |
| [`demo/seisint/`](../demo/seisint/) | Worked example, schemas, generator scripts |

---

> **Document version**: 3.0 — 2026-04-07
> **Authors**: ORES project team
> **Status**: Updated for M27 — documents official schemas + worked example
> **Previous versions**: 2.0 (pre-M27 gap analysis), 1.0 (initial RESQML comparison)
