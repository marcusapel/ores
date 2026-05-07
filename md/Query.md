# Querying OSDU & Reservoir Data

---

## Query Paths

```
ORES Client ──► OSDU Search API    (metadata, spatial, kind-based)
            ──► RDDMS REST API     (browse dataspaces/types/objects/graph/arrays)
            ──► ETP WebSocket      (bulk import/export, streaming)
            ──► GraphQL /api/graphql/query  (deep search + arrays + graph)
                    │
               ┌────┴─────────────────────────────┐
               │ Path A: OSDU Catalog (ES)        │  ← kind + text search
               │ Path B: Local PG (asyncpg)       │  ← fastest, un-indexed data
               │ Path C: Remote RDDMS (REST)      │  ← Azure-hosted dataspaces
               └──────────────────────────────────┘
                         ↓ merge by UUID ↓
                    FederatedSearchResult
```

| Path | Best for | Speed |
|------|----------|-------|
| OSDU Search | Records by kind, metadata keywords, spatial | Fast (metadata only) |
| RDDMS REST | Browse dataspaces, single objects, full XML | Medium |
| ETP WebSocket | Bulk EPC import/export, streaming | Fast |
| GraphQL (PG) | Deep filtering, array predicates, multi-dataspace | Fastest |
| GraphQL federated | OSDU + RDDMS simultaneously, UUID dedup | Fast (parallel) |

---

## 1. OSDU Search API

```json
{
  "kind": "osdu:wks:work-product-component--BusinessDecision:*",
  "query": "Drogon AND DG2",
  "limit": 50
}
```

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

---

## 2. RDDMS REST API

| Endpoint | Purpose |
|----------|---------|
| `GET /dataspaces` | List all dataspaces |
| `GET /dataspaces/{ds}/types` | Types with counts |
| `GET /dataspaces/{ds}/types/{type}/resources` | List objects |
| `GET /dataspaces/{ds}/types/{type}/resources/{uuid}` | Single object |
| `GET .../resources/{uuid}/targets` | Forward references |
| `GET .../resources/{uuid}/sources` | Reverse references |
| `GET .../resources/{uuid}/arrays` | List arrays |
| `GET .../resources/{uuid}/arrays/{path}` | Read array data |

---

## 3. GraphQL Deep Search

### Available Queries

| Query | Purpose |
|-------|---------|
| `status` | Backend check (PG version or REST info) |
| `dataspaces { path uri }` | List dataspaces |
| `resourceTypes(dataspace)` | Types + counts |
| `resqmlObjects(dataspace, typeName)` | Browse objects |
| `objectRelations(dataspace, typeName, uuid, direction)` | Graph traversal |
| `objectArrays(dataspace, typeName, uuid)` | Arrays + statistics |
| `deepSearch(dataspace, typeName, propertyFilter)` | Combined filter |
| `deepSearch(dataspaces: [...])` | Multi-dataspace |
| `federatedSearch(text, dataspaces, kind)` | OSDU catalog + RDDMS dual-path |

### PropertyFilter Reference

| Field | Type | Example |
|-------|------|---------|
| `kind` | String | `"General continuous"` |
| `titleContains` | String | `"PORO"`, `"PERMX"` |
| `arrayFilter.threshold` | Float | `0.25`, `500.0` |
| `arrayFilter.operator` | Enum | `GT`, `LT`, `GTE`, `LTE`, `EQ` |

---

### Exploring

```graphql
{ status }
{ dataspaces { path uri } }
{ resourceTypes(dataspace: "maap/drogon") { name count } }
```

```graphql
# Browse objects of a type (swap typeName for any RESQML type)
{
  resqmlObjects(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_IjkGridRepresentation"
    limit: 10
  ) { uuid title typeName }
}
```

---

### Relationships (Graph Traversal)

```graphql
# Forward refs (targets): what does Simgrid reference?
{
  objectRelations(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_IjkGridRepresentation"
    uuid: "0bc36994-2032-4e08-bad8-60ce0871002a"
    direction: "targets"
  ) { uuid name typeName direction contentType }
}
```

```graphql
# Reverse refs (sources): what properties/representations point to this object?
{
  objectRelations(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_IjkGridRepresentation"
    uuid: "0bc36994-2032-4e08-bad8-60ce0871002a"
    direction: "sources"
  ) { uuid name typeName direction contentType }
}
```

Common traversal patterns (same query, swap `typeName`, `uuid`, `direction`):

| Pattern | typeName | direction | What you get |
|---------|----------|-----------|--------------|
| Grid → CRS + StratColumn | `obj_IjkGridRepresentation` | targets | Referenced objects |
| Grid → all properties | `obj_IjkGridRepresentation` | sources | Attached ContinuousProperty/DiscreteProperty |
| Well Feature → Interp → Traj | `obj_WellboreFeature` | sources | Chain of representations |
| Horizon → surfaces | `obj_HorizonInterpretation` | sources | Grid2D representations |
| Surface → horizon | `obj_Grid2dRepresentation` | targets | Which horizon it represents |
| Well frame → log curves | `obj_WellboreFrameRepresentation` | both | All attached properties |

---

### Deep Search - Property Filtering

```graphql
# Find grids where porosity > 0.25 (change titleContains/threshold for other properties)
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
        title kind uom
        statistics { count minValue maxValue mean }
        matchingCells { count total fraction }
      }
    }
  }
}
```

Common filter variations (same query structure, swap `titleContains` + `threshold` + `operator`):

| Use case | titleContains | threshold | operator |
|----------|--------------|-----------|----------|
| High porosity zones | `"PORO"` | 0.25 | GT |
| High-perm streaks | `"PERMX"` | 500.0 | GT |
| Hydrocarbon zones (low Sw) | `"SWATINIT"` | 0.3 | LT |
| Tight zones (low perm) | `"PERMX"` | 1.0 | LT |
| Net-to-gross cutoff | `"ntg_pem"` | 0.5 | GT |
| Well log porosity | `"PHIT"` | 0.25 | GT |
| Well log permeability | `"KLOGH"` | 100.0 | GT |

```graphql
# Browse ALL properties on IjkGrids (no filter - omit propertyFilter)
{
  deepSearch(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_IjkGridRepresentation"
    includeStatistics: true
    limit: 2
  ) {
    objects { uuid title properties { title kind uom statistics { count minValue maxValue mean } } }
  }
}
```

For **well logs**, use `typeName: "resqml20.obj_WellboreFrameRepresentation"` with the same filter pattern.

---

### Deep Search - Multiple Dataspaces

```graphql
{
  deepSearch(
    dataspaces: ["maap/drogon", "maap/volve"]
    typeName: "resqml20.obj_IjkGridRepresentation"
    propertyFilter: { titleContains: "PORO", arrayFilter: { threshold: 0.2, operator: GT } }
    includeStatistics: true
    limit: 10
  ) {
    backend totalScanned totalMatched queryDescription
    objects { uuid title properties { title statistics { minValue maxValue } matchingCells { count total fraction } } }
  }
}
```

---

### Federated Search (OSDU + RDDMS)

The `federatedSearch` resolver combines **three independent sources** in a single query:

| Source | Parameter | What it searches | Speed |
|--------|-----------|------------------|-------|
| OSDU Catalog | `searchCatalog` | Elasticsearch metadata (kind, text, spatial) | Fast |
| Local RDDMS | `searchRddms` | PostgreSQL (direct, when `GRAPHQL_PG_CONN_STRING` set) | Fastest |
| Remote RDDMS | `searchRemoteRddms` | OSDU Reservoir-DDMS REST API (Azure-hosted) | Medium |

**How routing works:**

1. Selected dataspaces are classified as _local_ (present in PG) or _remote_ (only on OSDU RDDMS).
2. Local dataspaces are queried via direct PostgreSQL; remote ones go through the REST API.
3. The OSDU catalog is searched independently (by `kind` + free-text).
4. Results are **merged by UUID** - if the same object appears in multiple sources, flags indicate where it was found: `foundInCatalog`, `foundInLocalRddms`, `foundInRemoteRddms`.

**When to use which mode:**

| Scenario | Settings |
|----------|----------|
| Browse local un-indexed data (fast, offline) | `searchRddms:true`, others `false` |
| Check what's in the OSDU catalog | `searchCatalog:true`, others `false` |
| Verify catalog records exist in RDDMS | All three `true`, compare flags |
| Search remote + local RDDMS together | `searchRddms:true, searchRemoteRddms:true`, catalog off |
| Full discovery across everything | All three `true` (default) |
| Enrich results with relations/properties | Add `includeRelations`, `includeProperties`, `includeStatistics` |

**Key parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `text` | String | `"*"` | Free-text filter (title match for RDDMS, query string for catalog) |
| `kind` | String | `*:*` | OSDU kind filter (catalog path only) |
| `typeName` | String | - | RESQML type filter (RDDMS paths only) |
| `dataspaces` | [String] | auto-discover | Which dataspaces to search |
| `searchCatalog` | Boolean | true | Enable OSDU catalog path |
| `searchRddms` | Boolean | true | Enable local PG path |
| `searchRemoteRddms` | Boolean | true | Enable remote RDDMS REST path |
| `includeRelations` | Boolean | false | Enrich hits with graph edges |
| `includeProperties` | Boolean | false | Enrich hits with attached properties |
| `includeStatistics` | Boolean | false | Compute array min/max/mean for properties |
| `propertyFilter` | PropertyFilter | - | Filter results by property name/threshold |
| `limit` | Int | 30 | Max results returned |

```graphql
# Search both catalog and RDDMS for "grid"
{
  federatedSearch(
    text: "grid"
    searchCatalog: true
    searchRddms: true
    dataspaces: ["maap/drogon"]
    limit: 10
  ) {
    totalCatalog totalRddms totalMerged sources queryDescription
    hits {
      uuid title typeName dataspace
      foundInCatalog foundInRddms
      osduId osduKind
    }
  }
}
```

```graphql
# RDDMS-only with enrichment (relations + property statistics)
{
  federatedSearch(
    text: "Simgrid"
    searchCatalog: false
    searchRddms: true
    dataspaces: ["maap/drogon"]
    includeRelations: true
    includeProperties: true
    includeStatistics: true
    limit: 5
  ) {
    totalRddms totalMerged
    hits {
      uuid title typeName dataspace
      relations { uuid name typeName direction }
      properties {
        uuid title kind
        statistics { count minValue maxValue mean }
      }
    }
  }
}
```

```graphql
# Catalog-only - search by OSDU kind
{
  federatedSearch(
    text: "Drogon"
    kind: "osdu:wks:work-product-component--GenericRepresentation:*"
    searchCatalog: true
    searchRddms: false
    limit: 20
  ) {
    totalCatalog
    hits { uuid title typeName dataspace osduId osduKind foundInCatalog }
  }
}
```

---

### Array Statistics & Samples

```graphql
# Get array metadata, statistics, and sample values for any object
{
  objectArrays(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_Grid2dRepresentation"
    uuid: "02a9d0b6-1f7c-4553-994b-5060cd725d6d"
    includeStatistics: true
    includeSampleValues: true
    sampleSize: 10
  ) { path dataType dimensions totalElements statistics { count minValue maxValue mean stdDev } sampleValues }
}
```

Works with any object type - swap `typeName` + `uuid` for IjkGrids, WellboreFrames, etc.

---

## Choosing the Right Query Path

| Question | Path |
|----------|------|
| Find BusinessDecision records for Drogon | OSDU Search |
| What types are in a dataspace? | GraphQL `resourceTypes` |
| List all wells | GraphQL `resqmlObjects` |
| What does an object reference? | GraphQL `objectRelations(targets)` |
| Which properties are on this grid? | GraphQL `objectRelations(sources)` |
| Grids with porosity > 0.25 | GraphQL `deepSearch` |
| Wells with high permeability | GraphQL `deepSearch` (WellboreFrame) |
| Surfaces for a horizon | GraphQL `objectRelations(sources)` |
| Depth stats for a surface | GraphQL `objectArrays` |
| Search multiple dataspaces | GraphQL `deepSearch(dataspaces:[...])` |
| Search OSDU catalog + RDDMS at once | GraphQL `federatedSearch` |
| Find un-indexed local RESQML data | GraphQL `federatedSearch(searchRddms:true)` |
| Match OSDU records to RDDMS objects | GraphQL `federatedSearch` (UUID merge) |
| Import/export EPC file | ETP CLI |
| Full XML of an object | RDDMS REST |

---

## Setup - Local PostgreSQL

```bash
# 1. Start Docker (PG on 5433, ETP on 9002)
cd demo/epc && docker compose up -d

# 2. Import Drogon
./demo/epc/ingest.sh

# 3. Set env var (add to ~/.bashrc)
export GRAPHQL_PG_CONN_STRING="host=localhost port=5433 dbname=rddms user=foo password=bar"

# 4. Start ORES
ores   # or: uvicorn app.main:app --reload --port 8000

# 5. Verify
curl http://localhost:8000/api/graphql/info
curl -X POST http://localhost:8000/api/graphql/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"{ status dataspaces { path } }"}'
```

| Environment | PG conn string location | Target |
|-------------|-------------------------|--------|
| Local dev | `~/.bashrc` export | Docker PG (`localhost:5433`) |
| k8s | `k8s/secret.yaml` | Azure PG (`rddms-pg.database.azure.com`) |

### PostgreSQL Schema (openkv)

| Table | Content |
|-------|---------|
| `res` | Resource metadata (obj_id, guid, name) |
| `obj` | XML content |
| `rel` | Relationship edges |
| `ary` | Array metadata (path, type, dimensions) |
| `bin` | Array binary data (chunks) |
| `typ` | Type registry |

---

## Easy Mode – Visual Query Builder

The `/keys` page offers an **Easy Mode** tab that builds GraphQL queries without writing raw syntax.

### How it works

1. Select **Query type** (Deep Search, Browse, Relations, Federated)
2. Pick an **Object type** from categorized dropdown (Grid, Well, Surface, Property, …)
3. Optionally enter a **Property** name/alias (e.g. `poro`, `sw`, `perm`)
4. Set an **operator + threshold** filter (e.g. `> 0.25`)
5. Toggle **Statistics**, **Relations**, **Sample values**
6. Click **▶ Run Query**

Results render as **colored cards** with type-category badges, sparkline statistics bars, and matching-cell percentages.

### Query types in Easy Mode

| Action | GraphQL query generated | Use case |
|--------|------------------------|----------|
| Deep Search | `deepSearch(…)` | Find objects by type + filter on numerical property arrays |
| Browse | `resqmlObjects(…)` | List objects of a type (no filter) |
| Relations | `objectRelations(…)` | Graph traversal from a specific UUID |
| Federated | `federatedSearch(…)` | Search OSDU catalog + RDDMS simultaneously |

### Match modes

| Mode | Filter field | Behaviour |
|------|--------------|-----------|
| **Loose** (default) | `titleContains` | Substring match on property title or kind |
| **Strict** | `kind` | Exact match on canonical RESQML property kind |

Click **"Show generated GraphQL"** to see the raw query and switch to Advanced Mode for tweaking.

---

## Property Alias Map & Reference Data

### `/api/graphql/reference` endpoint

Returns the full reference dataset used by Easy Mode:

```json
{
  "propertyKinds": [
    { "name": "porosity", "aliases": ["poro", "phit", "phi", "nphi"],
      "description": "Fraction of void space in rock", "uom": "v/v" },
    ...
  ],
  "resqmlTypes": [
    { "name": "resqml20.obj_IjkGridRepresentation", "short": "IjkGrid",
      "category": "Grid", "description": "3D geocellular grid (corner-point or parametric)" },
    ...
  ],
  "operators": [
    { "value": "GT", "label": "> (greater than)", "symbol": ">" },
    ...
  ],
  "aliasMap": { "poro": "porosity", "sw": "water saturation", "perm": "permeability", ... }
}
```

**Counts:** 20 property kinds, 29 RESQML types (9 categories), 5 operators, 90 alias entries.

### `/api/graphql/resolve-alias?term=<term>` endpoint

Resolves a shorthand term to its canonical RESQML property kind:

```bash
# Exact match
curl /api/graphql/resolve-alias?term=poro
# → { "matches": [{ "name": "porosity", "aliases": [...], "uom": "v/v" }], "mode": "exact" }

# Fuzzy match (multiple candidates)
curl /api/graphql/resolve-alias?term=sat
# → { "matches": [{ "name": "water saturation" }, { "name": "oil saturation" }, ...], "mode": "fuzzy" }
```

### Standard Property Kinds (RESQML reference)

| Canonical name | Common aliases | Unit | Description |
|----------------|---------------|------|-------------|
| porosity | poro, phit, phi, nphi | v/v | Fraction of void space |
| permeability | perm, permx, permy, permz, kh | mD | Flow capacity |
| water saturation | sw, swat, swatinit | v/v | Water fraction in pore space |
| oil saturation | so, soil | v/v | Oil fraction in pore space |
| gas saturation | sg, sgas | v/v | Gas fraction in pore space |
| net-to-gross | ntg, n2g | ratio | Net reservoir thickness / gross |
| depth | tvd, tvdss, z | m | Vertical depth |
| pressure | pres, pressure, bhp | bar | Fluid pressure |
| temperature | temp | °C | Formation temperature |
| bulk density | rhob, den | g/cm³ | Bulk density log |
| gamma ray | gr, gamma | API | Natural gamma radiation |
| resistivity | rt, res, ild | ohm·m | Formation resistivity |
| acoustic impedance | ai, imp | (m/s)·(g/cm³) | Seismic impedance |
| velocity | vp, vs, vel | m/s | Seismic velocities |
| facies | facies, lith, litho | - | Discrete rock type |
| zone | zone, region, segment | - | Discrete zone/region index |
| thickness | thick, dz, isochore | m | Layer thickness |
| volume | vol, bulk_vol, bv | m³ | Volume attribute |
| age | age, chrono | Ma | Geological age |
| displacement | throw, heave | m | Fault displacement |

### RESQML Type Categories

| Category | Example types |
|----------|--------------|
| Grid | IjkGrid, UnstructuredGrid |
| Surface | Grid2d, TriangulatedSet |
| Well | WellboreFeature, WellboreInterpretation, WellboreTrajectory, WellboreFrame |
| Property | ContinuousProperty, DiscreteProperty, CategoricalProperty |
| Stratigraphy | StratigraphicColumn, HorizonInterpretation, FaultInterpretation |
| Organization | StructuralOrganization, StratigraphicColumnRankInterpretation |
| CRS | LocalDepth3dCrs, LocalTime3dCrs |
| Provenance | Activity, ActivityTemplate |
| Container | EpcExternalPartReference |

---

## Colored Result Cards

Easy Mode renders results as **type-colored cards** instead of raw JSON:

| Category | Color scheme | Badge |
|----------|-------------|-------|
| Grid | Green bg, dark green text | `IjkGrid` |
| Surface | Blue bg, dark blue text | `Grid2d` |
| Well | Orange bg, dark orange text | `WellboreFeature` |
| Property | Purple bg, dark purple text | `ContinuousProperty` |
| Stratigraphy | Pink bg, dark pink text | `HorizonInterpretation` |
| CRS | Grey bg, dark grey text | `LocalDepth3dCrs` |

Each card includes:
- **UUID** (monospace, selectable)
- **Title** (bold, category-colored)
- **Type badge** (short name like `IjkGrid` instead of `resqml20.obj_IjkGridRepresentation`)
- **Sparkline bar** for statistics (min → mean → max with blue needle for mean)
- **Matching cells bar** (green/orange/red based on fraction)
- **Source flags** for federated results (Catalog, Local PG, Remote)

---

## Links

| Resource | URL |
|----------|-----|
| OSDU Search | [community.opengroup.org](https://community.opengroup.org/osdu/platform/system/search-service) |
| RDDMS / OpenETPServer | [community.opengroup.org](https://community.opengroup.org/osdu/platform/domain-data-mgmt-services/reservoir/open-etp-server) |
| ETP 1.2 Spec | [energistics.org](https://www.energistics.org/energistics-transfer-protocol/) |
| RESQML 2.0/2.2 | [energistics.org](https://www.energistics.org/resqml/) |
| Strawberry GraphQL | [strawberry.rocks](https://strawberry.rocks/) |
| GraphQL language reference | [graphql.org/learn](https://graphql.org/learn/) |
| ORES GraphQL module | `app/graphql_router.py` |
| ORES source & issues | [github.com/equinor/ores](https://github.com/equinor/ores) |
