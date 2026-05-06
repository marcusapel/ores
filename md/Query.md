# Querying OSDU & Reservoir Data — Guide

> A practical guide to all query interfaces available in ORES for searching, browsing, and deep-filtering subsurface data.
> All examples use the **Drogon** synthetic reservoir model (589 objects, 18 wells, 48 surfaces, 2 geocellular grids).

---

## Table of Contents

- [Overview — Four Query Paths](#overview--four-query-paths)
- [1. OSDU Search API (Catalog)](#1-osdu-search-api-catalog)
- [2. RDDMS REST API (ETP/HTTP)](#2-rddms-rest-api-etphttp)
- [3. ETP WebSocket Protocol](#3-etp-websocket-protocol)
- [4. GraphQL Deep Search (PostgreSQL)](#4-graphql-deep-search-postgresql)
  - [Exploring (status, dataspaces, types, objects)](#exploring)
  - [Relationships (targets, sources, chains)](#relationships)
  - [Deep Search — IjkGrid properties](#deep-search--ijkgrid-properties)
  - [Deep Search — Surfaces (Grid2D)](#deep-search--surfaces-grid2d)
  - [Deep Search — Horizons](#deep-search--horizons)
  - [Deep Search — Well Logs](#deep-search--well-logs)
  - [Deep Search — Multiple Dataspaces](#deep-search--multiple-dataspaces)
  - [Array Statistics & Samples](#array-statistics--samples)
- [Choosing the Right Query Path](#choosing-the-right-query-path)
- [Drogon Dataset Reference](#drogon-dataset-reference)
- [Setup — Local PostgreSQL for GraphQL](#setup--local-postgresql-for-graphql)
- [Links & References](#links--references)

---

## Overview — Four Query Paths

```
┌─────────────────────────────────────────────────────────────────┐
│                     ORES Web Client                              │
│  /search        /keys          /keys (GraphQL panel)            │
└─────┬─────────────┬────────────────┬────────────────────────────┘
      │             │                │
      ▼             ▼                ▼
┌──────────┐  ┌──────────────┐  ┌──────────────────┐
│ OSDU     │  │ RDDMS REST   │  │ GraphQL          │
│ Search   │  │ API (v2)     │  │ /api/graphql     │
│ API      │  │              │  │                  │
└──────────┘  └──────────────┘  └────────┬─────────┘
      │             │                     │
      │             │              ┌──────┴──────┐
      ▼             ▼              ▼             ▼
┌──────────┐  ┌──────────────┐  ┌─────────┐  ┌──────────┐
│ OSDU     │  │ OpenETP      │  │ REST    │  │ Direct   │
│ Indexer  │  │ Server       │  │ fallback│  │ asyncpg  │
│ (Elastic)│  │ (ETP 1.2)   │  │         │  │ to PG    │
└──────────┘  └──────────────┘  └─────────┘  └──────────┘
                     │                             │
                     └─────────────┬───────────────┘
                                   ▼
                           ┌──────────────┐
                           │  PostgreSQL  │
                           │  (openkv)    │
                           └──────────────┘
```

| Path | Best for | Speed | Depth |
|------|----------|-------|-------|
| **OSDU Search** | Finding records by kind, metadata keywords, spatial | Fast | Metadata only |
| **RDDMS REST** | Browsing dataspaces, types, single objects, graphs | Medium | Full objects |
| **ETP WebSocket** | Bulk import/export, streaming, file-level ops | Fast | Full data |
| **GraphQL (PG)** | Deep filtering, array predicates, relationship graph | Fastest | Objects + arrays |

---

## 1. OSDU Search API (Catalog)

The OSDU Search API queries the platform-wide catalog (backed by Elasticsearch/OpenSearch). It searches across **all kinds** — not just reservoir data.

### When to use
- Find records by **kind** (e.g. `osdu:wks:work-product-component--SeismicHorizon:1.2.0`)
- Full-text search on metadata fields
- Spatial queries (within polygon, bounding box)
- Filter by data partition, tags, legal compliance

### ORES page
**`/search`** — enter kind patterns + query text, results render as typed cards (BD, REV, Risk, GeoLabelSet).

### Example — find all BusinessDecision records
```json
{
  "kind": "osdu:wks:work-product-component--BusinessDecision:*",
  "query": "Drogon AND DG2",
  "limit": 50
}
```

### Example — spatial search for horizons
```json
{
  "kind": "osdu:wks:work-product-component--SeismicHorizon:*",
  "spatialFilter": {
    "field": "data.SpatialArea.Wgs84Coordinates",
    "byBoundingBox": {
      "topLeft": { "latitude": 62.0, "longitude": 1.5 },
      "bottomRight": { "latitude": 58.0, "longitude": 3.5 }
    }
  }
}
```

### Key endpoints
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/search/v2/query` | POST | Full search with filters |
| `/api/search/v2/query_with_cursor` | POST | Paginated results |

### Links
- [OSDU Search API spec](https://community.opengroup.org/osdu/platform/system/search-service)
- ORES implementation: `app/main.py` → `/search` route + `app/osdu.py` → `search()`

---

## 2. RDDMS REST API (ETP/HTTP)

The Reservoir DDMS (RDDMS) exposes an HTTP REST API on top of the ETP datastore. This is what ORES uses for the **Resources page** (`/keys`).

### When to use
- Browse **dataspaces** and their contents
- List types and objects within a dataspace
- Get individual RESQML objects (full JSON/XML)
- Traverse the object graph (targets / sources)
- Read numerical array data (grid points, property values)

### ORES page
**`/keys`** — select Dataspace → Type → Object. Metadata, relations, and arrays are displayed inline.

### Key endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /dataspaces` | List all dataspaces |
| `GET /dataspaces/{ds}/types` | List resource types with counts |
| `GET /dataspaces/{ds}/types/{type}/resources` | List objects |
| `GET /dataspaces/{ds}/types/{type}/resources/{uuid}` | Get single object |
| `GET /dataspaces/{ds}/types/{type}/resources/{uuid}/targets` | Forward references |
| `GET /dataspaces/{ds}/types/{type}/resources/{uuid}/sources` | Reverse references |
| `GET /dataspaces/{ds}/types/{type}/resources/{uuid}/arrays` | List arrays |
| `GET /dataspaces/{ds}/types/{type}/resources/{uuid}/arrays/{path}` | Read array data |

### Example — list Grid2D objects in a dataspace
```
GET /reservoir-ddms/ddms/v2/dataspaces/demo%2FVolve/types/resqml20.obj_Grid2dRepresentation/resources
```

### RESQML relationship model (graph)
Every RESQML object can reference other objects via `DataObjectReference`:

```
Grid2dRepresentation
  → RepresentedInterpretation : HorizonInterpretation
  → LocalCrs : LocalDepth3dCrs

HorizonInterpretation
  → InterpretedFeature : GeneticBoundaryFeature

ContinuousProperty
  → SupportingRepresentation : Grid2dRepresentation (or IjkGrid)
```

The REST API exposes this via `/targets` (forward) and `/sources` (reverse).

### Links
- [RDDMS v2 API Documentation](https://community.opengroup.org/osdu/platform/domain-data-mgmt-services/reservoir/open-etp-server)
- ORES implementation: `app/osdu.py` → `list_dataspaces()`, `list_types()`, `list_resources()`, etc.

---

## 3. ETP WebSocket Protocol

ETP 1.2 (Energistics Transfer Protocol) is a WebSocket-based binary protocol for real-time data exchange. The OpenETPServer implements this protocol over PostgreSQL.

### When to use
- **Bulk import/export** of EPC files (`.epc` + `.h5`)
- Streaming large array data
- Creating/deleting dataspaces programmatically
- Low-latency operations (single TCP connection, binary encoding)

### CLI Examples (OpenETPServer client)

```bash
# Ping the server
openETPServer probe --ping -S ws://localhost:9002 --auth none

# Create a dataspace
openETPServer space -S ws://localhost:9002 --auth none --new -s "demo/MyProject"

# Import an EPC file
openETPServer space -S ws://localhost:9002 --auth none \
  -s "demo/MyProject" --import-epc /path/to/model.epc

# Export to EPC file
openETPServer space -S ws://localhost:9002 --auth none \
  -s "demo/MyProject" --export-epc ./export.epc --overwrite

# List dataspaces
openETPServer space -S ws://localhost:9002 --auth none --list ""

# Get statistics
openETPServer space -S ws://localhost:9002 --auth none \
  -s "demo/MyProject" --stats
```

### Links
- [Energistics ETP 1.2 Specification](https://www.energistics.org/energistics-transfer-protocol/)
- [OpenETPServer source](https://community.opengroup.org/osdu/platform/domain-data-mgmt-services/reservoir/open-etp-server)
- [OpenETPClient (TypeScript)](https://community.opengroup.org/osdu/platform/domain-data-mgmt-services/reservoir/open-etp-client)
- ORES Docker setup: `demo/epc/docker-compose.yaml`

---

## 4. GraphQL Deep Search (PostgreSQL)

The GraphQL module (`/api/graphql/query`) enables deep queries that combine **object browsing**, **graph traversal**, and **array-level numerical filtering** in a single request.

### When to use
- Find objects by type + filter on attached property values
- Traverse RESQML relationships across multiple hops
- Compute statistics on array data without downloading all values
- Query across **multiple dataspaces** simultaneously
- Answer questions like *"which wells have porosity > 0.25?"*

### ORES page
**`/keys`** → **GraphQL Deep Search** panel (collapsible).
- The badge shows whether backend is **PostgreSQL** (green) or **REST API** (blue)
- Use the **Dataspaces** multi-select to query across multiple dataspaces at once
- Preset dropdown includes working examples for IjkGrids, surfaces, horizons, and well logs

### Architecture

When `GRAPHQL_PG_CONN_STRING` is set, queries go **directly to PostgreSQL** (fastest).
Otherwise, they use the RDDMS REST API as fallback (always works if you have a valid token).

```
GraphQL Query  →  graphql_router.py  →  asyncpg pool  →  PostgreSQL (openkv)
                                     ↘  httpx          →  RDDMS REST API (fallback)
```

### Available queries

| Query | Purpose |
|-------|---------|
| `status` | Backend connectivity check |
| `dataspaces` | List all dataspaces |
| `resourceTypes(dataspace)` | Types + counts in a dataspace |
| `resqmlObjects(dataspace, typeName)` | Browse objects by type |
| `objectRelations(dataspace, typeName, uuid, direction)` | Graph traversal |
| `objectArrays(dataspace, typeName, uuid)` | Array data + statistics |
| `deepSearch(dataspace, typeName, propertyFilter)` | Combined graph + array filter |
| `deepSearch(dataspaces: [...])` | Multi-dataspace search |

---

### Exploring

#### Backend status
```graphql
{ status }
```
Returns: `"PostgreSQL direct: PostgreSQL 18.3 ..."`

#### List dataspaces
```graphql
{
  dataspaces {
    path
    uri
  }
}
```

#### Resource types and counts
```graphql
{
  resourceTypes(dataspace: "maap/drogon") {
    name
    count
  }
}
```
Returns 29 types including `obj_IjkGridRepresentation` (2), `obj_Grid2dRepresentation` (48),
`obj_ContinuousProperty` (215), `obj_WellboreFeature` (18), etc.

#### Browse objects by type
```graphql
# All IjkGrid geocellular grids (Drogon has 2: Simgrid + Geogrid)
{
  resqmlObjects(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_IjkGridRepresentation"
    limit: 10
  ) {
    uuid
    title
    typeName
  }
}
```

```graphql
# Wellbore features (18 wells: exploration 55/33-* and production OP*)
{
  resqmlObjects(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_WellboreFeature"
    limit: 30
  ) {
    uuid
    title
    typeName
  }
}
```

```graphql
# Horizon interpretations (6 horizons)
{
  resqmlObjects(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_HorizonInterpretation"
    limit: 10
  ) {
    uuid
    title
    typeName
  }
}
```

---

### Relationships

#### Forward references (targets)
What does the Simgrid reference? Shows CRS and stratigraphic column links:
```graphql
{
  objectRelations(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_IjkGridRepresentation"
    uuid: "0bc36994-2032-4e08-bad8-60ce0871002a"
    direction: "targets"
  ) {
    uuid name typeName direction contentType
  }
}
```
Returns:
```json
[
  {"name": "Structural model for Simgrid",
   "typeName": "resqml20.obj_StratigraphicColumnRankInterpretation",
   "contentType": "rsq:IntervalStratigraphicUnits/rsq:StratigraphicOrganization"},
  {"name": "Local Depth CRS",
   "typeName": "resqml20.obj_LocalDepth3dCrs",
   "contentType": "rsq:Geometry/rsq:LocalCrs"}
]
```

#### Reverse references (sources)
What references the Simgrid? (Properties attached to it):
```graphql
{
  objectRelations(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_IjkGridRepresentation"
    uuid: "0bc36994-2032-4e08-bad8-60ce0871002a"
    direction: "sources"
  ) {
    uuid name typeName direction contentType
  }
}
```
Returns ~30 properties: PORO, PERMX, PERMY, PERMZ, SWATINIT, ntg_pem, FWL, Zone, FaultBlock, etc.

#### Well chain: Feature → Interpretation → Trajectory
```graphql
# Step 1: What references well "55_33-A-1"? (WellboreInterpretation)
{
  objectRelations(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_WellboreFeature"
    uuid: "50495987-88f4-4e39-95c8-0b2624298c47"
    direction: "sources"
  ) {
    uuid name typeName contentType
  }
}
# → Returns WellboreInterpretation (and WellboreMarkerFrame, Trajectory, etc.)

# Step 2: Follow the interpretation to find trajectory + logs
{
  objectRelations(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_WellboreInterpretation"
    uuid: "INTERPRETATION-UUID-FROM-STEP-1"
    direction: "sources"
  ) {
    uuid name typeName contentType
  }
}
# → Returns WellboreTrajectory, WellboreFrame (logs), DeviationSurvey
```

#### Horizon → all representations
```graphql
# TopVolantis horizon → all surfaces, point sets, polylines, well markers
{
  objectRelations(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_HorizonInterpretation"
    uuid: "02e954a9-d7db-4b57-aef7-12b8ebf47a65"
    direction: "sources"
  ) {
    uuid name typeName contentType
  }
}
```
Returns ~30+ objects: 14 Grid2D surfaces, 8 PointSets, 6 PolylineSets (fault lines),
9 WellboreMarkerFrames, 2 StratigraphicColumnRankInterpretations.

---

### Deep Search — IjkGrid Properties

The flagship query: find representations with properties matching criteria, including **numerical filtering on array cell values**.

#### Porosity > 0.25
```graphql
{
  deepSearch(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_IjkGridRepresentation"
    propertyFilter: {
      titleContains: "PORO"
      arrayFilter: { threshold: 0.25, operator: GT }
    }
    includeStatistics: true
    limit: 5
  ) {
    backend totalScanned totalMatched queryDescription
    objects {
      uuid title
      properties {
        title kind
        statistics { count minValue maxValue mean }
        matchingCells { count total fraction }
      }
    }
  }
}
```
Result: Both Simgrid (107k cells) and Geogrid (927k cells) found. PORO on Simgrid has
2,621 cells > 0.25 out of 107,456 (2.4%). Max porosity = 0.359.

#### Permeability > 500 mD (high-perm zones)
```graphql
{
  deepSearch(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_IjkGridRepresentation"
    propertyFilter: {
      titleContains: "PERMX"
      arrayFilter: { threshold: 500.0, operator: GT }
    }
    includeStatistics: true
    limit: 5
  ) {
    backend totalScanned totalMatched queryDescription
    objects {
      uuid title
      properties {
        title kind
        statistics { count minValue maxValue mean }
        matchingCells { count total fraction }
      }
    }
  }
}
```
Result: PERMX on Simgrid: 20,744 cells > 500 mD (19.3%), max = 4,278 mD.

#### Water saturation < 0.3 (hydrocarbon zones)
```graphql
{
  deepSearch(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_IjkGridRepresentation"
    propertyFilter: {
      titleContains: "SWATINIT"
      arrayFilter: { threshold: 0.3, operator: LT }
    }
    includeStatistics: true
    limit: 5
  ) {
    backend totalScanned totalMatched queryDescription
    objects {
      uuid title
      properties {
        title kind
        statistics { count minValue maxValue mean }
        matchingCells { count total fraction }
      }
    }
  }
}
```

#### Browse ALL properties (no filter)
```graphql
{
  deepSearch(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_IjkGridRepresentation"
    includeStatistics: true
    limit: 2
  ) {
    backend totalScanned totalMatched
    objects {
      uuid title
      properties {
        title kind uom
        statistics { count minValue maxValue mean }
      }
    }
  }
}
```
Result: Lists all 30 properties on each grid (Simgrid has 20 continuous + 10 discrete).

**Drogon IjkGrid properties reference:**

| Property | Kind | Range (Simgrid) | Description |
|----------|------|-----------------|-------------|
| PORO | Unknown | 0 – 0.36 | Porosity |
| PERMX | Unknown | 0 – 4,278 | Horizontal permeability X (mD) |
| PERMY | Unknown | 0 – 4,278 | Horizontal permeability Y (mD) |
| PERMZ | Unknown | 0 – 2,497 | Vertical permeability (mD) |
| SWATINIT | General continuous | 0 – 1.0 | Initial water saturation |
| ntg_pem | General continuous | 0 – 1.0 | Net-to-gross (petrophysics) |
| poro_pem | General continuous | 0 – 0.36 | Porosity (petrophysics) |
| FWL | General continuous | 0 – 1,677 | Free water level (m) |
| GOC | General continuous | 0 – 1,648 | Gas-oil contact (m) |
| Cell_Z | General continuous | 1,500 – 1,800 | Cell center depth (m) |
| Zone | General discrete | 1 – 5 | Stratigraphic zone |
| Region | General discrete | 1 – 6 | Region index |
| FaultBlock | General discrete | 1 – 5 | Fault block |
| SATNUM | General discrete | 1 – 6 | Saturation region |
| FIPNUM | General discrete | 1 – 5 | Fluid-in-place region |

---

### Deep Search — Surfaces (Grid2D)

Grid2D representations are depth or time maps (horizons/surfaces). They don't have attached property objects — the Z-values are stored directly in the grid's own arrays.

#### List all 48 surfaces
```graphql
{
  resqmlObjects(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_Grid2dRepresentation"
    limit: 48
  ) {
    uuid title typeName
  }
}
```

Surface naming convention:
- `DS_*` — depth surfaces
- `TS_*` — time surfaces
- `GS_*` — velocity grids

#### Surface array statistics (Z-values)
```graphql
{
  objectArrays(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_Grid2dRepresentation"
    uuid: "02a9d0b6-1f7c-4553-994b-5060cd725d6d"
    includeStatistics: true
    includeSampleValues: true
    sampleSize: 10
  ) {
    path dataType dimensions totalElements
    statistics { count minValue maxValue mean stdDev }
    sampleValues
  }
}
```
Result: DS_interp (BaseVolantis) has 123,200 nodes, depth range 1,605 – 2,004 m, mean 1,751 m.

#### Find which horizon a surface represents
```graphql
{
  objectRelations(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_Grid2dRepresentation"
    uuid: "02a9d0b6-1f7c-4553-994b-5060cd725d6d"
    direction: "targets"
  ) {
    uuid name typeName contentType
  }
}
```
Returns: `BaseVolantis` (HorizonInterpretation) + `Local Depth CRS`.

---

### Deep Search — Horizons

Horizons are the geological surfaces. In RESQML they are modeled as:
`GeneticBoundaryFeature → HorizonInterpretation → Grid2dRepresentation (surface)`

#### All surfaces for a horizon
```graphql
# Which surfaces represent TopVolantis?
{
  objectRelations(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_HorizonInterpretation"
    uuid: "02e954a9-d7db-4b57-aef7-12b8ebf47a65"
    direction: "sources"
  ) {
    uuid name typeName contentType
  }
}
```
Returns ~30 objects: Grid2D surfaces, PointSets, PolylineSets, WellboreMarkerFrames.

#### Which wells penetrate a horizon?
Filter the results above for `typeName` containing `WellboreMarkerFrame`:
```graphql
{
  objectRelations(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_HorizonInterpretation"
    uuid: "02e954a9-d7db-4b57-aef7-12b8ebf47a65"
    direction: "sources"
  ) {
    uuid name typeName contentType
  }
}
# Filter results where typeName = "resqml20.obj_WellboreMarkerFrameRepresentation"
# These 9 marker frames correspond to 9 wells with picks at TopVolantis
```

#### Horizon → Feature (geological identity)
```graphql
{
  objectRelations(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_HorizonInterpretation"
    uuid: "02e954a9-d7db-4b57-aef7-12b8ebf47a65"
    direction: "targets"
  ) {
    uuid name typeName contentType
  }
}
```
Returns: `TopVolantis` (GeneticBoundaryFeature) — the geological entity.

**Drogon horizons reference:**

| Horizon | UUID | Surfaces | Well markers |
|---------|------|----------|-------------|
| TopVolantis | `02e954a9-d7db-4b57-aef7-12b8ebf47a65` | 14 Grid2D | 9 wells |
| BaseVolantis | `3657ca0b-d21f-41ca-801b-4a6a7eb1f426` | 14 Grid2D | 9 wells |
| TopTherys | `6c6eeb68-bb4d-4fa4-9eb4-b880b5bd7086` | 14 Grid2D | 9 wells |
| TopVolon | `db54a781-84ad-41e5-8bdd-c510246375cd` | 14 Grid2D | 9 wells |
| MSL | `7da0e4d7-1955-4031-8eaf-68a93515414d` | — | — |
| BaseVelmodel | `011ae8ee-bfa5-4804-a675-1f4704b1730c` | — | — |

---

### Deep Search — Well Logs

Well logs are stored as `ContinuousProperty` or `DiscreteProperty` objects attached
(via `SupportingRepresentation`) to a `WellboreFrameRepresentation`. The frame defines
the MD (measured depth) sample points; each property holds one log curve's values.

#### Wells with Total Porosity (PHIT) > 0.25
```graphql
{
  deepSearch(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_WellboreFrameRepresentation"
    propertyFilter: {
      titleContains: "PHIT"
      arrayFilter: { threshold: 0.25, operator: GT }
    }
    includeStatistics: true
    limit: 14
  ) {
    backend totalScanned totalMatched queryDescription
    objects {
      uuid title
      properties {
        title kind
        statistics { count minValue maxValue mean }
        matchingCells { count total fraction }
      }
    }
  }
}
```
Scans 14 well frames, finds wells where PHIT log has samples > 0.25.

#### Wells with Permeability (KLOGH) > 100 mD
```graphql
{
  deepSearch(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_WellboreFrameRepresentation"
    propertyFilter: {
      titleContains: "KLOGH"
      arrayFilter: { threshold: 100.0, operator: GT }
    }
    includeStatistics: true
    limit: 14
  ) {
    backend totalScanned totalMatched queryDescription
    objects {
      uuid title
      properties {
        title kind
        statistics { count minValue maxValue mean }
        matchingCells { count total fraction }
      }
    }
  }
}
```

#### Browse all log curves on a well
```graphql
{
  deepSearch(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_WellboreFrameRepresentation"
    includeStatistics: true
    limit: 3
  ) {
    backend totalScanned totalMatched
    objects {
      uuid title
      properties {
        title kind uom
        statistics { count minValue maxValue mean }
      }
    }
  }
}
```

#### Well log curve for a specific well
To find log curves for well "55_33-A-2":
```graphql
# Step 1: Find the WellboreFrameRepresentation for this well
{
  objectRelations(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_WellboreFrameRepresentation"
    uuid: "0086eb99-eca4-485b-882a-af15bc9add89"
    direction: "both"
  ) {
    uuid name typeName direction contentType
  }
}
# Sources = log curves (ContinuousProperty: VS, KLOGH, AI, PHIT, DENS, VP, VSH...)
# Targets = WellboreInterpretation + WellboreTrajectory
```

**Drogon well log curves reference:**

| Curve | Description | Typical range |
|-------|-------------|---------------|
| PHIT | Total porosity | 0 – 0.40 |
| KLOGH | Log-derived horizontal permeability | 0 – 5,000 mD |
| VSH | Shale volume fraction | 0 – 1.0 |
| DENS | Bulk density | 1.8 – 2.8 g/cm³ |
| AI | Acoustic impedance | 4,000 – 12,000 |
| VP | P-wave velocity | 2,000 – 5,000 m/s |
| VS | S-wave velocity | 1,000 – 3,000 m/s |
| VPVS | Vp/Vs ratio | 1.5 – 3.0 |
| Sw | Water saturation | 0 – 1.0 |
| VPHYL | Phyllosilicate volume | 0 – 1.0 |
| Facies | Lithofacies code (discrete) | 0 – 5 |
| Zone | Stratigraphic zone (discrete) | 1 – 5 |
| PERF | Perforation flag (discrete) | 0/1 |

---

### Deep Search — Multiple Dataspaces

You can search across multiple dataspaces simultaneously using the `dataspaces` list parameter:

```graphql
{
  deepSearch(
    dataspaces: ["maap/drogon", "maap/volve"]
    typeName: "resqml20.obj_IjkGridRepresentation"
    propertyFilter: {
      titleContains: "PORO"
      arrayFilter: { threshold: 0.2, operator: GT }
    }
    includeStatistics: true
    limit: 10
  ) {
    backend totalScanned totalMatched queryDescription
    objects {
      uuid title
      properties {
        title kind
        statistics { minValue maxValue mean }
        matchingCells { count total fraction }
      }
    }
  }
}
```

Notes:
- The `dataspaces: [...]` parameter runs the search in each dataspace in parallel
- Results are merged and truncated to `limit`
- `queryDescription` will show: `"Searched 2 dataspaces: maap/drogon, maap/volve"`
- The single `dataspace: "..."` parameter still works (backward compatible)
- If neither is provided, searches all available dataspaces (capped at 5)

In the UI, use the **Dataspaces** multi-select widget (Ctrl/Cmd+click) to pick
which dataspaces to query. The preset editor auto-inserts the correct parameter.

---

### Array Statistics & Samples

#### IjkGrid array metadata (geometry)
```graphql
{
  objectArrays(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_IjkGridRepresentation"
    uuid: "0bc36994-2032-4e08-bad8-60ce0871002a"
    includeStatistics: true
  ) {
    path dataType dimensions totalElements
    statistics { count minValue maxValue mean stdDev nanCount }
  }
}
```

#### Read sample values
```graphql
{
  objectArrays(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_IjkGridRepresentation"
    uuid: "0bc36994-2032-4e08-bad8-60ce0871002a"
    includeStatistics: true
    includeSampleValues: true
    sampleSize: 20
  ) {
    path dimensions totalElements
    statistics { minValue maxValue mean stdDev }
    sampleValues
  }
}
```

#### Surface Z-values with samples
```graphql
{
  objectArrays(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_Grid2dRepresentation"
    uuid: "02a9d0b6-1f7c-4553-994b-5060cd725d6d"
    includeStatistics: true
    includeSampleValues: true
    sampleSize: 10
  ) {
    path dataType totalElements
    statistics { minValue maxValue mean }
    sampleValues
  }
}
```

### Backend info endpoint
```
GET /api/graphql/info
```
Returns:
```json
{
  "pg_configured": true,
  "pg_connected": true,
  "pg_connection": "host=localhost port=5433 dbname=rddms user=foo password=***",
  "backend": "PostgreSQL",
  "hint": "Set GRAPHQL_PG_CONN_STRING env var on the server..."
}
```

### PropertyFilter input reference

The `propertyFilter` argument has these fields:

| Field | Type | Description |
|-------|------|-------------|
| `kind` | String | Match property kind (e.g. "Unknown", "General continuous") |
| `titleContains` | String | Match property title substring (e.g. "PORO", "PERMX") |
| `arrayFilter.threshold` | Float | Numerical threshold for cell values |
| `arrayFilter.operator` | Enum | `GT`, `LT`, `GTE`, `LTE`, `EQ` |

Both `kind` and `titleContains` are case-insensitive substring matches.
If both are specified, they are combined with AND logic.

### PostgreSQL schema (openkv)

The OpenETPServer stores RESQML data in PostgreSQL with this schema per dataspace:

| Table | Content |
|-------|---------|
| `res` | Resource metadata: obj_id, type, guid, name, timestamps |
| `obj` | XML content of each object |
| `rel` | Relationship edges (obj_id → dst_id, semantic label) |
| `ary` | Array metadata: path, type, dimensions, size |
| `bin` | Array binary data (chunks of float64/float32/int32) |
| `typ` | Type registry (EML/RESQML types) |
| `uri` | Namespace registry |
| `xpa` | Relationship semantic labels (e.g. `rsq:RepresentedInterpretation`) |

### Links
- GraphQL IDE: `/graphql` (GraphiQL)
- API endpoint: `POST /api/graphql/query`
- Info endpoint: `GET /api/graphql/info`
- ORES implementation: `app/graphql_router.py`
- Test script: `demo/epc/test_graphql.py`

---

## Choosing the Right Query Path

| Question | Best path |
|----------|-----------|
| "Find all BusinessDecision records for Drogon" | **OSDU Search** |
| "What types are in dataspace maap/drogon?" | **GraphQL** `resourceTypes` |
| "List all wells in the model" | **GraphQL** `resqmlObjects` |
| "What does the Simgrid reference?" | **GraphQL** `objectRelations(targets)` |
| "Which properties are on this IjkGrid?" | **GraphQL** `objectRelations(sources)` |
| "Find grids with porosity > 0.25" | **GraphQL** `deepSearch` |
| "Which wells have high permeability?" | **GraphQL** `deepSearch` (WellboreFrame) |
| "All surfaces representing TopVolantis" | **GraphQL** `objectRelations(sources)` |
| "Which wells penetrate this horizon?" | **GraphQL** `objectRelations(sources)` |
| "Get depth stats for a surface" | **GraphQL** `objectArrays` |
| "Search across multiple dataspaces" | **GraphQL** `deepSearch(dataspaces:[...])` |
| "Import an EPC file into a dataspace" | **ETP CLI** |
| "Get the full XML of a WellboreFeature" | **RDDMS REST** |
| "Bulk export a dataspace to EPC" | **ETP CLI** |

---

## Drogon Dataset Reference

The Drogon model (imported from `drogon.epc`) contains:

| Category | Count | Details |
|----------|-------|---------|
| **IjkGrids** | 2 | Simgrid (107k cells), Geogrid (927k cells) |
| **Grid2D surfaces** | 48 | DS_ (depth), TS_ (time), GS_ (velocity) |
| **Wells** | 18 | Exploration (55/33-*), production (OP*), RFT |
| **Horizons** | 6 | TopVolantis, BaseVolantis, TopTherys, TopVolon, MSL, BaseVelmodel |
| **Faults** | 6 | TectonicBoundaryFeature + FaultInterpretation |
| **Continuous Properties** | 215 | Grid properties + well logs |
| **Discrete Properties** | 82 | Zone, Region, FaultBlock, Facies |
| **Well log frames** | 14 | ~20 curves per well (PHIT, KLOGH, VP, VS, AI...) |
| **Well markers** | 9 | Horizon picks on wells |
| **Stratigraphy** | 5 units | StratigraphicUnitFeature + Interpretations |
| **CRS** | 2 | LocalDepth3dCrs + LocalTime3dCrs |
| **Activities** | 1 | Import provenance |
| **Total objects** | 589 | |
| **Total arrays** | 618 | |

### Key UUIDs for queries

| Object | UUID |
|--------|------|
| Simgrid (IjkGrid) | `0bc36994-2032-4e08-bad8-60ce0871002a` |
| Geogrid (IjkGrid) | `2c6de928-7e08-4601-b979-34048bd68c02` |
| TopVolantis (HorizonInterp) | `02e954a9-d7db-4b57-aef7-12b8ebf47a65` |
| BaseVolantis (HorizonInterp) | `3657ca0b-d21f-41ca-801b-4a6a7eb1f426` |
| TopTherys (HorizonInterp) | `6c6eeb68-bb4d-4fa4-9eb4-b880b5bd7086` |
| TopVolon (HorizonInterp) | `db54a781-84ad-41e5-8bdd-c510246375cd` |
| DS_interp surface (Grid2D) | `02a9d0b6-1f7c-4553-994b-5060cd725d6d` |
| Well 55_33-A-1 (Feature) | `50495987-88f4-4e39-95c8-0b2624298c47` |
| Well 55_33-A-2 (Feature) | `53e2c61e-9ef6-40fc-9d35-7c740287c0ca` |
| Log frame (55_33-A-2) | `0086eb99-eca4-485b-882a-af15bc9add89` |
| Local Depth CRS | `0a0ae03b-aee1-4651-8f11-5433eeda0ec2` |

---

## Setup — Local PostgreSQL for GraphQL

To enable direct PostgreSQL access from the ORES GraphQL module locally:

### 1. Start Docker services

```bash
cd demo/epc
docker compose up -d
```

This starts:
- **PostgreSQL** on port `5433` (user: `foo`, password: `bar`, db: `rddms`)
- **OpenETPServer** on port `9002` (no auth)

### 2. Import the Drogon dataset

```bash
./demo/epc/ingest.sh
```

Creates dataspace `maap/drogon` and imports `drogon.epc` (589 objects, 618 arrays, ~400 MB).

### 3. Set env var

Add to `~/.bashrc` (or export manually):
```bash
export GRAPHQL_PG_CONN_STRING="host=localhost port=5433 dbname=rddms user=foo password=bar"
```

### 4. Start ORES

```bash
ores
# or manually:
source ~/.bashrc
cd /path/to/ores
uvicorn app.main:app --reload --port 8000 --host 127.0.0.1
```

The `ores` script (`~/bin/ores`) runs `env_from_k8s.py` to load config/secrets from k8s YAMLs,
but **respects existing env vars** — so your `.bashrc` PG string takes priority over the k8s
Azure hostname.

### 5. Verify

Open `http://localhost:8000/keys` — the GraphQL badge should show **PostgreSQL** (green).

Or check via CLI:
```bash
curl http://localhost:8000/api/graphql/info
# → {"pg_configured": true, "pg_connected": true, "backend": "PostgreSQL", ...}

curl -X POST http://localhost:8000/api/graphql/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"{ status dataspaces { path } }"}'
# → {"data":{"status":"PostgreSQL direct: ...", "dataspaces":[{"path":"maap/drogon"}]}}
```

### 6. Run test suite

```bash
python demo/epc/test_graphql.py
```

### Environment configuration

| Environment | Where PG conn string lives | Points to |
|-------------|---------------------------|-----------|
| **Local dev** | `~/.bashrc` export | Docker PG (`localhost:5433`) |
| **k8s deployed** | `k8s/secret.yaml` → `GRAPHQL_PG_CONN_STRING` | Azure PG (`rddms-pg.database.azure.com`) |

The app reads it at startup via `os.getenv("GRAPHQL_PG_CONN_STRING")`.
Restart uvicorn after changing the value.

---

## Links & References

| Resource | URL |
|----------|-----|
| OSDU Search Service | [community.opengroup.org](https://community.opengroup.org/osdu/platform/system/search-service) |
| Reservoir DDMS / OpenETPServer | [community.opengroup.org](https://community.opengroup.org/osdu/platform/domain-data-mgmt-services/reservoir/open-etp-server) |
| OpenETPClient (TypeScript) | [community.opengroup.org](https://community.opengroup.org/osdu/platform/domain-data-mgmt-services/reservoir/open-etp-client) |
| ETP 1.2 Specification | [energistics.org](https://www.energistics.org/energistics-transfer-protocol/) |
| RESQML 2.0.1/2.2 | [energistics.org](https://www.energistics.org/resqml/) |
| Strawberry GraphQL | [strawberry.rocks](https://strawberry.rocks/) |
| asyncpg | [github.com/MagicStack/asyncpg](https://github.com/MagicStack/asyncpg) |
| ORES GraphQL module | `app/graphql_router.py` |
| ORES GraphQL presets | `/keys` → GraphQL Deep Search panel |
| Local docker setup | `demo/epc/docker-compose.yaml` |
| Test script | `demo/epc/test_graphql.py` |
