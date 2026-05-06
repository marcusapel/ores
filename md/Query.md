# Querying OSDU & Reservoir Data — Guide

> A practical guide to all query interfaces available in ORES for searching, browsing, and deep-filtering subsurface data.

---

## Table of Contents

- [Overview — Four Query Paths](#overview--four-query-paths)
- [1. OSDU Search API (Catalog)](#1-osdu-search-api-catalog)
- [2. RDDMS REST API (ETP/HTTP)](#2-rddms-rest-api-etphttp)
- [3. ETP WebSocket Protocol](#3-etp-websocket-protocol)
- [4. GraphQL Deep Search (PostgreSQL)](#4-graphql-deep-search-postgresql)
- [Choosing the Right Query Path](#choosing-the-right-query-path)
- [Quick Reference — Example Queries](#quick-reference--example-queries)
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
- Answer questions like *"which Grid2D has Porosity > 0.2?"*

### ORES page
**`/keys`** → **GraphQL Deep Search** panel (collapsible). The badge shows whether the backend is PostgreSQL (green) or REST API (blue).

### Architecture

When `GRAPHQL_PG_CONN_STRING` is set on the server, queries go **directly to PostgreSQL** (fastest). Otherwise, they use the RDDMS REST API as fallback (always works if you have a valid token).

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

### Example — browse objects
```graphql
{
  resqmlObjects(
    dataspace: "demo/Volve"
    typeName: "resqml20.obj_Grid2dRepresentation"
    limit: 20
  ) {
    uuid title typeName
  }
}
```

### Example — relationship traversal
```graphql
{
  objectRelations(
    dataspace: "demo/Volve"
    typeName: "resqml20.obj_Grid2dRepresentation"
    uuid: "2bcdc46c-2132-4cdf-beb2-711f6e5eda6c"
    direction: "both"
  ) {
    uuid name typeName direction contentType
  }
}
```

Returns:
```json
[
  {"name": "Sleipner_Fm._Top", "typeName": "resqml20.obj_HorizonInterpretation",
   "direction": "target", "contentType": "rsq:RepresentedInterpretation"},
  {"name": "Local Depth CRS", "typeName": "resqml20.obj_LocalDepth3dCrs",
   "direction": "target", "contentType": "rsq:Grid2dPatch/rsq:Geometry/rsq:LocalCrs"}
]
```

### Example — reverse graph (who uses this CRS?)
```graphql
{
  objectRelations(
    dataspace: "demo/Volve"
    typeName: "resqml20.obj_LocalDepth3dCrs"
    uuid: "46f85007-5e5f-41dc-8a6e-2bb9e94c1a2d"
    direction: "sources"
  ) {
    name typeName contentType
  }
}
```

### Example — deep search with array filter
```graphql
{
  deepSearch(
    dataspace: "demo/Volve"
    typeName: "resqml20.obj_Grid2dRepresentation"
    propertyFilter: {
      kind: "Porosity"
      arrayFilter: { threshold: 0.2, operator: GT }
    }
    includeStatistics: true
  ) {
    backend totalScanned totalMatched
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

### Example — array statistics
```graphql
{
  objectArrays(
    dataspace: "demo/Volve"
    typeName: "resqml20.obj_Grid2dRepresentation"
    uuid: "2bcdc46c-2132-4cdf-beb2-711f6e5eda6c"
    includeStatistics: true
    includeSampleValues: true
    sampleSize: 10
  ) {
    path dimensions totalElements
    statistics { count minValue maxValue mean stdDev }
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
  "backend": "PostgreSQL",
  "hint": "Set GRAPHQL_PG_CONN_STRING env var on the server..."
}
```

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
| "What horizons are in dataspace demo/Volve?" | **RDDMS REST** or **GraphQL** |
| "What does this Grid2D point to?" | **GraphQL** `objectRelations` |
| "Which properties have porosity > 0.2?" | **GraphQL** `deepSearch` |
| "Import an EPC file into a dataspace" | **ETP CLI** |
| "Get the full XML of a WellboreFeature" | **RDDMS REST** |
| "Array statistics without downloading data" | **GraphQL** `objectArrays` |
| "Bulk export a dataspace to EPC" | **ETP CLI** |
| "Which objects reference this CRS?" | **GraphQL** `objectRelations(sources)` |

---

## Quick Reference — Example Queries

### OSDU Search — find all seismic horizons
```json
POST /api/search/v2/query
{
  "kind": "osdu:wks:work-product-component--SeismicHorizon:*",
  "query": "*",
  "limit": 100
}
```

### RDDMS REST — list types in a dataspace
```
GET /reservoir-ddms/ddms/v2/dataspaces/demo%2FVolve/types
```

### ETP CLI — import EPC
```bash
openETPServer space -S ws://server:9002 -s "demo/Volve" --import-epc model.epc
```

### GraphQL — chain traversal
```graphql
# Step 1: Grid2D targets
{ objectRelations(dataspace: "demo/Volve", typeName: "resqml20.obj_Grid2dRepresentation", uuid: "...", direction: "targets") { uuid name typeName contentType } }

# Step 2: follow Interpretation → Feature
{ objectRelations(dataspace: "demo/Volve", typeName: "resqml20.obj_HorizonInterpretation", uuid: "...", direction: "targets") { uuid name typeName contentType } }
```

---

## Setup — Local PostgreSQL for GraphQL

To enable direct PostgreSQL access from the ORES GraphQL module locally:

### 1. Start Docker services

```bash
cd demo/epc
docker compose up -d
```

This starts:
- **PostgreSQL** on port `5433` (user: `tester`, db: `openetp`)
- **OpenETPServer** on port `9002` (no auth)

### 2. Import test data

```bash
./demo/epc/ingest.sh
```

Creates dataspace `demo/Volve` and imports `volve.surfaces.epc` (4 Grid2D horizon surfaces).

### 3. Start ORES with PG connection

```bash
export GRAPHQL_PG_CONN_STRING="host=localhost port=5433 dbname=openetp user=tester password=tester"
uvicorn app.main:app --reload --port 8080
```

### 4. Verify

Open `http://localhost:8080/keys` — the GraphQL badge should show **PostgreSQL** (green).

Or check via CLI:
```bash
curl http://localhost:8080/api/graphql/info
# → {"pg_configured": true, "pg_connected": true, "backend": "PostgreSQL", ...}
```

### 5. Run test suite

```bash
python demo/epc/test_graphql.py
```

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
