## **OSDU Support for FMU Data Handling – System of Record**

> **Reference links**:
> - [fmu-dataio](https://github.com/equinor/fmu-dataio) - FMU data standard & metadata export library (v2.24, schema v0.20.0)
> - [fmu-sumo](https://github.com/equinor/fmu-sumo) - Interaction with Sumo (current SoR for FMU results)
> - [fmu-drogon](https://github.com/equinor/fmu-drogon) - Public Drogon reference case
> - [ERT](https://github.com/equinor/ert) - Ensemble-based Reservoir Tool (workflow orchestrator)
> - [fmu-dataio data model](https://fmu-dataio.readthedocs.io/en/latest/datamodel/index.html) - FMU results metadata schema

***

### **1. Purpose**

* Support for OSDU storage and query as System of Record. SSDL alternative. Perspectively SoE workflow support.
* Enable round-trip fidelity between FMU Eclipse/OPM decks and OSDU Reservoir DMS facilitating:
   * Lossless, in-memory RESQML IJK grid and property handling (`resqpy`)
   * Efficient, file-less data transfer  (`pyetp`)
   * Metadata preservation end-to-end to ensure traceability and reproducibility. Option complete Activity use.
   * Bonus: Nexus, Intersect support via RESQML ETP API

### **1.1 Current FMU Data Landscape**

FMU data currently flows through the **Sumo** cloud storage platform, which serves as the primary System of Record for FMU results. The pipeline is:

1. **ERT** orchestrates the FMU workflow - defines cases, iterations/ensembles, realizations, and FORWARD_MODELs (RMS, Eclipse/OPM, etc.)
2. **fmu-dataio** exports data from within FORWARD_MODELs with rich metadata sidecars (denormalized YAML/JSON, one per file)
3. **Sumo** receives and indexes the exported data for querying, visualization (Webviz), and consumption
4. **OSDU** can complement or replace Sumo as the persistent SoR, with structured data management via the OSDU data model

The fmu-dataio metadata schema (currently v0.20.0) defines a **parent/child data model**: `case → ensemble → realization → files`. Each exported file has a metadata sidecar containing:
- `fmu.case` - case identity (name, uuid, user, model template)
- `fmu.ensemble` - ensemble/iteration identity (name, uuid)
- `fmu.realization` - realization identity (id, uuid, is_reference)
- `fmu.ert` - ERT experiment context (experiment.id, simulation_mode)
- `data.content` - content type (volumes, surfaces, grids, tables, cubes, etc.)
- `data.standard_result` - standardized result name (e.g., `inplace_volumes`)
- `masterdata` - field/country references
- `access` - classification and security

**Standard results** (via `fmu.dataio.export.rms.export_inplace_volumes` etc.) are the recommended export path - they enforce column conventions, naming, and validation against the FMU data standard.

***

### **2. Ground Rules**

*   **No breaking changes to FMU** workflow design and functionality, governance, ERT and other component roles: focus is on data handling, storage, metadata support, query
*   **Respect fmu-dataio as the metadata standard**: All FMU exports use fmu-dataio for metadata generation. OSDU mapping must preserve the fmu-dataio metadata structure and be able to reconstruct it on round-trip.
*   **One identity per artifact**: Each grid, property, map, and deck has a stable `UUID/SRN` and `version`.
*   **Lossless provenance**: Every output carries ancestry back to the exact input WPCs and FMU run.
*   **CRS & units are first-class**: CRS definition, axis order, rotation, and UOM travel with the data.
*   **Round-trip fidelity**: Data exported from Eclipse can be fully recovered from OSDU with identical identity and metadata.

***

### **3. Canonical Data Model - FMU ↔ OSDU Mapping**

The FMU data model (fmu-dataio) is denormalized and file-centric. The OSDU data model is normalized and record-centric. The mapping between them:

| FMU concept (fmu-dataio) | OSDU concept | Notes |
|---|---|---|
| `fmu.case` (name, uuid, model) | **Dataspace** or **WorkProduct** | Case = partition boundary for ACL and legal tags |
| `fmu.ensemble` (name, uuid) | **Collection** or **WorkProduct** | One per case with realization facet |
| `fmu.realization` (id, uuid) | **WPC** per artifact per realization | Realization index as key in tables |
| `data.content = volumes` | `ReservoirEstimatedVolumes` WPC | Standard result: `inplace_volumes` |
| `data.content = surface` | Surface/Grid WPC | |
| `data.content = property` | Grid Property WPC | |
| `data.content = table` | `ColumnBasedTable` WPC | Design matrix, timeseries, etc. |
| `data.standard_result.name` | WPC kind + PropertyTypeID | Canonical column/naming conventions |
| `masterdata.smda.field` | `master-data--Reservoir` | |
| `fmu.ert.experiment` | Activity / ActivityTemplate | ERT experiment → OSDU Activity provenance |

OSDU-specific types used:

* **Dataspace**: Partition boundary for ACL and legal tags. Analogous to ensemble unix file structure.
* **Work Product (WP)**: Reservoir model context (e.g., asset/model/vintage/ensemble).
* **Work Product Component (WPC)**: Atomic datasets (3D grid, 2D grid, property, table, deck, etc.).
* **Collection**: Groups WPs/WPCs by case, ensemble, or realization. Collection Granularity: One per **case** with **realization facet**. 

***

#### **Grid (IJK, Corner-Point)**

*   **Identity**: `grid_uuid`, `osdu_srn`, `version`
*   **Geometry**: `ni, nj, nk`, `k_direction`, `handedness`
*   **CRS**: Type (LocalDepth3d/Global), origin, rotation, axis order, units (XY/Z)
*   **Governance**: `legalTags`, `acl`, `data.ancestry.inputs`, timestamps
*   FMU-generated metadata: fmu-dataio

#### **Property (Cell-Sized)**

*   **Identity**: `property_uuid`, `osdu_srn`, `version`
*   **Mapping**: Eclipse keyword (PORO, PERMX, etc.), indexable element (cells), UOM, discrete/continuous
*   **Ties**: `supported_by_uuid` (grid UUID), property set/title
*   Governance & ancestry as above
*   FMU-generated metadata

#### **2D Grid / Map**

*   Identity + grid reference (surface grid or parent 3D grid + layer/slice), units, CRS
*   FMU-generated metadata 

#### **Table (CSV/Parquet or RESQML Table)**

*   Identity + schema (columns & UOM), run scope (case/realization/time)
*   FMU-generated metadata

#### **Deck Artifacts (Eclipse/OPM)**

*   **Identity**: `deck_id` (stable identifier for produced deck bundle)
*   **Components**: Which files exist (`GRID.grdecl`, `PORO.grdecl`, `.DATA/.EGRID`)
*   **Binding**: `grid_uuid` + list of `property_uuid`s
*   **Manifest**: json preferred but can use fmu-dataio yaml handler; stored as disk sidecar and attached to Deck WPC, Dataspace.
*   FMU-generated metadata

***

### **4. Deck Manifest**

A small sidecar (JSON/YAML) accompanying every deck export and OSDU write-back:

*   **Identity**: `deck_id`, `case`, `realization`
*   **Grid**: `grid_uuid`, `osdu_srn`, `dims`, `crs`
*   **Properties\[]**: `property_uuid`, `title`, `ecl_keyword`, `uom`, `discrete`
*   **Files**: List of produced artifacts (paths/names)
*   **Ancestry Inputs**: `[<wpc-id-grid>, <wpc-id-poro>, …]`
*   **Provenance**: Timestamp, tool version, conversion notes

> Stored both on disk and as metadata/attachment on the OSDU WPC.\
> Enables Eclipse → OSDU → Eclipse identity continuity.

***

### **5. Flow & Functionality (Metadata Emphasized)**

*   **Discovery**: Only attach properties linked to the chosen grid (`supported_by_uuid`).
*   **Conversion**: `resqpy` writes GRDECL/EGRID/ROFF using grid’s CRS/UOM; manifest records included items. Evaluate use of xtgeo/resdata for conversion support/parsing/validation. Maintain FMU-related metadata schema (prefer json, support yaml).
*   *   **FMU Run**: Consumes deck as-is; metadata manifest ties run back to inputs.
*   **Write-back**: Outputs become WPCs with ancestry to manifest inputs, linked into correct Collection.
*   **Round-trip**: Re-fetching Deck Artifact WPC + manifest allows deck reconstruction.

***

### **6. Round-Trip Identity Rules (Eclipse ⇄ OSDU)**

1.  **Grid Lock**
    *   Deck’s grid always referenced by `grid_uuid`.
    *   Re-publishing increments `deck_id`; `grid_uuid` remains unless topology changes.

2.  **Property Lock**
    *   Each property retains original `property_uuid`, Eclipse keyword, and UOM.
    *   Renames in Eclipse don’t overwrite OSDU identity.

3.  **CRS/UOM Lock**
    *   Manifest must include CRS type, origin/rotation, axis order, and UOM.
    *   Mismatches trigger validation warnings.

4.  **Ancestry Chain**
    *   All outputs must set `data.ancestry.inputs` to exact input WPC IDs.
    *   Collections capture ensemble structure; each realization adds its Deck Artifact WPC.

***

### **7. Minimal Responsibilities per Component**

*   **ERT**: Orchestrate FMU workflows - define cases, iterations/ensembles, realizations, FORWARD_MODELs. Owner of case definitions and experiment identity.
*   **fmu-dataio**: Export data with rich metadata (denormalized YAML/JSON sidecar per file). Enforces the FMU data standard (schema v0.20.0). Provides standard results for validated exports (e.g., `export_inplace_volumes`).
*   **Sumo (fmu-sumo)**: Current cloud SoR - receives fmu-dataio exports, indexes metadata, serves queries for Webviz/clients. Upload via `fmu-sumo` library.
*   **pyetp**: Discover WPCs (grid + properties), stream arrays, respect dataspace/ACL.
*   **resqpy**: Build in-memory model, convert to GRDECL/EGRID, emit Deck Manifest.
*   **xtgeo (resdata)**: support ROFF/GRDECL/EGRID conversion/validation
*   **OSDU**: Persist WPCs with `legalTags`, `acl`, `version`, `ancestry`, and Collections. Complements/replaces Sumo as persistent SoR.
*   **FMU workflow**: Consume decks as-is; echo `deck_id` and realization in run metadata via fmu-dataio exports.

***

### **8. Lightweight Acceptance**

*   **Identity**: `grid_uuid` in manifest matches WPC; all `property_uuid`s present and correct.
*   **CRS/UOM**: Manifest CRS fields complete; property UOMs consistent.
*   **Ancestry**: Output WPCs list all inputs; Collection structure matches ensemble layout.
*   **Round-trip**: Deck Artifact WPC + manifest sufficient to reconstruct runnable deck.

***

## Data flow diagram - SoR case

``` mermaid
flowchart LR
  ERT[ERT Orchestrator]
  RMS[RMS]
  DATAIO[fmu-dataio]
  RESQPY[resqpy/etp handler]
  ECL[Eclipse/OPM]
  SUMO[Sumo - current SoR]
  OSDU[OSDU Reservoir DMS]
  COLL[Ensemble as Collections]
  CLIENTS[Clients/Webviz/ResInsight ...]

  ERT -- orchestrates --> RMS
  ERT -- orchestrates --> ECL
  RMS -- export via fmu-dataio --> DATAIO
  ECL -- results --> RESQPY
  RESQPY -- export via fmu-dataio --> DATAIO
  DATAIO -- upload --> SUMO
  SUMO -- sync/migrate --> OSDU
  OSDU -- grid properties deck --> RESQPY
  RESQPY -- build deck --> ECL
  OSDU -- organize and relate --> COLL
  OSDU -- query maps results tables --> CLIENTS
  SUMO -- query results --> CLIENTS
  RMS -- SoR models maps --> OSDU
```

