# OSDU Schemas for Seismic Interpretation — M27 Landscape & Worked Example

## Table of Contents

- [1) Executive Summary](#1-executive-summary)
  - [Catalog Record vs Actual Data](#catalog-record-vs-actual-data--the-core-concept)
- [2) M27 Official Schemas](#2-m27-official-schemas)
- [3) Schema Inheritance Architecture](#3-schema-inheritance-architecture)
- [4) Interpretation Chain — Seed to Surface](#4-interpretation-chain--seed-to-surface)
- [5) StructureMap:1.0.0 — Detailed Properties](#5-structuremap100--detailed-properties)
  - [5.4 Where Are the Z-Values?](#54-where-are-the-z-values--relationship-anatomy)
- [6) GenericBinGrid:1.0.0 & AbstractGenericBinGrid:1.0.0](#6-genericbingrid100--abstractgenericbingrid100)
- [7) HorizonControlPoints:1.0.0](#7-horizoncontrolpoints100)
- [8) SeismicHorizon:2.1.0](#8-seismichorizon210)
- [9) Field Alignment Across Schemas](#9-field-alignment-across-schemas)
- [10) Supplementary Proposal: SeismicInterpretationProject](#10-supplementary-proposal-seismicinterpretationproject)
- [11) Generating OSDU Records from RDDMS Content](#11-generating-osdu-records-from-rddms-content)
- [12) StructureMap in Reservoir DDMS — RESQML 2.2 Storage & Generation](#12-structuremap-in-reservoir-ddms--resqml-22-storage--generation)
  - [12.9 What's Actually in the RDDMS](#129-whats-actually-in-the-rddms--live-data-from-maap-drogon)
- [13) Demo Implementation — Volantis Worked Example](#13-demo-implementation--volantis-worked-example)
- [14) Community Context & Open Questions](#14-community-context--open-questions)
- [15) Duplication Argument: StructureMap vs GenericRepresentation + HorizonInterpretation](#15-duplication-argument-structuremap-vs-genericrepresentation--horizoninterpretation)
- [16) References](#16-references)

---

## 1) Executive Summary

Seismic interpretation workflows produce **horizon surfaces**, **fault interpretations**, **velocity models**, and **bin grid definitions**. These objects live as RESQML content in the Reservoir DDMS (RDDMS), where they are accessed computationally. To make them **discoverable** — searchable by name, domain, spatial area, petroleum system element, interpreter — they must also be registered as OSDU catalog records (WPCs) in the search index.

### Catalog Record vs Actual Data — The Core Concept

A structure map (or any interpretation surface) lives in **two places**, each serving a different purpose:

| Layer | What is stored | Where | Access pattern |
|---|---|---|---|
| **OSDU Catalog Record** (e.g. StructureMap:1.0.0, GenericRepresentation:1.2.0) | Searchable metadata — name, interpretation link, grid geometry parameters, CRS info, spatial area | OSDU Storage + Search index | REST: Search API → Storage API |
| **Reservoir DDMS (RDDMS)** | Actual surface data — Z-value arrays (depth or TWT), full grid geometry, CRS objects | RESQML objects in the Reservoir DDMS | REST: RDDMS API → `Grid2dRepresentation` |

The OSDU record **never contains the Z-value arrays** (the depth/time surface data). It intentionally duplicates only the grid geometry parameters (origin, bearing, spacing, node counts) so the surface can be discovered spatially without hitting the RDDMS. For visualisation or computation, the `DDMSDatasets[]` URI on the OSDU record points to the RDDMS object where the actual data lives:

```mermaid
flowchart LR
    OSDU["**OSDU Catalog Record**<br/>Name · InterpretationID<br/>Grid geometry (origin, spacing)<br/>SpatialArea · DomainTypeID<br/>❌ No Z-values"]
    RDDMS["**Reservoir DDMS**<br/>Grid2dRepresentation<br/>Z-values array (depth/TWT)<br/>Full lattice geometry<br/>LocalCrs · ✅ All data here"]
    OSDU -- "DDMSDatasets[] URI<br/>(generic inherited array —<br/>only bridge to actual data)" --> RDDMS
```

There is **no dedicated "StructureMap" type in RESQML** — a `Grid2dRepresentation` with a depth CRS **is** the structure map. The distinction between depth and TWT is made entirely by the CRS (`VerticalAxis.IsTime = false` for depth, `true` for TWT). The OSDU StructureMap:1.0.0 schema is a catalog wrapper that makes this RESQML object discoverable.

> **Important**: The StructureMap record has **no typed relationship field** pointing to the RDDMS depth surface.  `BinGridID` links to grid geometry (XY positions only), `SeismicHorizonID` links to the TWT source, `InterpretationID` links to geologic meaning.  The **only** link to the actual depth Z-values is `DDMSDatasets[]` — a generic URI array inherited from `AbstractWPCGroupType`, not a StructureMap-specific property.  See [§5.4](#54-where-are-the-z-values--relationship-anatomy) for the full breakdown.

### What changed with M27

The OSDU Data Definitions **M27 release** (tag v0.30.0, February 2026) shipped four new schemas that close the most critical gaps:

| New M27 Schema | What it catalogs |
|---|---|
| **`StructureMap:1.0.0`** | Depth/time gridded surfaces on a GenericBinGrid — the "depth structure map" |
| **`GenericBinGrid:1.0.0`** | Standalone reusable lattice grid, independent of seismic acquisition |
| **`HorizonControlPoints:1.0.0`** | Seed picks for horizon interpretation — the "control points" WPC |
| **`SeismicHorizon:2.1.0`** | Updated: `BinGridID` (renamed from `SeismicBinGridID`), `HorizonControlPointsID` link, structured `Remark[]` |

Key breaking changes from pre-release drafts:
- **`CrsID` removed** from `AbstractRepresentation` — CRS now lives exclusively inside `ABCDBinGridSpatialLocation.AsIngestedCoordinates.CoordinateReferenceSystemID`
- **`SeismicBinGridID` → `BinGridID`** on SeismicHorizon — unified naming with StructureMap/SeismicFault
- **`Remarks[]` → `Remark[]`** on SeismicHorizon — changed from string array to structured `AbstractRemark` objects
- **`FeatureTypeID` dropped** from LocalBoundaryFeature — was present in earlier drafts, removed in M27

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

> **Schema Service registration**: M27 schemas may not be pre-deployed on all OSDU instances. Use `register_m27_schemas.py` to register them with the Schema Service. The script fetches resolved JSON Schema definitions from the OSDU Data Definitions GitLab repo, builds the `{ schemaInfo, schema }` envelope, and PUTs them to the Schema Service. Three schemas (HorizonInterpretation:1.2.0, LocalBoundaryFeature:1.1.0, SeismicBinGrid:1.3.0) are typically available from a shared tenant; the remaining four (StructureMap:1.0.0, GenericBinGrid:1.0.0, SeismicHorizon:2.1.0, HorizonControlPoints:1.0.0) require explicit registration.

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

**Key individual properties**: `SeismicTraceDataIDs[]`, `BinGridID`, `WellboreMarkerSetIDs[]`, `DomainTypeID`, `HorizonControlPoints` (AbstractColumnBasedTable with I, J, X, Y, Z columns), `ExtensionProperties`.

> Full property list: [HorizonControlPoints:1.0.0 schema](https://community.opengroup.org/osdu/data/data-definitions/-/blob/master/E-R/work-product-component/HorizonControlPoints.1.0.0.md)

### 2.4 SeismicHorizon:2.1.0

**Kind**: `osdu:wks:work-product-component--SeismicHorizon:2.1.0`
**Status**: PUBLISHED — First deployed M27.0

**Changes from 2.0.0**:
- Added `HorizonControlPointsID` (→ HorizonControlPoints:1.0.0) — creates the traceability link from the interpolated surface back to the seed picks
- Renamed `SeismicBinGridID` → `BinGridID` — unified naming with StructureMap and SeismicFault
- Replaced `Remarks[]` (string array) with `Remark[]` (array of `AbstractRemark` objects: `Remark`, `RemarkSource`, `RemarkDate`, `RemarkSequenceNumber`)

---

## 3) Schema Inheritance Architecture

```mermaid
classDiagram
    direction TB

    AbstractCommonResources <|-- AbstractWPCGroupType
    AbstractWPCGroupType <|-- AbstractWorkProductComponent

    AbstractWorkProductComponent <|-- AbstractInterpretation
    AbstractInterpretation <|-- HorizonInterpretation
    AbstractInterpretation <|-- FaultInterpretation

    AbstractWorkProductComponent <|-- AbstractRepresentation
    AbstractRepresentation <|-- SeismicHorizon
    AbstractRepresentation <|-- SeismicFault
    AbstractRepresentation <|-- GenericRepresentation
    AbstractRepresentation <|-- VelocityModeling
    AbstractRepresentation <|-- HorizonControlPoints
    AbstractRepresentation <|-- StructureMap
    AbstractGenericBinGrid <|-- StructureMap

    AbstractWorkProductComponent <|-- AbstractBinGrid
    AbstractBinGrid <|-- SeismicBinGrid

    AbstractWorkProductComponent <|-- AbstractGenericBinGrid
    AbstractGenericBinGrid <|-- GenericBinGrid

    class AbstractWPCGroupType {
        DDMSDatasets[]
        Datasets[]
        NameAliases[]
    }
    class AbstractInterpretation {
        DomainTypeID
        FeatureID
        FeatureName
    }
    class AbstractRepresentation {
        InterpretationID
        InterpretationName
        IndexableElementCount[]
    }
    class AbstractGenericBinGrid {
        Origin, Bearing
        BinWidth, NodeCount
    }
    class StructureMap {
        BinGridID
        SeismicHorizonID
        DomainTypeID
        «dual inheritance»
    }
    class HorizonControlPoints {
        «M27 new»
    }
    class GenericBinGrid {
        «M27 new»
    }
```

> 🟢 StructureMap, HorizonControlPoints, GenericBinGrid = **new M27** schemas. 🟠 SeismicHorizon = **updated in M27**.

**Key design principles**:
- Schemas inheriting **AbstractInterpretation** carry geologic meaning (the "what") — no geometry data
- Schemas inheriting **AbstractRepresentation** carry surface/geometry metadata (the "how") — linked to an interpretation via `InterpretationID`
- Schemas inheriting **AbstractBinGrid** define seismic acquisition lattice geometry
- Schemas inheriting **AbstractGenericBinGrid** define non-seismic lattice geometry (new in M27)
- StructureMap has **dual inheritance**: AbstractRepresentation + AbstractGenericBinGrid (can define grid inline or reference via BinGridID)
- `DDMSDatasets[]` (from AbstractWPCGroupType) links the OSDU catalog record to the RDDMS object where the actual surface data (Z-value arrays, full geometry, CRS) lives — **no OSDU schema carries the actual depth/time values**

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
    SH -->|BinGridID| SBG
    SM -->|InterpretationID| HI
    SM -->|SeismicHorizonID| SH
    SM -->|BinGridID| GBG
    SM -->|BinGridID| SBG
    HCP -->|InterpretationID| HI
    SH -.->|DDMSDatasets| DS
    SM -.->|DDMSDatasets| DS
    HCP -.->|DDMSDatasets| DS
```

**Solid arrows** = OSDU record-to-record references (metadata linkage, all within the OSDU catalog).  
**Dashed arrows** = `DDMSDatasets[]` links pointing out to the Reservoir DDMS where the actual Z-value arrays, grid geometry, and CRS objects are stored as RESQML content. Every representation schema (SeismicHorizon, StructureMap, HorizonControlPoints) has this link — the OSDU record is the catalog entry; the RDDMS object is the data.

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

### 5.2 Key Properties (Individual)

StructureMap inherits standard representation metadata from [AbstractRepresentation](https://community.opengroup.org/osdu/data/data-definitions/-/blob/master/E-R/abstract/AbstractRepresentation.1.0.0.md) (`InterpretationID`, `InterpretationName`, `IndexableElementCount[]`) and grid geometry from [AbstractGenericBinGrid](https://community.opengroup.org/osdu/data/data-definitions/-/blob/master/E-R/abstract/AbstractGenericBinGrid.1.0.0.md) (`Origin`, `Bearing`, `BinWidth`, `NodeCount`, `TransformationMethod`, `ABCDBinGridSpatialLocation`).

> **M27 note — CrsID removed**: Earlier schema drafts included a top-level `CrsID` on AbstractRepresentation.  The released M27 schemas removed this property; the CRS is now conveyed exclusively inside the `ABCDBinGridSpatialLocation.AsIngestedCoordinates.CoordinateReferenceSystemID` nested structure.

Its **own** individual properties are:

| Property | Type | Description |
|---|---|---|
| `BinGridID` | rel → GenericBinGrid \| SeismicBinGrid | External grid reference (mutex with inline) |
| `SeismicHorizonID` | rel → SeismicHorizon:2.1.0 | Source TWT surface (provenance) |
| `DomainTypeID` | ref-data → DomainType | Depth / Time / Mixed |
| `ExtensionProperties` | object | Operator-specific extensions |

> Full schema: [StructureMap:1.0.0](https://community.opengroup.org/osdu/data/data-definitions/-/blob/master/E-R/work-product-component/StructureMap.1.0.0.md)

### 5.3 Design Notes

- **No Interpreter field**: Unlike SeismicHorizon:2.1.0, StructureMap does not have `Interpreter` or `Remark[]`. This metadata can be carried in the inherited `AuthorIDs[]` (from AbstractWorkProductComponent) or in `ExtensionProperties`.
- **No RepresentationType**: The description states the type is "always Regular2DGrid", so there is no explicit property.
- **No PetroleumSystemElementTypeID**: Can be derived from the linked HorizonInterpretation / BoundaryFeature or placed in ExtensionProperties.
- **DomainTypeID note**: The schema description says it's "added to be human friendly and support search" and to "keep both properties synchronised" with HorizonInterpretation.

### 5.4 Where Are the Z-Values? — Relationship Anatomy

A common question: the StructureMap has `BinGridID` for grid geometry and various interpretation links — but **where are the actual depth values referenced?**

The answer: the StructureMap record has **no typed relationship field** for the depth surface data.  Every relationship field on the record points to metadata or geometry — none carry Z-values:

| Relationship Field | Points To | What It Provides | Carries Z-Values? |
|---|---|---|---|
| `InterpretationID` | HorizonInterpretation WPC | Geologic meaning ("Top Volantis") | ❌ No |
| `SeismicHorizonID` | SeismicHorizon WPC | Provenance — which TWT pick this came from | ❌ No |
| `BinGridID` | GenericBinGrid / SeismicBinGrid | **Grid XY geometry only** — node positions in map space | ❌ No |
| Inline grid props | (embedded on the record) | Same grid XY geometry, no external reference | ❌ No |
| **`DDMSDatasets[]`** | **RDDMS Grid2dRepresentation** | **The actual depth values (Z-array)** | ✅ **Yes — only here** |

`DDMSDatasets[]` is **not** a StructureMap-specific property — it is a generic URI array inherited from `AbstractWPCGroupType`, shared by all WPC schemas.  It contains an opaque EML URI string:

```
eml://rddms-1/dataspace('maap/drogon')/resqml20.obj_Grid2dRepresentation('f857c36c-...')
```

This means:

1. **The bin grid carries zero depth information** — it defines where nodes sit in XY, not what value they hold.  Think of it as the empty spreadsheet grid; the Z-values are the cell contents.
2. **There is no OSDU catalog record for the Grid2dRepresentation** — the StructureMap:1.0.0 **is** the catalog entry.  The RDDMS object is accessed only through the `DDMSDatasets[]` URI.
3. **Discovery vs data retrieval are separate acts** — you discover the surface via OSDU Search (name, domain, spatial area), then retrieve the actual data from the RDDMS.

```mermaid
flowchart TD
    SM["**StructureMap:1.0.0**<br/>(OSDU catalog record)<br/>Name, DomainType, grid geometry<br/>❌ No Z-values"]
    HI["HorizonInterpretation<br/>geologic meaning"]
    SH["SeismicHorizon<br/>TWT provenance"]
    BG["GenericBinGrid<br/>XY geometry only"]
    RDDMS["**RDDMS Grid2dRep**<br/>211,248 depth values<br/>✅ Z-array lives here"]

    SM -- "InterpretationID" --> HI
    SM -- "SeismicHorizonID" --> SH
    SM -. "BinGridID<br/>(or inline)" .-> BG
    SM == "DDMSDatasets[] URI<br/>(only bridge to Z-values)" ==> RDDMS

    style RDDMS fill:#2d6a2d,color:#fff
    style SM fill:#1a5276,color:#fff
```

---

## 6) GenericBinGrid:1.0.0 & AbstractGenericBinGrid:1.0.0

### 6.1 Overview

All grid geometry properties (OriginEasting, OriginNorthing, BinWidthOnI/Jaxis, MapGridBearingOfBinGridJaxis, NodeCountOnI/JAxis, ScaleFactor, TransformationMethod, ABCDBinGridSpatialLocation) are defined on [AbstractGenericBinGrid:1.0.0](https://community.opengroup.org/osdu/data/data-definitions/-/blob/master/E-R/abstract/AbstractGenericBinGrid.1.0.0.md) and inherited by both GenericBinGrid:1.0.0 and StructureMap:1.0.0.

GenericBinGrid:1.0.0 adds no individual properties — it exists solely as a standalone, referenceable grid entity (the non-seismic counterpart to SeismicBinGrid:1.3.0).

> Full schema: [GenericBinGrid:1.0.0](https://community.opengroup.org/osdu/data/data-definitions/-/blob/master/E-R/work-product-component/GenericBinGrid.1.0.0.md)

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

```mermaid
flowchart LR
    HCP[HorizonControlPoints] -->|InterpretationID| HI[HorizonInterpretation]
    HCP -->|BinGridID| Grid[GenericBinGrid / SeismicBinGrid]
    HCP -->|SeismicTraceDataIDs| STD[SeismicTraceData]
    HCP -->|WellboreMarkerSetIDs| WMS[WellboreMarkerSet]
    SH[SeismicHorizon] -->|HorizonControlPointsID| HCP
    SM[StructureMap] -->|SeismicHorizonID| SH
```

The chain provides full lineage: **Picks → TWT Surface → Depth Map**.

---

## 8) SeismicHorizon:2.1.0

### 8.1 Changes from 2.0.0

- **`HorizonControlPointsID`** (rel → HorizonControlPoints:1.0.0) — links the interpolated surface back to its seed picks.
- **`BinGridID`** renamed from `SeismicBinGridID` — unifies naming with StructureMap and SeismicFault.
- **`Remark[]`** replaces `Remarks[]` — now an array of structured `AbstractRemark` objects (`Remark`, `RemarkSource`, `RemarkDate`, `RemarkSequenceNumber`) instead of plain strings.

> Full schema: [SeismicHorizon:2.1.0](https://community.opengroup.org/osdu/data/data-definitions/-/blob/master/E-R/work-product-component/SeismicHorizon.2.1.0.md)

### 8.2 DomainTypeID vs SeismicDomainTypeID

Issue [#12 (Seismic Domain vs Domain)](https://gitlab.opengroup.org/osdu/subcommittees/data-def/projects/seismic/home/-/issues/12) resolved the naming: `SeismicDomainTypeID` was migrated to `DomainTypeID` for consistency across all representation schemas. This was treated as a minor version bump (1.6.0 → 1.7.0 → 2.0.0) rather than a patch.

---

## 9) Field Alignment Across Schemas

The key design differences between representation schemas (what each schema adds beyond the shared AbstractRepresentation base) are summarised below. For complete property lists, see the individual schema links in [§16 References](#16-references).

| Capability | SeismicHorizon | SeismicFault | StructureMap | GenericRep |
|---|:---:|:---:|:---:|:---:|
| `DomainTypeID` | ✓ | ✓ | ✓ | ✗ |
| `Interpreter` / `Remark[]` | ✓ | ✓ | ✗ | ✗ |
| `BinGridID` reference | ✓ | ✓ | ✓ (Generic or Seismic) | ✗ |
| Inline grid geometry | ✗ | ✗ | ✓ (AbstractGenericBinGrid) | ✗ |
| `SeismicHorizonID` provenance | ✗ | ✗ | ✓ | ✗ |
| `HorizonControlPointsID` | ✓ (M27) | ✗ | ✗ | ✗ |
| `ExtensionProperties` | ✗ | ✗ | ✓ | ✗ |

All share `InterpretationID`, `DDMSDatasets[]` via inheritance.

> **M27 note — BinGridID naming**: SeismicHorizon:2.1.0 now uses `BinGridID` (matching StructureMap/SeismicFault). Earlier drafts used `SeismicBinGridID`; this was unified in the M27 release. The `Remark[]` field (structured objects with `Remark`, `RemarkSource`, `RemarkDate`) replaces the informal `Remarks[]` string arrays from earlier versions.

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
| `SeismicBinGridID` | rel → SeismicBinGrid | Primary bin grid (custom proposal) |
| `VelocityModelingID` | rel → VelocityModeling | Velocity model |
| `InterpreterName` | string | Person/team |
| `InterpretationDate` | datetime | When |
| `SoftwareUsed` | string | Application name + version |
| `ResqmlDataspaceID` | rel → ETPDataspace | RDDMS dataspace link |

---

## 11) Generating OSDU Records from RDDMS Content

OSDU catalog records are generated from RESQML content already stored in the RDDMS. This is a **metadata extraction + registration** pipeline, not a data copy.

### 11.1 Pipeline Pattern

```mermaid
flowchart LR
    subgraph RDDMS["RDDMS Dataspace (RESQML)"]
        R1[GeneticBoundaryFeature]
        R2[HorizonInterpretation]
        R3[seed picks / markers]
        R4[Grid2dRep — TWT]
        R5[Grid2dRep — Depth]
        R6[FaultInterpretation]
        R7[TriangulatedSetRep]
        R8[lattice geometry]
    end

    subgraph OSDU["OSDU Catalog Records"]
        O1[LocalBoundaryFeature]
        O2[HorizonInterpretation WPC]
        O3[HorizonControlPoints 🆕]
        O4[SeismicHorizon WPC]
        O5[StructureMap 🆕]
        O6[FaultInterpretation WPC]
        O7[SeismicFault WPC]
        O8[GenericBinGrid 🆕]
    end

    R1 --> O1
    R2 --> O2
    R3 --> O3
    R4 --> O4
    R5 --> O5
    R6 --> O6
    R7 --> O7
    R8 --> O8
```

> 🆕 = M27 additions.

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

Every OSDU representation WPC links to its RDDMS counterpart via `DDMSDatasets[]`. The OSDU record carries **only metadata** (grid parameters, interpretation links, CRS) — the actual Z-value arrays live in the RDDMS as `Grid2dRepresentation` objects:

```json
{
  "DDMSDatasets": [
    "eml://rddms-1/dataspace('maap/drogon')/resqml20.obj_Grid2dRepresentation('f857c36c-3939-4ff3-9125-a11cf2af105c')"
  ]
}
```

The URI encodes the RDDMS host, dataspace path, RESQML type, and UUID — enough for any ETP-aware client to fetch the full object.

---

## 12) StructureMap in Reservoir DDMS — RESQML 2.2 Storage & Generation

As described in the [Catalog vs Data concept](#catalog-record-vs-actual-data--the-core-concept), the OSDU StructureMap:1.0.0 is a **catalog record** — it provides searchable metadata. The actual depth surface data (Z-value arrays on a grid) lives exclusively in the Reservoir DDMS as RESQML content. There is no mechanism to store Z-values in the OSDU record itself — `DDMSDatasets[]` is the bridge.

This section documents the bidirectional mapping between OSDU StructureMap and RESQML 2.2 `Grid2dRepresentation`, and how to generate one from the other.

### 12.1 RESQML Native Representation

In RESQML 2.2 (and 2.0.1), a depth structure map is stored as a **`Grid2dRepresentation`** — the same type used for TWT seismic horizons. The distinction between TWT and depth is made entirely by the **LocalCrs** (Coordinate Reference System):

| Domain | CRS Property | RESQML Value |
|---|---|---|
| Time (TWT) | `LocalCrs → VerticalAxis.IsTime` | `true` |
| Depth | `LocalCrs → VerticalAxis.IsTime` | `false` |
| Mixed | Multiple patches with different CRS | — |

**There is no dedicated "StructureMap" type in RESQML** — a `Grid2dRepresentation` with `SurfaceRole: "map"` and a depth CRS **is** the structure map. No RESQML extension is required.

### 12.2 Grid Geometry — Two RESQML Patterns

RESQML offers two grid geometry strategies that map 1:1 to the OSDU StructureMap approaches (§5.1):

#### Pattern A: Inline Lattice → OSDU Inline Grid

```json
{
  "Points": {
    "$type": "resqml22.Point3dZValueArray",
    "SupportingGeometry": {
      "$type": "resqml22.Point3dLatticeArray",
      "AllDimensionsAreOrthogonal": true,
      "Origin": { "Coordinate1": 461000.0, "Coordinate2": 6782000.0, "Coordinate3": 0 },
      "Dimension": [
        { "Direction": { "Coordinate1": 0, "Coordinate2": 1, "Coordinate3": 0 },
          "Spacing": { "Value": 25.0, "Count": 199 } },
        { "Direction": { "Coordinate1": 1, "Coordinate2": 0, "Coordinate3": 0 },
          "Spacing": { "Value": 25.0, "Count": 299 } }
      ]
    },
    "ZValues": { "Values": [-2150.0, -2151.2, "..."] }
  }
}
```

| RESQML Lattice Property | OSDU StructureMap Property | Conversion |
|---|---|---|
| `Origin.Coordinate1` | `OriginEasting` | Direct (in projected CRS) |
| `Origin.Coordinate2` | `OriginNorthing` | Direct (in projected CRS) |
| `Dimension[slow].Spacing.Value` | `BinWidthOnJaxis` | Direct |
| `Dimension[fast].Spacing.Value` | `BinWidthOnIaxis` | Direct |
| `Dimension[slow].Direction` | `MapGridBearingOfBinGridJaxis` | `atan2(Coord1, Coord2)` |
| `Dimension[slow].Spacing.Count + 1` | `NodeCountOnJAxis` | RESQML count = steps; OSDU count = nodes |
| `Dimension[fast].Spacing.Count + 1` | `NodeCountOnIAxis` | Same |
| `AllDimensionsAreOrthogonal` + axis order | `TransformationMethod` | Right-handed → EPSG 9666 |

#### Pattern B: Supporting Representation → OSDU External BinGridID

```json
{
  "Points": {
    "$type": "resqml22.Point3dZValueArray",
    "SupportingGeometry": {
      "$type": "resqml22.Point3dFromRepresentationLatticeArray",
      "SupportingRepresentation": {
        "Uuid": "aa5b90f1-2eab-4fa6-8720-69dd4fd51a4d",
        "QualifiedType": "resqml22.Grid2dRepresentation",
        "Title": "Seismic BinGrid"
      },
      "NodeIndicesOnSupportingRepresentation": { "StartValue": 0, "Offset": ["..."] }
    },
    "ZValues": { "..." }
  }
}
```

| RESQML Property | OSDU StructureMap Property |
|---|---|
| `SupportingRepresentation.Uuid` | `BinGridID` → resolve UUID to OSDU GenericBinGrid or SeismicBinGrid ID |
| Inline grid properties | Empty — grid geometry comes from the referenced BinGrid |

### 12.3 Complete Property Mapping Table

| OSDU StructureMap Property | RESQML Grid2dRepresentation Property | Direction | Notes |
|---|---|---|---|
| `data.Name` | `Citation.Title` | ↔ | Direct copy |
| `InterpretationID` | `RepresentedObject.Uuid` (→ HorizonInterpretation) | ↔ | UUID ↔ OSDU ID resolution |
| `DomainTypeID` = Depth | `LocalCrs.VerticalAxis.IsTime = false` | ← RESQML | CRS-based detection |
| `DomainTypeID` = Time | `LocalCrs.VerticalAxis.IsTime = true` | ← RESQML | CRS-based detection |
| `SeismicHorizonID` | — | OSDU only | No RESQML equivalent; provenance via Activity or ExtraMetadata |
| `BinGridID` | `SupportingRepresentation.Uuid` | ↔ | Only when external grid pattern used |
| `OriginEasting/Northing` | `Point3dLatticeArray.Origin` | ↔ | Only when inline grid; needs CRS-to-projected transform |
| `BinWidthOnI/Jaxis` | `Dimension[].Spacing.Value` | ↔ | Constant spacing assumed |
| `MapGridBearingOfBinGridJaxis` | `atan2(Dim[J].Direction.Coord1, .Coord2)` | ← RESQML | Computed from direction vector |
| `NodeCountOnI/JAxis` | `FastestAxisCount` / `SlowestAxisCount` | ↔ | RESQML Spacing.Count = nodes−1 |
| `TransformationMethod` | `AllDimensionsAreOrthogonal` + axis ordering | ← RESQML | 9666 if right-handed |
| `ABCDBinGridSpatialLocation` | Computed from Origin + Dimension vectors | ← RESQML | Corner computation |
| `DDMSDatasets[].DatasetURI` | Self-reference | → OSDU | `eml://{rddms}/dataspace('...')/resqml22.Grid2dRepresentation('{uuid}')` |
| `ExtensionProperties` | `ExtraMetadata` | ↔ | Name-value pairs |

### 12.4 RESQML 2.2.1 Extension Assessment

**No formal RESQML extension is required.** RESQML 2.2 `Grid2dRepresentation` natively supports everything needed for an OSDU StructureMap:

| Requirement | RESQML 2.2 Support | Status |
|---|---|---|
| Regular depth grid with Z values | `Grid2dRepresentation` + depth CRS | ✓ Native |
| Inline grid geometry | `Point3dLatticeArray` | ✓ Native |
| External bin grid reference | `Point3dFromRepresentationLatticeArray` + `SupportingRepresentation` | ✓ Native |
| Link to interpretation | `RepresentedObject` → HorizonInterpretation | ✓ Native |
| CRS / domain type | `LocalCrs` with vertical axis configuration | ✓ Native |
| Z-value storage (HDF5, external) | `FloatingPointExternalArray` | ✓ Native |
| OSDU integration metadata | `OSDUIntegration` block + `ExtraMetadata` | ✓ Via existing EML extension point |

#### Recommended ExtraMetadata Conventions

Three OSDU-specific properties have no direct RESQML equivalent. For lossless round-tripping, store them as **`ExtraMetadata`** name-value pairs with an `osdu:` prefix:

| OSDU Property | ExtraMetadata Key | Value | Purpose |
|---|---|---|---|
| `SeismicHorizonID` | `osdu:SeismicHorizonID` | OSDU WPC ID | Provenance link to TWT source (no RESQML equivalent) |
| `DomainTypeID` | `osdu:DomainTypeID` | Ref-data ID | Redundant with CRS but enables catalog sync without CRS parsing |
| `TransformationMethod` | `osdu:TransformationMethod` | `9666` or `1049` | EPSG code — can be inferred from lattice but explicit is safer |

This approach avoids the governance overhead of a formal Energistics extension proposal while keeping the RESQML object self-documenting for OSDU round-tripping.

#### When Would an Actual Extension Be Needed?

1. RESQML needs to store **OSDU-specific typed relationships** natively (not just ExtraMetadata strings)
2. Validation tools need to enforce OSDU constraints at the RESQML level
3. The `OSDUIntegration` block needs StructureMap-specific fields beyond `OSDULineageAssertion`

None of these conditions are currently met. The ExtraMetadata convention approach is sufficient.

### 12.5 Generating StructureMap from RDDMS RESQML Content

Given RESQML objects already stored in the RDDMS, OSDU StructureMap records can be **automatically generated** via metadata extraction:

```mermaid
flowchart TD
    A["RDDMS: GET Grid2dRepresentations"] --> B{"CRS check:\nIsTime == false?"}
    B -->|Yes — depth| C["For each depth surface"]
    B -->|No — TWT| skip[Skip]
    C --> D["Citation.Title → Name"]
    D --> E["RepresentedObject → InterpretationID"]
    E --> F{"Grid pattern?"}
    F -->|Inline lattice| G["Populate inline grid props\n(origin, bearing, spacing, nodes)"]
    F -->|External ref| H["Resolve SupportingRepresentation\n→ BinGridID"]
    G --> I["Find TWT counterpart → SeismicHorizonID"]
    H --> I
    I --> J["Build DDMSDatasets[] URI"]
    J --> K["Emit StructureMap:1.0.0 record"]
```

#### Finding the TWT Counterpart (SeismicHorizonID)

The `SeismicHorizonID` provenance link requires finding the TWT `Grid2dRepresentation` that shares the same `RepresentedObject` (HorizonInterpretation):

```python
# Query against RDDMS objects in the same dataspace
twt_counterpart = [
    rep for rep in grid2d_representations
    if rep.RepresentedObject.Uuid == depth_rep.RepresentedObject.Uuid
    and rep.LocalCrs.VerticalAxis.IsTime is True
]
# If found → resolve twt_counterpart.Uuid to OSDU SeismicHorizon WPC ID
# If not found → leave SeismicHorizonID empty (standalone depth surface)
```

### 12.6 Example: Reference JSON → OSDU StructureMap

Using `testHorizonEverythingIncluded.json` from `demo/seisint/references/` (a RESQML 2.2 JSON document with inline lattice geometry, depth CRS, and HorizonInterpretation):

**Input RESQML** (key properties):

```json
{
  "$type": "resqml22.Grid2dRepresentation",
  "Uuid": "030a82f6-10a7-4ecf-af03-54749e098624",
  "Citation": { "Title": "Horizon1 Interp1 Grid2dRep" },
  "RepresentedObject": {
    "Uuid": "ac12dc12-4951-459b-b585-90f48aa88a5a",
    "QualifiedType": "resqml22.HorizonInterpretation"
  },
  "SurfaceRole": "map",
  "FastestAxisCount": 4, "SlowestAxisCount": 2,
  "Geometry": {
    "Points": {
      "SupportingGeometry": {
        "$type": "resqml22.Point3dLatticeArray",
        "Origin": { "Coordinate1": 5010, "Coordinate2": 6020, "Coordinate3": 0 },
        "Dimension": [
          { "Direction": { "Coordinate1": 0, "Coordinate2": 0, "Coordinate3": 1 }, "Spacing": { "Value": 200, "Count": 1 } },
          { "Direction": { "Coordinate1": 0, "Coordinate2": 1, "Coordinate3": 0 }, "Spacing": { "Value": 250, "Count": 3 } }
        ]
      }
    }
  }
}
```

**Output OSDU StructureMap** (generated by `gen_structuremap_from_resqml.py`):

```json
{
  "kind": "osdu:wks:work-product-component--StructureMap:1.0.0",
  "data": {
    "Name": "Horizon1 Interp1 Grid2dRep",
    "DomainTypeID": "dev:reference-data--DomainType:Depth:",
    "InterpretationID": "dev:work-product-component--HorizonInterpretation:76204e5b-...:1",
    "OriginEasting": 5010,
    "OriginNorthing": 6020,
    "BinWidthOnIaxis": 200,
    "BinWidthOnJaxis": 250,
    "MapGridBearingOfBinGridJaxis": 0.0,
    "NodeCountOnIAxis": 2,
    "NodeCountOnJAxis": 4,
    "TransformationMethod": 9666,
    "DDMSDatasets": [
      "eml://rddms-1/dataspace('maap/drogon')/resqml22.Grid2dRepresentation('030a82f6-...')"
    ]
  }
}
```

### 12.7 Reverse Direction: OSDU StructureMap → RESQML Storage

To store a new StructureMap in the RDDMS (e.g., from an interpretation application):

1. **Create RESQML objects**: `Grid2dRepresentation` + `LocalEngineeringCompoundCrs` (depth) + optional `HorizonInterpretation` + `BoundaryFeature`
2. **Map OSDU grid properties** to RESQML lattice geometry (see §12.3, reversed)
3. **Store Z values** in HDF5 (production) or inline XML array (small grids)
4. **Package as EPC** and upload via ETP to RDDMS
5. **Register the OSDU StructureMap** record pointing to the RDDMS object via `DDMSDatasets[]`

The key transformations for OSDU → RESQML direction:

| OSDU Property | RESQML Construction |
|---|---|
| `OriginEasting/Northing` | `Point3dLatticeArray.Origin.Coordinate1/2` |
| `MapGridBearingOfBinGridJaxis` | J-axis `Direction = (sin(bearing), cos(bearing), 0)` |
| `TransformationMethod` 9666 | I-axis `Direction = (sin(bearing+90°), cos(bearing+90°), 0)` |
| `BinWidthOnI/Jaxis` | `Spacing.Value` |
| `NodeCountOnI/JAxis` | `Spacing.Count = NodeCount - 1`, `FastestAxisCount = NodeCountI`, `SlowestAxisCount = NodeCountJ` |
| `DomainTypeID` = Depth | `LocalCrs.VerticalAxis.IsTime = false`, `Uom = "m"` |
| `SeismicHorizonID` | `ExtraMetadata: osdu:SeismicHorizonID` |
| `BinGridID` | `SupportingRepresentation` reference to bin grid Grid2dRepresentation |

### 12.8 Demo Script

The bidirectional mapping is implemented in [`demo/seisint/gen_structuremap_from_resqml.py`](../demo/seisint/gen_structuremap_from_resqml.py):

```bash
# RESQML → OSDU (from test JSON)
python gen_structuremap_from_resqml.py --from-resqml references/testHorizonEverythingIncluded.json

# OSDU → RESQML (from Volantis manifest)
python gen_structuremap_from_resqml.py --from-osdu manifest_volantis_interp.json

# Round-trip demo
python gen_structuremap_from_resqml.py --round-trip
```

Outputs:
- `structuremap_from_resqml.json` — OSDU StructureMap(s) generated from RESQML
- `resqml_from_structuremap.json` — RESQML document generated from OSDU StructureMaps
- `resqml_roundtrip.json` — Round-trip verification

### 12.9 What's Actually in the RDDMS — Live Data from `maap/drogon`

The OSDU StructureMap record is intentionally a **thin catalog entry** — it tells you *what* the surface is and *where* the data lives, but it does **not** contain the depth or time values.  To actually render a depth map, you must follow the `DDMSDatasets[]` URI into the RDDMS.  Here is what you find there (real data from the `maap/drogon` dataspace):

#### RDDMS Grid2dRepresentation — TopVolantis Depth

```
GET /api/reservoir-ddms/v2/dataspaces/maap%2Fdrogon/resources/
    resqml20.obj_Grid2dRepresentation/f857c36c-3939-4ff3-9125-a11cf2af105c
```

```json
{
  "$type": "resqml20.obj_Grid2dRepresentation",
  "Uuid": "f857c36c-3939-4ff3-9125-a11cf2af105c",
  "Citation": { "Title": "TopVolantis", "Originator": "dalsaab", "Format": "Aspen SKUA V15" },
  "Grid2dPatch": {
    "FastestAxisCount": 432,
    "SlowestAxisCount": 489,
    "Geometry": {
      "Points": {
        "SupportingGeometry": {
          "Origin": { "Coordinate1": 6421.15, "Coordinate2": -3119.98, "Coordinate3": 0 },
          "Offset": [
            { "Offset": { "Coordinate1": 0, "Coordinate2": 1, "Coordinate3": 0 },
              "Spacing": { "Value": 25, "Count": 489 } },
            { "Offset": { "Coordinate1": 1, "Coordinate2": 0, "Coordinate3": 0 },
              "Spacing": { "Value": 25, "Count": 432 } }
          ]
        },
        "ZValues": { "Values": { "PathInHdfFile": "/RESQML/f857c36c-.../points_patch0", "HdfProxy": "..." } }
      },
      "LocalCrs": {
        "UUID": "0272f9c3-648c-40bd-aa3d-85b922d2e9f0",
        "ContentType": "application/x-resqml+xml;version=2.0;type=obj_LocalDepth3dCrs",
        "_data": {
          "VerticalUom": "m",
          "ProjectedUom": "m",
          "XOffset": 450000,
          "YOffset": 5930000
        }
      }
    }
  },
  "RepresentedInterpretation": {
    "UUID": "68b2675f-0a6a-48a8-a3bb-39c4aad3b58a",
    "ContentType": "...obj_HorizonInterpretation"
  }
}
```

#### Z-Value Array — The Actual Depth Data

```
GET /api/reservoir-ddms/v2/dataspaces/maap%2Fdrogon/resources/
    resqml20.obj_Grid2dRepresentation/f857c36c-.../arrays/
    %2FRESQML%2Ff857c36c-...%2Fpoints_patch0
```

Returns **211,248 depth values** (489 × 432 nodes) stored as HDF5 — this is the Z-value array:

| Statistic | Value |
|---|---|
| Total nodes | 211,248 |
| Min depth | 1,560.85 m |
| Max depth | 1,935.68 m |
| Mean depth | 1,717.38 m |
| Std dev | 59.75 m |
| First 5 values | `[1669.63, 1669.62, 1669.62, 1669.62, 1669.62]` |

#### How Grid Coordinates Map to Real-World XY

The RDDMS lattice uses a **local CRS** with offsets.  To get projected coordinates:

```
Projected X = LocalCrs.XOffset + Origin.Coordinate1 + (i × Offset[fast].Spacing.Value × Offset[fast].Direction.Coordinate1)
Projected Y = LocalCrs.YOffset + Origin.Coordinate2 + (j × Offset[slow].Spacing.Value × Offset[slow].Direction.Coordinate2)
```

For TopVolantis: origin in projected space = **(456421 E, 5926880 N)** in ED50/UTM31N, matching the OSDU `OriginEasting/Northing`.

#### End-to-End: OSDU Catalog → RDDMS → Depth Map

This is the complete data retrieval flow that a viewer application follows:

```mermaid
sequenceDiagram
    participant App as Viewer App
    participant Search as OSDU Search
    participant Storage as OSDU Storage
    participant RDDMS as Reservoir DDMS

    App->>Search: Find depth maps for "TopVolantis"
    Search-->>App: StructureMap record ID
    App->>Storage: GET record by ID
    Storage-->>App: StructureMap record (Name, grid params, DDMSDatasets[])

    Note over App: Record has grid geometry<br/>for spatial discovery,<br/>but no Z-values

    App->>RDDMS: GET Grid2dRepresentation<br/>from DDMSDatasets[] URI
    RDDMS-->>App: Grid metadata (lattice, CRS, offsets)
    App->>RDDMS: GET /arrays/{path}
    RDDMS-->>App: 211,248 depth values (float[])

    Note over App: Now has:<br/>• Grid geometry (from OSDU or RDDMS)<br/>• CRS + offsets → real-world XY<br/>• Z-values → depth at each node<br/>→ Can render depth map
```

The demo script **`peek_rddms_grid2d.py`** (§13) performs exactly these steps and displays the result.

---

## 13) Demo Implementation — Volantis Worked Example

Working example records and scripts are in [`demo/seisint/`](../demo/seisint/):

| File | Description |
|---|---|
| `_shared.py` | Shared helpers: deterministic UUIDs, ID builders, grid geometry, ABCD corners |
| `gen_volantis_interp.py` | Python generator script — produces the full manifest |
| `gen_structuremap_from_resqml.py` | Bidirectional RESQML ↔ OSDU StructureMap mapping (§12) |
| `manifest_volantis_interp.json` | Complete worked example: full chain for a Volantis interpretation |
| `ingest_records_seisint.py` | Sequential Storage API ingestion with retry logic |
| `manifest2records_seisint.py` | Splits manifest into individual record files for ingestion |
| `register_m27_schemas.py` | Registers M27 JSON Schema definitions with the Schema Service |
| `resolve_schemas.py` | Resolves `$ref` links in downloaded schemas → platform-ready format |
| `schema_seismicinterpretationproject.json` | SeismicInterpretationProject:1.0.0 schema (supplementary proposal) |
| `schemas/` | M27 JSON Schema definitions (downloaded + resolved for Schema Service) |
| `peek_rddms_grid2d.py` | Fetch & display RDDMS Grid2dRepresentations — shows the Z-value data behind DDMSDatasets[] (§12.9) |
| `references/` | Test horizon JSONs, discussion docs from OSDU GitLab |

### 13.1 Scenario

The manifest demonstrates the **Volantis 2025 Interpretation** — a consistent end-to-end demo using the Volantis field (Norwegian Sea). All `DDMSDatasets[]` links point to **real** RESQML `Grid2dRepresentation` objects stored in the Reservoir DDMS dataspace **`maap/drogon`** (exported from Aspen SKUA).

| Layer | Records | Schema |
|---|---|---|
| Features | 3 (Top Volantis, Base Volantis, Top Therys) | `LocalBoundaryFeature:1.1.0` |
| Interpretations | 3 horizon interpretations | `HorizonInterpretation:1.2.0` |
| Seismic grid | 1 (Volantis3D, 12.5m × 12.5m) | `SeismicBinGrid:1.3.0` |
| Depth grids | 2 (Volantis 25 m, Therys 20 m) | **`GenericBinGrid:1.0.0`** (M27) |
| TWT picks | 3 horizons | `SeismicHorizon:2.1.0` |
| Depth surfaces — Pattern B | 3 (TopVolantis, BaseVolantis — shared 25 m grid; TopTherys — own 20 m grid) | **`StructureMap:1.0.0`** (M27) |
| Depth surfaces — Pattern A | 2 (TopVolantis, BaseVolantis — inline 25 m grid) | **`StructureMap:1.0.0`** (M27) |
| Project grouping | 1 | **`SeismicInterpretationProject:1.0.0`** (proposal) |
| **Total** | **18 records** | |

### 13.2 RDDMS Data Source — `maap/drogon`

The demo references real RESQML objects living in the `maap/drogon` RDDMS dataspace. SeismicHorizon and StructureMap records are **pure catalog/metadata** — they carry no Z-value arrays themselves, only descriptive properties and a `DDMSDatasets[]` link to the RDDMS where the actual depth/time data lives (see §12.9 for the concrete RDDMS content).  To reconstruct the actual depth map you must follow the `DDMSDatasets[]` URI, fetch the Grid2dRepresentation metadata + Z-value array, and apply the CRS offsets.  The `peek_rddms_grid2d.py` script demonstrates this workflow end-to-end.

RDDMS data source mapping:

| OSDU Record | Domain | RDDMS Grid2dRep UUID | RDDMS Name | CRS |
|---|---|---|---|---|
| TopVolantis TWT | Time | `9deb9074-c4eb-44ff-990a-229bb545d442` | TS_interp | `LocalTime3dCrs` |
| BaseVolantis TWT | Time | `efcf91f9-6e56-4bed-9e23-f0e9350a0b91` | TS_interp | `LocalTime3dCrs` |
| TopTherys TWT | Time | — (no RDDMS object) | — | — |
| TopVolantis Depth Map | Depth | `f857c36c-3939-4ff3-9125-a11cf2af105c` | TopVolantis | `LocalDepth3dCrs` |
| BaseVolantis Depth Map | Depth | `0c6ab8e7-c793-4ab5-a88c-ccf457d9266d` | BaseVolantis | `LocalDepth3dCrs` |
| TopVolantis Pattern A | Depth | `f857c36c-3939-4ff3-9125-a11cf2af105c` | (same) | `LocalDepth3dCrs` |
| BaseVolantis Pattern A | Depth | `0c6ab8e7-c793-4ab5-a88c-ccf457d9266d` | (same) | `LocalDepth3dCrs` |
| TopTherys Depth Map | Depth | `0ce9278d-979c-450a-a3db-08ea96517463` | DS_extract_postprocess | `LocalDepth3dCrs` |

The RDDMS dataspace `maap/drogon` contains **51 Grid2dRepresentation** objects total (depth + time surfaces from the Drogon FMU workflow). Pattern A and Pattern B records for the same horizon point to the **same** RDDMS object — they differ only in how the OSDU catalog record describes the grid.

TopTherys has a depth surface in RDDMS (`DS_extract_postprocess`, 550 × 350 nodes, 20 m spacing, bearing ≈ 150°) but **no TWT counterpart** — the TopTherys SeismicHorizon TWT record therefore omits `DDMSDatasets[]`.

### 13.3 Grid Strategy Comparison — Pattern A vs Pattern B

The demo ingests both patterns **for the same horizons** (TopVolantis and BaseVolantis), making direct comparison possible:

#### Records Ingested

| StructureMap Record | Grid Pattern | BinGridID | Inline Grid Props | ancestry.parents |
|---|---|---|---|---|
| TopVolantis Depth Map (shared grid ref) | **B — external ref** | → GenericBinGrid (25 m) | ✗ empty | 4 (SH + HI + GBG + BF) |
| BaseVolantis Depth Map (shared grid ref) | **B — external ref** | → GenericBinGrid (25 m) | ✗ empty | 4 (SH + HI + GBG + BF) |
| TopTherys Depth Map (own 20 m grid) | **B — external ref** | → GenericBinGrid (20 m) | ✗ empty | 4 (SH + HI + GBG + BF) |
| TopVolantis Depth Map (inline 25 m grid) | **A — inline lattice** | ✗ empty | ✓ same 25 m grid as GenericBinGrid, embedded | 3 (SH + HI + BF) |
| BaseVolantis Depth Map (inline 25 m grid) | **A — inline lattice** | ✗ empty | ✓ same 25 m grid as GenericBinGrid, embedded | 3 (SH + HI + BF) |

#### Pattern A: Inline Grid (RESQML `Point3dLatticeArray`)

```
StructureMap                    (OSDU catalog — metadata only)
  ├── InterpretationID  → HorizonInterpretation
  ├── SeismicHorizonID  → SeismicHorizon (TWT)
  ├── OriginEasting:     461000.0
  ├── OriginNorthing:    6782000.0
  ├── BinWidthOnIaxis:   25.0
  ├── BinWidthOnJaxis:   25.0
  ├── NodeCountOnIAxis:  300
  ├── NodeCountOnJAxis:  200
  └── DDMSDatasets[]    → eml://rddms-1/dataspace('maap/drogon')/...Grid2dRep('{uuid}')
                          ^^^^ actual Z-values live here in the RDDMS
```

**Grid geometry is embedded on the StructureMap record as metadata.** No separate BinGrid record needed. The RDDMS Grid2dRepresentation holds the actual data. The RESQML counterpart uses `Point3dLatticeArray` with inline origin and direction vectors.

#### Pattern B: External BinGrid Reference (RESQML `SupportingRepresentation`)

```
StructureMap                    (OSDU catalog — metadata only)
  ├── InterpretationID  → HorizonInterpretation
  ├── SeismicHorizonID  → SeismicHorizon (TWT)
  ├── BinGridID         → GenericBinGrid:1.0.0  (carries grid geometry metadata)
  └── DDMSDatasets[]    → eml://rddms-1/dataspace('maap/drogon')/...Grid2dRep('{uuid}')
                          ^^^^ actual Z-values live here in the RDDMS

GenericBinGrid (shared metadata, referenced by multiple StructureMaps)
  ├── OriginEasting:     461000.0
  ├── BinWidthOnIaxis:   25.0
  ├── NodeCountOnIAxis:  300
  └── ...
```

**Grid geometry metadata lives on a separate GenericBinGrid record.** Multiple StructureMaps can reference the same grid. The RESQML counterpart uses `SupportingRepresentation` pointing to a shared `Grid2dRepresentation`.

#### Comparison

| Criterion | Pattern A (inline) | Pattern B (external BinGridID) |
|---|---|---|
| **Self-contained** | ✓ One record has everything | ✗ Requires BinGrid record to exist |
| **Grid reuse** | ✗ Grid duplicated on each record | ✓ One grid, many surfaces |
| **Record count** | Fewer (no separate BinGrid) | More (+1 GenericBinGrid per shared grid) |
| **Search by grid** | Must compare grid params field-by-field | `BinGridID` gives exact grid identity |
| **Consistency** | Risk of drift if grid params copied | Single source of truth |
| **RESQML mapping** | `Point3dLatticeArray` — direct | `SupportingRepresentation` — UUID resolution needed |
| **When to use** | Surface has unique grid, or one-off export | Multiple surfaces share acquisition/depth grid |

#### Recommendation

Use **Pattern B** (external BinGridID) when surfaces share a common grid — typical for multi-horizon interpretation projects where all depth maps are on the same depth conversion grid. Use **Pattern A** (inline) for one-off surfaces or when the grid is unique to that surface (e.g., a different-resolution regional map).

The demo includes both patterns for TopVolantis and BaseVolantis specifically so that consumers can see the structural difference side-by-side. TopTherys uses Pattern B with its **own** GenericBinGrid (20 m, 550 × 350 nodes, bearing ≈ 150°) — demonstrating that Pattern B works equally well for surfaces with unique grids, not only shared ones.

### 13.4 Relationship Chain — What Gets Indexed

Every record carries `data.ancestry.parents[]`, making the full provenance chain visible to OSDU Search:

```mermaid
flowchart TD
    BF1["LocalBoundaryFeature<br/>Top Volantis"]
    BF2["LocalBoundaryFeature<br/>Base Volantis"]
    HI1["HorizonInterpretation<br/>TopVolantis"]
    HI2["HorizonInterpretation<br/>BaseVolantis"]
    SBG["SeismicBinGrid<br/>Volantis3D"]
    GBG["GenericBinGrid<br/>Volantis Depth 25m"]
    GBG2["GenericBinGrid<br/>Therys Depth 20m"]
    SH1["SeismicHorizon<br/>TopVolantis TWT"]
    SH2["SeismicHorizon<br/>BaseVolantis TWT"]
    SH3["SeismicHorizon<br/>TopTherys TWT"]
    SMB1["StructureMap<br/>TopVolantis Depth<br/>(Pattern B)"]
    SMB3["StructureMap<br/>TopTherys Depth<br/>(Pattern B, own grid)"]
    SMA1["StructureMap<br/>TopVolantis Depth<br/>(Pattern A)"]
    PROJ["SeismicInterpretation<br/>Project"]

    BF1 --> HI1
    BF2 --> HI2
    HI1 --> SH1
    HI2 --> SH2
    SBG --> SH1
    SBG --> SH2
    SBG --> SH3
    SH1 --> SMB1
    HI1 --> SMB1
    GBG --> SMB1
    SH3 --> SMB3
    GBG2 --> SMB3
    SH1 --> SMA1
    HI1 --> SMA1
    SMB1 --> PROJ
    SMB3 --> PROJ
    SMA1 --> PROJ
    GBG --> PROJ
    GBG2 --> PROJ
    SBG --> PROJ
```

### 13.5 Running the Demo

```bash
cd demo/seisint

# 1. Register M27 schemas (one-time — safe to re-run)
python register_m27_schemas.py
python register_schema_seisintproject.py

# 2. Generate manifest (18 records)
python gen_volantis_interp.py

# 3. Split into individual record files
python manifest2records_seisint.py

# 4. Ingest to OSDU (sequential with 3s delay for indexing)
python ingest_records_seisint.py --env-file ../../.env --delay 3

# 5. Verify (dry-run shows all records without sending)
python ingest_records_seisint.py --dry-run

# 6. Peek at the RDDMS data behind DDMSDatasets[] (§12.9)
python peek_rddms_grid2d.py                     # all 5 demo surfaces
python peek_rddms_grid2d.py --no-zvalues         # metadata only (faster)
python peek_rddms_grid2d.py --list                # list all Grid2dReps in dataspace
```

Output: `manifest_volantis_interp.json` — a complete OSDU manifest ready for ingestion via the Storage Service.

### 13.6 ORES Web App — Live StructureMap Generation

The ORES web app provides live StructureMap:1.0.0 generation from RDDMS content
via three REST endpoints. The implementation lives in two modules:

| Module | Purpose |
|---|---|
| [`app/structuremap.py`](../app/structuremap.py) | Reusable conversion logic: discover Grid2d surfaces, classify depth vs time, generate StructureMap:1.0.0 records |
| [`app/keys_router.py`](../app/keys_router.py) | FastAPI endpoints that expose the conversion over HTTP |

#### Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/keys/structuremaps/surfaces.json?ds=maap/drogon` | List & classify all Grid2dRepresentations (depth vs time) |
| `GET` | `/keys/structuremaps.json?ds=maap/drogon&prefix=dev` | Generate StructureMap:1.0.0 records for all depth surfaces |
| `POST` | `/dataspaces/manifest/structuremaps` | Build full M27 manifest from selected or all depth surfaces |

#### Pipeline Flow

```mermaid
flowchart LR
    subgraph RDDMS["RDDMS REST API"]
        L["list Grid2dReps"]
        F["fetch geometry + CRS + z"]
    end

    subgraph ORES["ORES structuremap module"]
        D["discover_surfaces()\nclassify by CRS"]
        S["surface_to_structuremap()\nbearing, width, ABCD corners\nDDMSDatasets URI"]
    end

    subgraph Catalog["OSDU Catalog"]
        I["Ingest via\nStorage Service"]
    end

    L --> D
    F --> S
    D --> S
    S --> I
```

#### Example: Generate manifest for maap/drogon

```bash
# 1. List surfaces (lightweight — no z-value fetch)
curl "$ORES_URL/keys/structuremaps/surfaces.json?ds=maap/drogon"

# 2. Generate StructureMap records for all depth surfaces
curl "$ORES_URL/keys/structuremaps.json?ds=maap/drogon&prefix=dev"

# 3. Build manifest for specific surfaces only
curl -X POST "$ORES_URL/dataspaces/manifest/structuremaps" \
  -H "Content-Type: application/json" \
  -d '{"ds": "maap/drogon", "uuids": ["aabb...", "ccdd..."]}'
```

#### Key Design Decisions

1. **Reuses `osdu.fetch_grid2d_surface()`** — same RDDMS REST calls as the existing map rendering
2. **CRS-based classification** — `LocalDepth3dCrs` → depth → StructureMap; `LocalTime3dCrs` → time → skipped
3. **Bearing/width from offset vectors** — RESQML 2.0.1 lattice `Offset[]` → compass bearing + bin width
4. **Deterministic IDs** — UUID5 from RDDMS UUID ensures same input always produces same OSDU record
5. **DDMSDatasets link** — every StructureMap links back to its RDDMS source via EML URI

---

## 14) Community Context & Open Questions

### 14.1 Key Decisions (from 2026 Meeting Minutes)

| Date | Decision |
|---|---|
| 2026-02-16 | **StructureMap, AbstractGenericBinGrid, GenericBinGrid approved for M27** |
| 2026-02-16 | **HorizonControlPoints approved for M27** with AbstractColumnBasedTable for tabular data |
| 2026-02-09 | SeismicHorizon:2.1.0 adds `HorizonControlPointsID` link |
| 2026-01-26 | Oslo F2F workshop dates confirmed: April 13–17, 2026 |

### 14.2 SeismicSurfaceGeneration Activity Template

[Issue #863](https://gitlab.opengroup.org/osdu/data/data-definitions/-/issues/863) tracks the creation of a `SeismicSurfaceGeneration` activity template on branch 822. This template defines the seed-to-surface workflow:

- **Inputs**: SeismicTraceData, SeismicBinGrid/GenericBinGrid, HorizonControlPoints, VelocityModel
- **Outputs**: SeismicHorizon, StructureMap
- **Parameters**: Grid parameterization, algorithm selection, domain type

When approved, it will provide standardized Activity records linking inputs to outputs — complementary to the schema-level references documented here.

### 14.3 Oslo F2F Workshop (April 2026)

The Oslo F2F (April 13–17, 2026) plans two MVP workshops:
- **MVP1**: Structure Map end-to-end demonstration (the [ddm_mvp1_structuremap](https://gitlab.opengroup.org/osdu/subcommittees/data-def/projects/seismic/ddm_mvp1_structuremap) repo)
- **MVP2**: Expanded scope (horizons + faults + activities)

Our Volantis worked example is positioned as a contribution to MVP1.

### 14.4 Open Questions

| Question | Status | Notes |
|---|---|---|
| Should StructureMap carry `Interpreter` / `Remark[]`? | Open | SeismicHorizon has them; StructureMap relies on inherited AuthorIDs[] or ExtensionProperties |
| Multi-Z surfaces (structure map as 2D multi-z) | Deferred | Risk of duplicating what already exists in RESQML normalized model in the RDDMS |
| SeismicInterpretationProject as official schema | Not yet proposed | Our demo includes it; could be submitted after M27 |
| Generic Property WPCs for Z values | Under discussion | Properties on a StructureMap may be stored as separate GenericProperty WPCs |
| `VelocityModelID` not on any M27 schema | Open | No link from StructureMap to velocity model — add via ExtensionProperties now, propose for StructureMap:1.1.0 |
| `SeismicAttributeTypeID` ref-data missing | Parked | Useful for search ("show me all amplitude maps") but no ref-data defined yet |

### 14.5 Proposed Improvements for StructureMap:1.1.0

Properties present on SeismicHorizon but absent from StructureMap (`Interpreter`, `Remark[]`, `PetroleumSystemElementTypeID`) create search asymmetry. Recommend proposing `StructureMap:1.1.0` adding these as optional individual properties (non-breaking minor version bump).

### 14.6 Cross-Schema Consistency

Key consistency gaps worth tracking:
- `BinGridID` naming: Now unified across schemas — SeismicHorizon:2.1.0, StructureMap:1.0.0, and SeismicFault all use `BinGridID`
- `Interpreter` is on SeismicHorizon and SeismicFault but not StructureMap or HorizonControlPoints
- HorizonInterpretation's `BoundaryRelationTypeID` (single) vs RESQML's `BoundaryRelation[]` (array)

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

```mermaid
flowchart TD
    AR[AbstractRepresentation\nInterpretationID, CRS, DDMSDatasets]

    AR --> GR["GenericRepresentation\nRole (string), Type (string)\n→ 2 free-text properties"]
    AR --> SM["StructureMap\n+ AbstractGenericBinGrid (10 grid props)\n+ BinGridID, SeismicHorizonID,\n  DomainTypeID, ExtensionProperties\n→ 4 typed + 10 grid properties"]

    style GR fill:#fff3e0,stroke:#ff9800,color:#000
    style SM fill:#e8f5e9,stroke:#4caf50,color:#000
```

**What overlaps**: Both inherit `AbstractRepresentation` (InterpretationID, CRS, DDMSDatasets). This is by design — all OSDU representations share a common base. This is inheritance, not duplication.

**What does NOT overlap**:
- StructureMap adds `AbstractGenericBinGrid` (10 grid properties) — GenericRepresentation has nothing equivalent
- StructureMap adds `SeismicHorizonID` (typed provenance) — GenericRepresentation has no provenance link
- StructureMap adds `DomainTypeID` (ref-data search) — GenericRepresentation has no domain filtering
- GenericRepresentation's `Role`/`Type` are generic strings; StructureMap's type is implicit (always Regular2DGrid)

### 15.4 The HorizonInterpretation Angle

HorizonInterpretation is an **interpretation** (the "what"), not a **representation** (the "how"). The OSDU model explicitly separates these:

```mermaid
flowchart LR
    I["Interpretation (1)\n'Top Volantis'"]
    I --> R1["TWT SeismicHorizon"]
    I --> R2["Depth StructureMap"]
    I --> R3["TriangulatedSet GenericRep"]
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

> **Document version**: 4.0 — 2026-04-08
> **Authors**: ORES project team
> **Status**: Updated for M27 — streamlined with Mermaid diagrams; verbose inherited-property tables replaced by schema links
> **Previous versions**: 3.0 (catalog-vs-data concept), 2.0 (pre-M27 gap analysis), 1.0 (initial RESQML comparison)
