# Querying OSDU & Reservoir Data

> All examples use the **Drogon** model (589 objects, 18 wells, 48 surfaces, 2 grids).

---

## Query Paths

```
ORES Client ──► OSDU Search API    (metadata, spatial, kind-based)
            ──► RDDMS REST API     (browse dataspaces/types/objects/graph/arrays)
            ──► ETP WebSocket      (bulk import/export, streaming)
            ──► GraphQL /api/graphql/query  (deep search + arrays + graph)
                    │
               ┌────┴────┐
               │ asyncpg │  ←  Direct PG (fast, when GRAPHQL_PG_CONN_STRING set)
               │ REST    │  ←  Fallback via RDDMS v2
               └─────────┘
```

| Path | Best for | Speed |
|------|----------|-------|
| OSDU Search | Records by kind, metadata keywords, spatial | Fast (metadata only) |
| RDDMS REST | Browse dataspaces, single objects, full XML | Medium |
| ETP WebSocket | Bulk EPC import/export, streaming | Fast |
| GraphQL (PG) | Deep filtering, array predicates, multi-dataspace | Fastest |

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

## 3. ETP WebSocket (CLI)

```bash
# Import EPC
openETPServer space -S ws://localhost:9002 --auth none \
  -s "maap/drogon" --import-epc drogon.epc

# Export EPC
openETPServer space -S ws://localhost:9002 --auth none \
  -s "maap/drogon" --export-epc ./export.epc --overwrite

# List dataspaces
openETPServer space -S ws://localhost:9002 --auth none --list ""
```

---

## 4. GraphQL Deep Search

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
```

```graphql
{ dataspaces { path uri } }
```

```graphql
{ resourceTypes(dataspace: "maap/drogon") { name count } }
```

```graphql
{
  resqmlObjects(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_IjkGridRepresentation"
    limit: 10
  ) { uuid title typeName }
}
```

```graphql
{
  resqmlObjects(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_WellboreFeature"
    limit: 30
  ) { uuid title typeName }
}
```

---

### Relationships (Graph Traversal)

```graphql
# Forward refs: what does Simgrid reference?
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
# Reverse refs: what properties are attached to Simgrid?
{
  objectRelations(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_IjkGridRepresentation"
    uuid: "0bc36994-2032-4e08-bad8-60ce0871002a"
    direction: "sources"
  ) { uuid name typeName direction contentType }
}
```

```graphql
# Well chain: Feature → Interpretation → Trajectory
{
  objectRelations(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_WellboreFeature"
    uuid: "50495987-88f4-4e39-95c8-0b2624298c47"
    direction: "sources"
  ) { uuid name typeName contentType }
}
```

```graphql
# Horizon → all representations (surfaces, point sets, well markers)
{
  objectRelations(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_HorizonInterpretation"
    uuid: "02e954a9-d7db-4b57-aef7-12b8ebf47a65"
    direction: "sources"
  ) { uuid name typeName contentType }
}
```

```graphql
# Which surface represents which horizon? (Grid2D → targets)
{
  objectRelations(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_Grid2dRepresentation"
    uuid: "02a9d0b6-1f7c-4553-994b-5060cd725d6d"
    direction: "targets"
  ) { uuid name typeName contentType }
}
```

---

### Deep Search — IjkGrid Properties

```graphql
# Porosity > 0.25
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

```graphql
# Permeability > 500 mD
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
    objects { uuid title properties { title kind statistics { minValue maxValue mean } matchingCells { count total fraction } } }
  }
}
```

```graphql
# Water saturation < 0.3 (hydrocarbon zones)
{
  deepSearch(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_IjkGridRepresentation"
    propertyFilter: {
      titleContains: "SWATINIT"
      arrayFilter: { threshold: 0.3, operator: LT }
    }
    includeStatistics: true
  ) {
    objects { uuid title properties { title kind matchingCells { count total fraction } } }
  }
}
```

```graphql
# Browse ALL properties (no filter)
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

---

### Deep Search — Well Logs

```graphql
# Wells with PHIT > 0.25
{
  deepSearch(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_WellboreFrameRepresentation"
    propertyFilter: { titleContains: "PHIT", arrayFilter: { threshold: 0.25, operator: GT } }
    includeStatistics: true
    limit: 14
  ) {
    totalScanned totalMatched
    objects { uuid title properties { title kind statistics { minValue maxValue mean } matchingCells { count total fraction } } }
  }
}
```

```graphql
# Wells with permeability (KLOGH) > 100 mD
{
  deepSearch(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_WellboreFrameRepresentation"
    propertyFilter: { titleContains: "KLOGH", arrayFilter: { threshold: 100.0, operator: GT } }
    includeStatistics: true
    limit: 14
  ) {
    objects { uuid title properties { title kind matchingCells { count total fraction } } }
  }
}
```

```graphql
# All log curves on a well frame
{
  objectRelations(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_WellboreFrameRepresentation"
    uuid: "0086eb99-eca4-485b-882a-af15bc9add89"
    direction: "both"
  ) { uuid name typeName direction }
}
```

---

### Deep Search — Multiple Dataspaces

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

### Array Statistics & Samples

```graphql
# Surface Z-values with statistics
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

```graphql
# IjkGrid geometry arrays
{
  objectArrays(
    dataspace: "maap/drogon"
    typeName: "resqml20.obj_IjkGridRepresentation"
    uuid: "0bc36994-2032-4e08-bad8-60ce0871002a"
    includeStatistics: true
    includeSampleValues: true
    sampleSize: 20
  ) { path dimensions totalElements statistics { minValue maxValue mean stdDev } sampleValues }
}
```

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
| Import/export EPC file | ETP CLI |
| Full XML of an object | RDDMS REST |

---

## Drogon Dataset Reference

| Category | Count | Details |
|----------|-------|---------|
| IjkGrids | 2 | Simgrid (107k cells), Geogrid (927k cells) |
| Grid2D surfaces | 48 | DS_ (depth), TS_ (time), GS_ (velocity) |
| Wells | 18 | Exploration (55/33-*), production (OP*) |
| Horizons | 6 | TopVolantis, BaseVolantis, TopTherys, TopVolon, MSL, BaseVelmodel |
| Continuous Properties | 215 | Grid properties + well logs |
| Discrete Properties | 82 | Zone, Region, FaultBlock, Facies |
| Well log frames | 14 | ~20 curves per well |
| Total objects | 589 | |
| Total arrays | 618 | |

### IjkGrid Properties

| Property | Range (Simgrid) | Description |
|----------|-----------------|-------------|
| PORO | 0 – 0.36 | Porosity |
| PERMX/Y | 0 – 4,278 | Horizontal perm (mD) |
| PERMZ | 0 – 2,497 | Vertical perm (mD) |
| SWATINIT | 0 – 1.0 | Initial water saturation |
| ntg_pem | 0 – 1.0 | Net-to-gross |
| FWL | 0 – 1,677 | Free water level (m) |
| GOC | 0 – 1,648 | Gas-oil contact (m) |
| Zone | 1 – 5 | Stratigraphic zone (discrete) |
| Region | 1 – 6 | Region index (discrete) |
| SATNUM | 1 – 6 | Saturation region (discrete) |

### Well Log Curves

| Curve | Range | Description |
|-------|-------|-------------|
| PHIT | 0 – 0.40 | Total porosity |
| KLOGH | 0 – 5,000 | Permeability (mD) |
| VSH | 0 – 1.0 | Shale volume |
| DENS | 1.8 – 2.8 | Bulk density (g/cm³) |
| AI | 4,000 – 12,000 | Acoustic impedance |
| VP / VS | 2,000–5,000 / 1,000–3,000 | P/S-wave velocity (m/s) |
| Sw | 0 – 1.0 | Water saturation |
| Facies | 0 – 5 | Lithofacies (discrete) |

### Key UUIDs

| Object | UUID |
|--------|------|
| Simgrid (IjkGrid) | `0bc36994-2032-4e08-bad8-60ce0871002a` |
| Geogrid (IjkGrid) | `2c6de928-7e08-4601-b979-34048bd68c02` |
| TopVolantis (HorizonInterp) | `02e954a9-d7db-4b57-aef7-12b8ebf47a65` |
| BaseVolantis (HorizonInterp) | `3657ca0b-d21f-41ca-801b-4a6a7eb1f426` |
| DS_interp surface (Grid2D) | `02a9d0b6-1f7c-4553-994b-5060cd725d6d` |
| Well 55_33-A-1 (Feature) | `50495987-88f4-4e39-95c8-0b2624298c47` |
| Log frame (55_33-A-2) | `0086eb99-eca4-485b-882a-af15bc9add89` |
| Local Depth CRS | `0a0ae03b-aee1-4651-8f11-5433eeda0ec2` |

---

## Setup — Local PostgreSQL

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

## Links

| Resource | URL |
|----------|-----|
| OSDU Search | [community.opengroup.org](https://community.opengroup.org/osdu/platform/system/search-service) |
| RDDMS / OpenETPServer | [community.opengroup.org](https://community.opengroup.org/osdu/platform/domain-data-mgmt-services/reservoir/open-etp-server) |
| ETP 1.2 Spec | [energistics.org](https://www.energistics.org/energistics-transfer-protocol/) |
| RESQML 2.0/2.2 | [energistics.org](https://www.energistics.org/resqml/) |
| Strawberry GraphQL | [strawberry.rocks](https://strawberry.rocks/) |
| ORES GraphQL module | `app/graphql_router.py` |
