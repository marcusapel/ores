## **OSDU Support for FMU Data Handling – System of Record**

> **Reference links**:
> - [fmu-dataio](https://github.com/equinor/fmu-dataio) — FMU data standard & metadata export library (v2.26, schema v0.21.0)
> - [fmu-dataio data model](https://fmu-dataio.readthedocs.io/en/latest/datamodel/index.html) — FMU results metadata schema (denormalized, parent/child)
> - [fmu-dataio simple exports](https://fmu-dataio.readthedocs.io/en/latest/simple_exports/index.html) — Standard result export functions
> - [fmu-sumo](https://github.com/equinor/fmu-sumo) — Interaction with Sumo (current SoR for FMU results)
> - [fmu-drogon](https://github.com/equinor/fmu-drogon) — Public Drogon reference case
> - [ERT](https://github.com/equinor/ert) — Ensemble-based Reservoir Tool (workflow orchestrator)
> - [Sumo / Webviz](https://github.com/equinor) — Cloud SoR, visualization, and aggregation
> - **Internal**: [Reservoir Modelling & Simulation Wiki](https://statoilsrm.sharepoint.com/sites/SubsurfaceCommunityHUB2/SitePages/Reservoir-Modelling-and-Simulation.aspx) — Equinor FMU practice, templates, and governance
>
> **Related guides in this repo**: [BusinessDecision](BusinessDecision.md) · [Volumes](Volumes.md) · [Uncertainty](Uncertainty.md) · [Risk](Risk.md) · [BdDemo](BdDemo.md) · [DevConcept](DevConcept.md) · [SeisInt](SeisInt.md) · [StratColumn](StratColumn.md)

***

### **1. Purpose**

This document describes how **OSDU** can serve as a **structured data management layer** for **FMU** (Fast Model Update) workflows — both as a persistent System of Record and as an enabler for better input provisioning, output management, and decision support across decision gates (DG1→DG4).

Three complementary objectives:

1. **System of Record** — OSDU as persistent SoR for FMU results, complementing/replacing Sumo. Structured, governed, version-controlled storage with OSDU data model semantics.
2. **Input provisioning** — OSDU as an organized source of input data for FMU workflows: master data (reservoir, segments, stratigraphy), reference data (fluid contacts, uncertainties), surfaces, grids, well data.
3. **Decision support** — OSDU `BusinessDecision` records as the backbone for DG1→DG4 tracking, linking ensemble results (volumes, maps, production profiles, uncertainties) to gates with full provenance.

Additionally, enable round-trip fidelity between FMU Eclipse/OPM decks and OSDU Reservoir DMS:
* Lossless, in-memory RESQML IJK grid and property handling (`resqpy`)
* Efficient, file-less data transfer (`pyetp`)
* Metadata preservation end-to-end for traceability and reproducibility
* Bonus: Nexus, Intersect support via RESQML ETP API

### **1.1 Current FMU Data Landscape**

FMU is Equinor's primary system for creating, maintaining, and using 3D predictive numerical models for the subsurface. It combines off-the-shelf software (RMS, Eclipse/OPM) with in-house components (ERT, fmu-dataio). FMU data currently flows through the **Sumo** cloud storage platform as the primary System of Record:

1. **ERT** orchestrates the FMU workflow — defines cases, iterations/ensembles, realizations, and FORWARD_MODELs (RMS → Eclipse/OPM → post-processing)
2. **fmu-dataio** exports data from within FORWARD_MODELs with rich metadata sidecars (denormalized YAML/JSON, one per file). Standard results (simple exports) enforce column conventions and validation.
3. **Sumo** receives and indexes the exported data for querying, visualization (Webviz), and consumption by downstream clients
4. **OSDU** can complement or replace Sumo as the persistent SoR, with structured data management, Activity provenance, and BusinessDecision support

The fmu-dataio metadata schema (currently v0.21.0) defines a **denormalized parent/child data model**: `case → ensemble → realization → files`. Each exported file has a metadata sidecar containing:
- `fmu.case` — case identity (name, uuid, user, model template)
- `fmu.ensemble` — ensemble/iteration identity (name, uuid)
- `fmu.realization` — realization identity (id, uuid, is_reference)
- `fmu.ert` — ERT experiment context (experiment.id, simulation_mode)
- `data.content` — content type (volumes, surfaces, grids, tables, cubes, polygons, etc.)
- `data.standard_result` — standardized result name (e.g., `inplace_volumes`, `structure_depth_surface`, `grid_model_static`)
- `data.property` — property attribute and discreteness for grid properties
- `masterdata` — field/country references (SMDA)
- `access` — classification and security

**Standard results** (fmu-dataio v2.26 simple exports) are the recommended export path:

| Standard result | Export function | Output format |
|---|---|---|
| Initial inplace volumes | `export_inplace_volumes` | Parquet (FLUID, ZONE, REGION, FACIES, LICENSE, BULK, NET, PORV, HCPV, STOIIP, GIIP, ASSOCIATEDGAS, ASSOCIATEDOIL) |
| Structure depth surfaces | `export_structure_depth_surfaces` | irap_binary (.gri) per horizon |
| Structure time surfaces | `export_structure_time_surfaces` | irap_binary (.gri) per horizon |
| Structure depth isochores | `export_structure_depth_isochores` | irap_binary (.gri) |
| Structure depth fault lines | `export_structure_depth_fault_lines` | — |
| Structure depth fault surfaces | `export_structure_depth_fault_surfaces` | — |
| Grid extracted depth surfaces | `export_grid_extracted_depth_surfaces` | irap_binary (.gri) |
| Grid model static | `export_grid_model_static` | ROFF (.roff) — grid + properties (porosity, perm, Sw, facies, NTG, Vsh, bulk volumes, fluid indicator, zonation, regions) |
| Field outline | `export_field_outline` | — |
| Fluid contact outlines | `export_fluid_contact_outlines` | — |
| Fluid contact surfaces | `export_fluid_contact_surfaces` | — |
| Simulator FIP regions mapping | `export_simulator_fipregions_mapping` | — |

Custom exports cover additional content types: `well_completions`, `production_network`, `pvt`, `relperm`, `rft`, `timeseries`, `lift_curves`, `observations`, `fault_surface`, `seismic`, `fluid_contact`, `field_outline`, `mapping`, and more.

***

### **2. Ground Rules**

* **No breaking changes to FMU** workflow design and functionality, governance, ERT and other component roles: focus is on data handling, storage, metadata support, query.
* **Respect fmu-dataio as the metadata standard**: All FMU exports use fmu-dataio for metadata generation. OSDU mapping must preserve the fmu-dataio metadata structure and be able to reconstruct it on round-trip.
* **One identity per artifact**: Each grid, property, map, and deck has a stable `UUID/SRN` and `version`.
* **Lossless provenance**: Every output carries ancestry back to the exact input WPCs and FMU run.
* **CRS & units are first-class**: CRS definition, axis order, rotation, and UOM travel with the data.
* **Round-trip fidelity**: Data exported from Eclipse can be fully recovered from OSDU with identical identity and metadata.
* **Gate alignment**: OSDU data model usage must support the decision-gate lifecycle (DG1→DG4) with increasing data richness at each stage.

***

### **3. Canonical Data Model — FMU ↔ OSDU Mapping**

The FMU data model (fmu-dataio) is denormalized and file-centric. The OSDU data model is normalized and record-centric. The mapping between them:

| FMU concept (fmu-dataio) | OSDU concept | Notes |
|---|---|---|
| `fmu.case` (name, uuid, model) | **WorkProduct** or **Dataspace** | Case = versioned package + partition boundary for ACL/legal |
| `fmu.ensemble` (name, uuid) | **WorkProduct** or **PersistedCollection** | Ensemble package, one per iteration |
| `fmu.realization` (id, uuid) | Key column in WPC tables, or per-realization WPC | Realization index as key in REV/CBT, or separate WPCs for large artifacts |
| `data.content = volumes` | `ReservoirEstimatedVolumes` WPC | Standard result: `inplace_volumes` |
| `data.content = surface` | `StructureMap` / `GenericRepresentation` WPC | Depth/time surfaces, isochores, fault surfaces |
| `data.content = property` | Grid Property WPC (`IjkGridRepresentation`) | PORO, PERMX, SW, NTG, facies, etc. |
| `data.content = grid` | `IjkGridRepresentation` WPC | Static grid model geometry |
| `data.content = table` | `ColumnBasedTable` WPC | Design matrix, timeseries, production profiles, simulator tables |
| `data.content = polygons` | `GenericRepresentation` WPC | Field outlines, fault lines, fluid contact outlines |
| `data.content = seismic` | Seismic WPCs | Cubes, attribute maps |
| `data.standard_result.name` | WPC kind + PropertyTypeID | Canonical column/naming conventions |
| `masterdata.smda.field` | `master-data--Reservoir` | |
| `masterdata.smda.country` | `master-data--Country` | |
| `fmu.ert.experiment` | `Activity` / `ActivityTemplate` | ERT experiment → OSDU Activity provenance |
| Design matrix (ERT parameters) | `ColumnBasedTable` WPC | Keys: CaseID, Realisation, Seed; Columns: parameter vector |
| Aggregated statistics | `ReservoirEstimatedVolumes` with FacetIDs | P10/P50/P90/Mean via `FacetType:statistics` + `FacetRole` |

#### OSDU types used

| OSDU type | Role in FMU context |
|---|---|
| **Dataspace** | Partition boundary for ACL and legal tags |
| **WorkProduct** | Versioned case/ensemble package — groups WPCs into a deliverable |
| **Work Product Component (WPC)** | Atomic datasets: grids, properties, maps, tables, documents, volumes |
| **PersistedCollection** | Evidence package for a gate — curated set of WPCs |
| **Activity / ActivityTemplate** | Workflow provenance — links inputs, outputs, and context with `Parameters[]` |
| **BusinessDecision** | Decision gate record — DG1→DG4 with risks, approvals, evidence links |
| **Reservoir / ReservoirSegment** | Master-data anchors for volumes scoping |
| **GeoLabelSet** | Headline KPI labels (P10/P50/P90 per segment) for dashboards |
| **Document** | SRA, CRA, PDO, PTR — governance documents linked to BD |
| **Risk** | Risk records with severity/probability, linked to BD |

***

#### **Grid (IJK, Corner-Point)**

* **Identity**: `grid_uuid`, `osdu_srn`, `version`
* **Geometry**: `ni, nj, nk`, `k_direction`, `handedness`
* **CRS**: Type (LocalDepth3d/Global), origin, rotation, axis order, units (XY/Z)
* **Governance**: `legalTags`, `acl`, `data.ancestry.inputs`, timestamps
* **Standard result**: `grid_model_static` — exports grid + standard properties as ROFF

#### **Property (Cell-Sized)**

* **Identity**: `property_uuid`, `osdu_srn`, `version`
* **Mapping**: Eclipse keyword (PORO, PERMX, SW, NTG, FACIES, etc.), indexable element (cells), UOM, discrete/continuous
* **Ties**: `supported_by_uuid` (grid UUID), property set/title
* **Standard properties** (from `export_grid_model_static`): zonation, regions, porosity, permeability, saturation_water, fluid_indicator, bulk_volume_oil/gas, facies, net_to_gross, volume_shale, permeability_vertical

#### **2D Grid / Surface / Map**

* Identity + grid reference (surface grid or parent 3D grid + layer/slice), units, CRS
* Standard results: `structure_depth_surface`, `structure_time_surface`, `structure_depth_isochore`, `grid_extracted_depth_surface`
* OSDU: `StructureMap` WPC (RDDMS Grid2dRepresentation for Z-values) or `GenericRepresentation`

#### **Table (CSV/Parquet)**

* Identity + schema (columns & UOM), run scope (case/realization/time)
* Standard results: `inplace_volumes` (Parquet), simulator tables (relperm, pvt, rft, well_completions, timeseries, lift_curves, production_network)
* OSDU: `ColumnBasedTable` WPC with KeyColumns/Columns schema

#### **Deck Artifacts (Eclipse/OPM)**

* **Identity**: `deck_id` (stable identifier for produced deck bundle)
* **Components**: `GRID.grdecl`, `PORO.grdecl`, `.DATA/.EGRID`
* **Binding**: `grid_uuid` + list of `property_uuid`s
* **Manifest**: JSON sidecar stored on disk and as metadata/attachment on Deck WPC

***

### **4. BusinessDecision Alignment with FMU Gates**

FMU is used from DG1 onwards in Equinor's capital value process. Each decision gate has increasing data requirements. The OSDU `BusinessDecision` record serves as the **hub** linking all gate evidence.

#### 4.1 Gate data progression

| Gate | FMU scope | Key OSDU artifacts |
|---|---|---|
| **DG1** — Identify & Assess | Screening: few realizations (3–10), simple design matrix, limited uncertainty variables, regional data | Reservoir, Segments, REV (raw + stats), input params CBT, 1–2 Risks, Activity, BD |
| **DG2** — Concept Select | Full ensemble: 50–200 realizations (Latin Hypercube), revised parameters, production forecast, development concept evaluation | All DG1 + more Risks, Documents (SRA, CRA, PDO), DevelopmentConcept WPC, GeoLabelSet, production forecast CBT, PersistedCollection, economics |
| **DG3** — FEED / Plan for Execution | Dynamic simulation: history matching (if brownfield), flow simulation grid (IJK), SCHEDULE, PVT, relperm, well trajectories, drainage strategy | All DG2 + IjkGridRepresentation, grid properties, WellboreTrajectory, simulator tables (relperm, PVT), ProductionValues, StructureMaps |
| **DG4** — FID / Execute | Full-field simulation & optimization: 100–1000+ realizations, history match quality, production optimization, detailed well plans | All DG3 + history match metrics, updated forecasts, field development plan, updated risks |

#### 4.2 BD as FMU evidence hub

The `BusinessDecision` record uses `Parameters[]` (from `AbstractProjectActivity`) with `ParameterRole = input|output|context` to link all gate evidence:

```
BusinessDecision (DG2 example)
  ├─ DecisionLevelID → reference-data--DecisionLevel:DG2
  ├─ ApprovalStatusID → reference-data--DecisionApprovalStatus:Approved
  ├─ RiskIDs → Risk records (porosity, fault, HSE, schedule, OWC, recovery)
  ├─ RiskAssessmentDocument → Document WPC (SRA)
  ├─ PriorActivityIDs → Activity (the workflow that produced evidence)
  ├─ Parameters[]:
  │    ├─ [input]  REV-raw → ReservoirEstimatedVolumes (per-realisation)
  │    ├─ [input]  REV-stats → ReservoirEstimatedVolumes (P10/P50/P90)
  │    ├─ [input]  InputParams → ColumnBasedTable (OWC + porosity design)
  │    ├─ [input]  GeoModelDataspace → ETPDataspace (RDDMS pointer)
  │    ├─ [output] ProductionForecast → ColumnBasedTable (20yr profile)
  │    ├─ [output] DevelopmentConcept → custom WPC
  │    ├─ [output] GeoLabelSet → headline P10/P50/P90 per segment
  │    ├─ [context] Reservoir → master-data--Reservoir
  │    ├─ [context] Prior gate → BD DG1 (cross-gate linkage)
  │    └─ [context] Documents → SRA, CRA, PDO, PTR
  ├─ Personnel[] → team members with ProjectRoleIDs
  ├─ ext.equinor.Alternatives[] → ranked concept alternatives
  └─ ext.equinor.UncertaintySummary → P10/P50/P90 range + method
```

#### 4.3 Adapting the BD model for FMU

**Current strengths:**
- `Parameters[]` with `ParameterRole` provides semantic input/output/context linking — well suited for FMU's input→workflow→output provenance
- `PriorActivityIDs` chains to the Activity record that represents the FMU workflow execution
- `RiskIDs` + governance documents capture the risk dimension required at each gate
- Cross-gate navigation (DG1→DG2→DG3→DG4) via `Parameters[]` back-references

**Improvements for better FMU alignment:**

1. **Standardize parameter keys**: Define a controlled vocabulary for `Parameters[].Title` / key strings used across gates. Current keys (`"REV-raw"`, `"DevelopmentConcept"`, `"GeoLabelSet"`, `"ProductionForecast"`) are ad-hoc. Propose a registry: `fmu-rev-raw`, `fmu-rev-stats`, `fmu-design-matrix`, `fmu-production-forecast`, `fmu-development-concept`, `fmu-geolabelset`, `fmu-geomodel-dataspace`.

2. **Ensemble metadata on BD**: The BD should carry ensemble summary metadata: number of realizations, sampling method (User_Defined / Latin_Hypercube / Monte_Carlo), number of uncertainty variables. Currently encoded as JSON strings in Activity parameters — consider promoting to `ext.equinor` keys or a dedicated WPC.

3. **Design matrix as first-class WPC**: Currently the design matrix is serialized as JSON in Activity `Parameters[]`. It should be a proper `ColumnBasedTable` WPC (keys: `CaseID`, `Realisation`, `Seed`; columns: parameter vector). This enables direct join/query against REV without JSON parsing. See [Uncertainty guide](Uncertainty.md).

4. **Economics WPC**: At DG2+ economics (NPV, CAPEX, OPEX, IRR, breakeven) are critical. Currently stored in `ext.equinor` which can be silently dropped by manifest ingestion. Consider a dedicated `ColumnBasedTable` WPC or a custom schema (like the DevelopmentConcept pattern) to ensure persistence.

5. **Gate comparison support**: OSDU queries should support cross-gate delta analytics — e.g., volumes at DG2 vs DG1, risk evolution, parameter refinement. The BD→BD back-reference chain enables this, but requires consistent parameter keys and segment mappings across gates.

***

### **5. OSDU Benefits for FMU Input and Output Management**

#### 5.1 Input provisioning — OSDU as FMU data source

OSDU can serve as a **governed, versioned source** of input data for FMU workflows, replacing or complementing ad-hoc file shares and SMDA lookups:

| FMU input need | OSDU source | Benefit |
|---|---|---|
| **Reservoir & segments** | `master-data--Reservoir`, `ReservoirSegment` | Canonical entity IDs; consistent scoping across gates |
| **Stratigraphy** | `StratigraphicUnitInterpretation`, `StratigraphicColumnRankInterpretation` | SMDA-aligned zones and horizons for FMU model template |
| **Structural surfaces** | `StructureMap` WPC (RDDMS + catalog) | Versioned depth/time surfaces; RDDMS streaming for large grids |
| **Fluid contacts** | `FluidBoundary` or `GenericRepresentation` WPC | OWC/GOC per segment — input to volume calculations |
| **Well data** | `WellboreTrajectory`, `WellLog`, `WellCompletionData` | Conditioning data for geomodels |
| **Seismic** | `SeismicHorizon`, `SeismicLineSet`, cubes | Velocity models, attribute maps |
| **Prior gate results** | `ReservoirEstimatedVolumes`, `ColumnBasedTable` | Previous ensemble results as baseline/comparison |
| **Reference data** | Units, CRS, facet types, property types | Governed catalogs for consistent metadata |

**Workflow pattern**: ERT pre-processing job queries OSDU for versioned inputs → fmu-dataio tags each export with `data.ancestry.inputs` pointing to OSDU WPC IDs → outputs carry full provenance back to governed input sources.

#### 5.2 Output management — ensemble results in OSDU

FMU produces large volumes of output across realizations. OSDU provides structured management for each category:

##### Volumes (ReservoirEstimatedVolumes)

Two flavours — see [Volumes guide](Volumes.md):

- **Raw per-realization**: Keys `Realisation/Zone/SegmentID/Facies`, columns `BULK/NET/PORV/HCPV/STOIIP/GIIP/ASSOCIATEDGAS/ASSOCIATEDOIL`. One REV WPC per ensemble with all realizations as rows.
- **Aggregated statistics**: Keys `Zone/SegmentID`, columns `Bulk.P10/Oil.P50/...` with `FacetIDs` carrying `FacetType:statistics` + `FacetRole:P10|P50|P90|ArithmeticMean|Minimum|Maximum|StandardDeviation`.

fmu-dataio column mapping: `BULK→Bulk`, `NET→Net`, `PORV→Pore`, `HCPV→HydrocarbonPore`, `STOIIP→Oil`, `GIIP→Gas`, `ASSOCIATEDGAS→AssociatedGas`, `REAL→Realisation` (key).

Note: fmu-dataio v2.26 adds `FLUID` as a standard index column (GAS/OIL/WATER), which should map to a `Fluid` key column in REV. The `LICENSE` column (optional) maps to a governance/scoping key.

##### Maps and surfaces

Structure depth surfaces, time surfaces, isochores, and grid-extracted surfaces are per-realization spatial data:

- **OSDU catalog**: `StructureMap` WPC or `GenericRepresentation` holding metadata (CRS, grid geometry, stratigraphic reference)
- **RDDMS storage**: `Grid2dRepresentation` in RESQML EPC for actual Z-values (streamed via ETP)
- **Ensemble handling**: Per-realization surfaces can be individual WPCs or bundled under a WorkProduct per ensemble. Aggregated surfaces (mean, P10, P90 maps) are separate WPCs with facet annotation.
- **Standard results**: `structure_depth_surface`, `structure_time_surface`, `structure_depth_isochore`

##### Production profiles

- **Forecast** (DG2+): `ColumnBasedTable` WPC with columns `Year/OilRate_Sm3d/GasRate_Sm3d/WaterRate_Sm3d/CumOil_MSm3`, optionally per realization
- **History** (DG3/DG4, brownfield): `ProductionValues` WPC for observed/historical data
- **Ensemble profiles**: Per-realization production tables enable P10/P50/P90 forecast uncertainty bands

##### Uncertainty parameters and design matrix

See [Uncertainty guide](Uncertainty.md):

- **Design matrix**: `ColumnBasedTable` WPC — keys `CaseID/Realisation/Seed`, columns represent parameter vector (e.g., `KxMultiplier`, `RelPermFamily`, `NTG_Shift`, OWC contacts per segment)
- **Provenance**: Activity `Parameters[]` link design matrix row → static bundle → raw REV output, joined on `Realisation` key
- **Variable metadata**: Number of variables, distributions, correlations, sampling method — stored in Activity or as metadata on the design matrix CBT

##### Simulator tables (DG3/DG4)

| fmu-dataio content | OSDU WPC type | Notes |
|---|---|---|
| `relperm` | `ColumnBasedTable` | Relative permeability curves per facies/SATNUM |
| `pvt` | `ColumnBasedTable` | PVT data per PVT region |
| `rft` | `ColumnBasedTable` | Repeat formation test data |
| `well_completions` | `ColumnBasedTable` | Well completion schedules |
| `timeseries` | `ColumnBasedTable` | Simulator summary vectors (FOPT, WBHP, etc.) |
| `lift_curves` | `ColumnBasedTable` | Artificial lift performance curves |
| `production_network` | `ColumnBasedTable` | Network model data |

#### 5.3 Ensemble modelling relationships in OSDU

The core challenge: FMU produces **N realizations × M artifact types** per ensemble. OSDU needs to represent the relationships:

```mermaid
erDiagram
  WorkProduct ||--o{ ReservoirEstimatedVolumes : "ensemble volumes"
  WorkProduct ||--o{ ColumnBasedTable : "design matrix and profiles"
  WorkProduct ||--o{ StructureMap : "per-realization surfaces"
  WorkProduct ||--o{ IjkGridRepresentation : "per-realization grids"
  Activity ||--o{ WorkProduct : "context"
  Activity ||--o{ ColumnBasedTable : "input"
  Activity ||--o{ ReservoirEstimatedVolumes : "output"
  ColumnBasedTable }o--|| ReservoirEstimatedVolumes : "realisation-join"
  BusinessDecision ||--o{ Activity : "PriorActivityIDs"
  BusinessDecision ||--o{ Risk : "RiskIDs"
  BusinessDecision ||--o{ ReservoirEstimatedVolumes : "Parameters evidence"
  Reservoir ||--o{ ReservoirSegment : "segments"
  ReservoirEstimatedVolumes }o--|| Reservoir : "ParentObjectID"
```

**Key patterns:**
1. **WorkProduct per ensemble** — groups all WPCs for one iteration (design matrix + static bundle + all output types)
2. **Realisation as key column** — not as separate WPC per artifact per realization (avoids record explosion for 200+ realizations)
3. **Activity as workflow record** — one Activity per ensemble execution, linking design matrix → static inputs → output WPCs
4. **BusinessDecision as gate record** — one BD per gate, linking Activities, Risks, Documents, and evidence WPCs via `Parameters[]`
5. **Cross-gate evolution** — BD at DG(n+1) references BD at DG(n) as context parameter, enabling delta tracking

***

### **6. Deck Manifest (Eclipse ⇄ OSDU Round-Trip)**

A small sidecar (JSON/YAML) accompanying every deck export and OSDU write-back:

* **Identity**: `deck_id`, `case`, `realization`
* **Grid**: `grid_uuid`, `osdu_srn`, `dims`, `crs`
* **Properties[]**: `property_uuid`, `title`, `ecl_keyword`, `uom`, `discrete`
* **Files**: List of produced artifacts (paths/names)
* **Ancestry Inputs**: `[<wpc-id-grid>, <wpc-id-poro>, …]`
* **Provenance**: Timestamp, tool version, conversion notes

> Stored both on disk and as metadata/attachment on the OSDU WPC.
> Enables Eclipse → OSDU → Eclipse identity continuity.

#### Round-trip identity rules

1. **Grid Lock** — Deck's grid always referenced by `grid_uuid`. Re-publishing increments `deck_id`; `grid_uuid` remains unless topology changes.
2. **Property Lock** — Each property retains original `property_uuid`, Eclipse keyword, and UOM. Renames in Eclipse don't overwrite OSDU identity.
3. **CRS/UOM Lock** — Manifest must include CRS type, origin/rotation, axis order, and UOM. Mismatches trigger validation warnings.
4. **Ancestry Chain** — All outputs must set `data.ancestry.inputs` to exact input WPC IDs. Collections capture ensemble structure.

***

### **7. Flow & Functionality**

* **Discovery**: Query OSDU for grid + properties by `supported_by_uuid`, surfaces by stratigraphic reference, volumes by reservoir/segment scope.
* **Conversion**: `resqpy` writes GRDECL/EGRID/ROFF using grid's CRS/UOM; `xtgeo`/`resdata` for ROFF/GRDECL/EGRID validation. Manifest records included items.
* **FMU Run**: Consumes deck as-is; metadata manifest ties run back to OSDU input WPC IDs.
* **Write-back**: Outputs become WPCs with ancestry to manifest inputs, linked into correct WorkProduct/Collection.
* **Round-trip**: Re-fetching Deck Artifact WPC + manifest allows deck reconstruction.
* **Aggregation**: Post-processing across realizations produces statistical summaries (P10/P50/P90) stored as faceted REV WPCs.

***

### **8. Minimal Responsibilities per Component**

| Component | Responsibility |
|---|---|
| **ERT** | Orchestrate FMU workflows — cases, ensembles, realizations, FORWARD_MODELs, design matrix. Owner of case definitions and experiment identity. |
| **fmu-dataio** | Export data with rich metadata (denormalized sidecar per file). Enforces FMU data standard (schema v0.21.0). Provides standard results for validated exports. |
| **Sumo (fmu-sumo)** | Current cloud SoR — receives exports, indexes metadata, serves queries for Webviz/clients. |
| **pyetp** | Discover WPCs (grid + properties), stream arrays, respect dataspace/ACL. |
| **resqpy** | Build in-memory RESQML model, convert to GRDECL/EGRID, emit Deck Manifest. |
| **xtgeo (resdata)** | ROFF/GRDECL/EGRID conversion/validation, surface I/O. |
| **OSDU** | Persist WPCs with `legalTags`, `acl`, `version`, `ancestry`, Collections. Structured query, Activity provenance, BusinessDecision support. |
| **FMU workflow** | Consume decks as-is; echo `deck_id` and realization in run metadata via fmu-dataio exports. |

***

### **9. Lightweight Acceptance**

* **Identity**: `grid_uuid` in manifest matches WPC; all `property_uuid`s present and correct.
* **CRS/UOM**: Manifest CRS fields complete; property UOMs consistent.
* **Ancestry**: Output WPCs list all inputs; Collection structure matches ensemble layout.
* **Round-trip**: Deck Artifact WPC + manifest sufficient to reconstruct runnable deck.
* **Gate linkage**: BD record has valid `DecisionLevelID`, `Parameters[]` link to evidence WPCs, `RiskIDs` populated, `PriorActivityIDs` chain to Activity.

***

### **10. TODO — Open Items and Next Steps**

#### 10.1 High priority

- [ ] **Automated fmu-dataio → OSDU converter**: Build a converter that reads fmu-dataio metadata sidecars (YAML/JSON) and produces OSDU manifests or Storage API payloads. This is the key enabler for production-scale FMU→OSDU sync. Evaluate as fmu-dataio plugin or standalone tool.
- [ ] **Design matrix as proper CBT WPC**: Implement the design matrix as a `ColumnBasedTable` WPC (keys: `CaseID/Realisation/Seed`, columns: parameter vector) instead of JSON strings in Activity parameters. Update demo generators.
- [ ] **Standardize BD parameter keys**: Define and document a controlled vocabulary for `Parameters[].Title` keys used in BusinessDecision records across gates. Publish as reference data or convention guide.
- [ ] **DG3/DG4 demo pipeline**: Extend the Drogon demo to DG3 (FEED) and DG4 (FID) with dynamic simulation artifacts: `IjkGridRepresentation`, grid properties, simulator tables (relperm, PVT), well trajectories, history match data.
- [ ] **Structure map / surface pipeline**: Implement surface ingestion in the demo — generate `StructureMap` WPCs from fmu-dataio surface exports, push Grid2dRepresentations to RDDMS. The [SeisInt](SeisInt.md) design is ready; implementation is pending.

#### 10.2 Medium priority

- [ ] **Economics WPC**: Design and implement a dedicated economics WPC (or custom schema like DevelopmentConcept) for NPV, CAPEX, OPEX, IRR, breakeven. Currently in `ext.equinor` which is fragile under manifest ingestion.
- [ ] **Per-realization surface handling at scale**: Define the packaging strategy for 200+ realization × N horizon surface sets. Options: (a) one WorkProduct per realization, (b) aggregated surfaces only in OSDU with raw in Sumo, (c) bulk RDDMS upload with catalog references.
- [ ] **Production profile ensemble WPC**: Formalize production forecast as a per-realization `ColumnBasedTable` (keys: `Realisation/Year`, columns: rates/cumulatives) enabling P10/P50/P90 forecast bands. Link `ProductionValues` WPC for observed data at DG3/DG4.
- [ ] **Sumo ↔ OSDU sync pipeline**: Implement automated or semi-automated sync from Sumo to OSDU. Options: (a) event-driven on Sumo upload, (b) batch after ensemble completion, (c) selective (standard results only).
- [ ] **Grid property WPC ingestion**: Implement ingestion of `grid_model_static` results (ROFF) into OSDU as `IjkGridRepresentation` + property WPCs. Requires RDDMS integration for geometry arrays.
- [ ] **Well data integration**: At DG3+, link `WellboreTrajectory` and `WellCompletionData` WPCs to the BD and Activity for planned wells.

#### 10.3 Lower priority / exploratory

- [ ] **OSDU as SoE (System of Engagement)**: Evaluate OSDU workflow services for orchestrating parts of the FMU pipeline — e.g., triggering post-processing, aggregation, or gate assembly after ensemble completion. Keep ERT as orchestrator; OSDU handles data lifecycle.
- [ ] **Cross-gate analytics API**: Build query patterns for cross-gate delta analysis: volumes DG2 vs DG1, risk evolution, parameter refinement history. Requires consistent parameter keys and segment mappings.
- [ ] **Ensemble lineage visualization**: Render the full provenance chain (design matrix row → static inputs → workflow → per-realization outputs → aggregation → gate evidence) as a navigable graph in the ORES analysis UI.
- [ ] **fmu-dataio schema v0.21.0+ alignment**: Track fmu-dataio schema changes (new content types: `observations`, `mapping`; new standard results for PVT, relperm, timeseries, lift curves, production network) and update OSDU mappings accordingly.
- [ ] **Custom schema registry**: Evaluate registering additional custom schemas (like `DevelopmentConcept`) for FMU-specific concepts that OSDU canonical schemas do not cover — e.g., `EnsembleSummary`, `HistoryMatchQuality`, `UncertaintyReport`.
- [ ] **Seismic data pipeline**: Integrate seismic interpretation chain (Feature → Interpretation → ControlPoints → SeismicHorizon → StructureMap) per the [SeisInt](SeisInt.md) design. Lower priority as seismic is typically pre-FMU input.
- [ ] **RESQML Activity round-trip**: Currently the demo creates dual representations (OSDU REST + RESQML EPC with Activity chains). Evaluate whether RESQML Activity can be the single source of truth for workflow provenance, with OSDU Activity as a derived view.

***

## Diagrams

### Data flow — FMU to OSDU SoR

```mermaid
flowchart LR
  ERT[ERT Orchestrator]
  RMS[RMS]
  DATAIO[fmu-dataio]
  RESQPY[resqpy / etp handler]
  ECL[Eclipse / OPM]
  SUMO[Sumo - current SoR]
  OSDU[OSDU Reservoir DMS]
  COLL[Ensemble as WorkProduct]
  CLIENTS[Webviz / ResInsight / ORES ...]

  ERT -- orchestrates --> RMS
  ERT -- orchestrates --> ECL
  RMS -- export via fmu-dataio --> DATAIO
  ECL -- results --> RESQPY
  RESQPY -- export via fmu-dataio --> DATAIO
  DATAIO -- upload --> SUMO
  SUMO -- sync / migrate --> OSDU
  OSDU -- grid properties deck --> RESQPY
  RESQPY -- build deck --> ECL
  OSDU -- organize and relate --> COLL
  OSDU -- query maps results tables --> CLIENTS
  SUMO -- query results --> CLIENTS
  RMS -- SoR models maps --> OSDU
```

### Decision gate lifecycle — FMU artifacts in OSDU

```mermaid
flowchart TB
  subgraph DG1["DG1 - Identify"]
    BD1[BusinessDecision DG1]
    REV1_RAW[REV Raw - 3 realisations]
    REV1_STAT[REV Stats P10/P50/P90]
    PARAMS1[Input Params CBT]
    ACT1[Activity]
    RISK1[Risk x1-2]
  end

  subgraph DG2["DG2 - Concept Select"]
    BD2[BusinessDecision DG2]
    REV2_RAW[REV Raw - 50+ realisations]
    REV2_STAT[REV Stats P10/P50/P90]
    PARAMS2[Input Params CBT revised]
    DM2[Design Matrix CBT]
    PP2[Production Forecast CBT]
    DEV2[DevelopmentConcept WPC]
    GLS2[GeoLabelSet]
    RISK2[Risk x6]
    DOCS2[SRA CRA PDO PTR]
    ACT2[Activity]
  end

  subgraph DG3["DG3 - FEED"]
    BD3[BusinessDecision DG3]
    GRID3[IjkGrid + Properties]
    SIM3[Simulator Tables]
    WELLS3[WellboreTrajectory]
    MAPS3[StructureMaps]
    PROD3[ProductionValues - history]
  end

  BD1 --> BD2
  BD2 --> BD3
  ACT1 --> BD1
  ACT2 --> BD2
  REV1_RAW --> ACT1
  REV2_RAW --> ACT2
  PARAMS1 --> ACT1
  DM2 --> ACT2
```

### Ensemble data relationships

```mermaid
erDiagram
  Reservoir ||--o{ ReservoirSegment : segments
  WorkProduct ||--o{ ReservoirEstimatedVolumes : "raw and stats"
  WorkProduct ||--o{ ColumnBasedTable : "design matrix and forecast"
  WorkProduct ||--o{ StructureMap : "per-horizon surfaces"
  WorkProduct ||--o{ IjkGridRepresentation : "per-realization grids"
  Activity ||--|{ WorkProduct : "context"
  Activity }|--|| ActivityTemplate : "follows template"
  Activity ||--o{ ColumnBasedTable : "input design params"
  Activity ||--o{ ReservoirEstimatedVolumes : "output volumes"
  ColumnBasedTable }o--|| ReservoirEstimatedVolumes : "join on Realisation"
  BusinessDecision ||--o{ Activity : "PriorActivityIDs"
  BusinessDecision ||--o{ Risk : "RiskIDs"
  BusinessDecision ||--o{ Document : "SRA CRA PDO"
  BusinessDecision }o--|| BusinessDecision : "prior gate via Parameters"
  ReservoirEstimatedVolumes }o--|| Reservoir : "ParentObjectID"
```
