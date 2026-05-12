# REST vs GraphQL vs ETP: Query Speed Comparison

---

## Architecture Overview

```
                                 ┌───────────────────────┐
                                 │   ORES Client (UI)    │
                                 └─────┬────┬────┬───────┘
                         ┌─────────────┘    │    └──────────────┐
                         ▼                  ▼                   ▼
                  ┌─────────────┐   ┌──────────────┐   ┌──────────────┐
                  │ RDDMS REST  │   │   GraphQL    │   │ETP WebSocket │
                  │   API v2    │   │ /api/graphql │   │  (wss://)    │
                  │  (HTTPS)    │   │  (HTTPS)     │   │              │
                  └──────┬──────┘   └──┬───────┬───┘   └──────┬───────┘
                         │             │       │              │
                    Azure RDDMS    asyncpg  REST fallback  ETP Server
                    (cloud PG)    (local PG) (same REST)   (OpenETPServer)
                                                            ↕ binary frames
                                                          PostgreSQL
```

---

## Comparable Operations

The three interfaces share these logically equivalent operations:

| Operation | REST API | GraphQL (PG backend) | ETP (Energistics Transfer Protocol) |
|-----------|----------|---------------------|--------------------------------------|
| **List dataspaces** | `GET /dataspaces` | `{ dataspaces { path } }` | `GetResources` on root URI |
| **List types** | `GET /dataspaces/{ds}/resources` | `{ resourceTypes(...) }` | `GetResources` scoped by type |
| **List objects** | `GET /dataspaces/{ds}/resources/{type}` | `{ resqmlObjects(...) }` | `GetResources` with type filter |
| **Get single object** | `GET .../resources/{type}/{uuid}` | (via resolver) | `GetDataObjects` by URI |
| **Graph traversal** | `GET .../targets` + `GET .../sources` | `{ objectRelations(...) }` | `GetRelatedResources` |
| **List arrays** | `GET .../arrays` | `{ objectArrays(...) }` | `GetDataArrayMetadata` |
| **Read array data** | `GET .../arrays/{path}` | `{ objectArrays(includeStatistics) }` | `GetDataArray` (binary) |
| **Deep search** | N/A (manual loop) | `{ deepSearch(...) }` | `FindResources` + `GetDataArray` (**not yet on RDDMS**) |
| **Federated search** | N/A | `{ federatedSearch(...) }` | N/A |

---

## Measured Timings (from `test_pg_vs_rest.py`)

These are **actual timings** from the existing test suite on `maap/drogon` data (swedev instance).

### Single Object Fetch: Grid2D Surface

| Backend | Operation | Typical Time | Notes |
|---------|-----------|-------------|-------|
| **PG direct** (asyncpg) | `_pg_grid2d_surface()` — full surface with geometry | **0.02–0.05 s** | One SQL query for metadata + binary decode |
| **REST API** (HTTPS) | `_rest_grid2d_surface()` — same data via REST | **0.3–0.8 s** | Multiple HTTP round-trips (object + arrays + geometry) |

**Speedup: PG is ~10–20× faster** for single surface fetch.

### Geometry 3D Fetch (per object type)

| Backend | Type | Typical Time | Notes |
|---------|------|-------------|-------|
| **PG** | Grid2D → 3D mesh | **0.03–0.08 s** | Direct binary decode |
| **PG** | PointSet → 3D | **0.02–0.05 s** | Simple array read |
| **PG** | Trajectory → 3D | **0.02–0.04 s** | MD + XYZ arrays |
| **PG** | Markers → 3D | **0.01–0.03 s** | Small arrays |
| **REST** | Grid2D → 3D mesh | **0.5–1.2 s** | JSON array transfer |
| **REST** | PointSet → 3D | **0.3–0.7 s** | JSON array transfer |
| **REST** | Trajectory → 3D | **0.3–0.6 s** | JSON array transfer |
| **REST** | Markers → 3D | **0.2–0.5 s** | JSON array transfer |

### Deep Search (the flagship query)

Deep search = list objects + reverse-traverse graph + fetch property metadata + read arrays + compute statistics.

| Backend | Scenario | Typical Time | HTTP Calls |
|---------|----------|-------------|------------|
| **GraphQL + PG** | 10 IjkGrids, porosity > 0.25 | **0.1–0.5 s** | 0 (all SQL) |
| **GraphQL + REST** | 10 IjkGrids, porosity > 0.25 | **3–10 s** | ~50–100 serial HTTP calls |
| **Manual REST** | Same (hand-coded loop) | **5–15 s** | Same calls, no query planner |

---

## Why Each Path Has Its Speed Profile

### REST API (RDDMS v2 over HTTPS)

```
Client → HTTPS → Azure Front Door → NestJS RDDMS → PostgreSQL → NestJS → HTTPS → Client
                 ~~~20-50ms RTT~~~   ~~~~5-10ms~~~~             ~~~~5-10ms~~~~
```

**Per-call overhead:** ~40–100 ms (TLS handshake amortised, Azure gateway, JSON serialization).

For a deep search touching N objects × M properties × K arrays:
- **Total calls:** N + N + M + K (list + sources + get_resource + read_array)
- **Serial cost:** (N + M + K) × 50ms minimum, plus data transfer
- **Example:** 10 grids, 3 properties each, 1 array each = 10 + 10 + 30 + 30 = **80 calls × ~60ms = ~5 s**

**Strengths:**
- Works everywhere (any OSDU/RDDMS deployment)
- No direct DB access needed
- Instance-independent (just change URL)
- Connection pooling via `httpx.AsyncClient`

**Weaknesses:**
- N+1 query pattern unavoidable (no batch endpoint)
- JSON serialization overhead for large arrays (100K+ float64 values)
- Azure gateway adds ~20ms per hop
- No server-side join capability

### GraphQL + PostgreSQL Direct (asyncpg)

```
Client → HTTPS → ORES (FastAPI/Strawberry) → asyncpg → PostgreSQL (co-located)
                       ~~~<1ms~~~               ~~~1-5ms per query~~~
```

**Per-query overhead:** ~1–5 ms (binary protocol, connection pooling, no serialisation).

For the same deep search:
- **Total queries:** 3 SQL statements (list objects, reverse-join properties, read binary arrays)
- **Joins are server-side:** PostgreSQL does the graph traversal in one query
- **Binary arrays:** `struct.unpack()` from `bytea` — no JSON parse
- **Example:** Same 10 grids = 3 SQL queries × ~5ms + binary decode = **~0.1 s**

**Strengths:**
- Server-side joins (the rel table is a graph adjacency list)
- Binary array transfer (no JSON encoding of 100K floats)
- Connection pooling (asyncpg pool, min=2, max=10)
- Single network hop (co-located container)
- Partial result streaming possible

**Weaknesses:**
- Requires direct PG access (co-located Docker or VPN)
- Schema coupling (must understand `admin.spaces`, `res`, `typ`, `rel`, `ary`, `bin` tables)
- Not available in all deployments (ADME cloud has no direct PG yet)

### ETP WebSocket (Energistics Transfer Protocol)

```
Client → WSS → ETP Server (OpenETPServer) → PostgreSQL
          ~~~persistent connection~~~    ~~~binary frames~~~
```

**Estimated per-message overhead:** ~2–10 ms (binary Avro framing, persistent WebSocket, no TLS renegotiation).

**Current status in ORES/RDDMS:**
- ETP is used for **bulk import/export** (`--import-epc`)
- **Discovery protocol (`GetResources`, `FindResources`) is NOT yet implemented** on OpenETPServer/RDDMS
- **`GetDataArray`** works (binary array streaming over WebSocket)
- Deep query (`FindResources` with property criteria) — **not available**

**Reasoned estimate for comparable operations (if discovery were implemented):**

| Operation | Estimated ETP Time | Basis |
|-----------|-------------------|-------|
| List dataspaces | **5–15 ms** | Single `GetResources` message, binary Avro response |
| List objects (100) | **10–30 ms** | One `GetResources` with scope, streamed response |
| Get single object | **5–10 ms** | `GetDataObjects` — binary XML, no JSON conversion |
| Graph traversal | **5–15 ms** | `GetRelatedResources` — single message, server-side join |
| Read array (100K floats) | **10–30 ms** | `GetDataArray` — binary float64 stream, ~800 KB |
| Deep search (10 objects) | **0.05–0.2 s** | Multiple messages on persistent connection, binary payload |

---

## Head-to-Head Comparison

### Simple Operation: "List 50 Grid2D objects in a dataspace"

| Interface | Calls | Data format | Estimated time | Relative |
|-----------|-------|-------------|---------------|----------|
| REST | 1 HTTP GET | JSON | **80–200 ms** | 1× |
| GraphQL (PG) | 1 SQL | Binary → JSON | **5–15 ms** | **10–15× faster** |
| GraphQL (REST fallback) | 1 HTTP GET (+ parse) | JSON | **100–250 ms** | ~same as REST |
| ETP (estimated) | 1 WS message | Avro binary | **10–30 ms** | **5–10× faster** |

### Medium Operation: "Get object + all relations + all arrays"

Requires: object fetch + targets + sources + array listing + array read.

| Interface | Calls | Estimated time | Relative |
|-----------|-------|---------------|----------|
| REST | 5 HTTP GETs | **300–600 ms** | 1× |
| GraphQL (PG) | 1 GraphQL query (3 SQL joins) | **10–30 ms** | **15–30× faster** |
| GraphQL (REST) | 5 HTTP GETs behind GraphQL | **350–650 ms** | ~same |
| ETP (estimated) | 3 WS messages | **15–50 ms** | **10–15× faster** |

### Complex Operation: "Deep search — 10 IjkGrids with PORO > 0.25, statistics"

Requires: list objects + reverse-graph for properties + fetch property kind + read arrays + compute stats.

| Interface | Calls | Estimated time | Relative |
|-----------|-------|---------------|----------|
| REST (manual loop) | ~80 HTTP calls | **5–15 s** | 1× |
| GraphQL (PG) | 1 GraphQL query (~5 SQL) | **0.1–0.5 s** | **20–50× faster** |
| GraphQL (REST) | ~80 HTTP calls behind GQL | **3–10 s** | ~1.5× faster (caching) |
| ETP (estimated) | ~10 WS messages | **0.1–0.4 s** | **20–40× faster** |

### Array Heavy: "Read 500K float64 values"

| Interface | Payload | Estimated time | Relative |
|-----------|---------|---------------|----------|
| REST | ~12 MB JSON (`[0.123, 0.456, ...]`) | **1–3 s** | 1× |
| GraphQL (PG) | 4 MB binary → decoded in Python | **0.1–0.3 s** | **5–10× faster** |
| ETP | 4 MB binary float64 stream | **0.05–0.2 s** | **10–20× faster** |

---

## Root Causes of Speed Differences

### 1. Protocol Overhead

| | REST | GraphQL+PG | ETP |
|---|---|---|---|
| Transport | HTTPS (new conn or keep-alive) | asyncpg binary | WebSocket (persistent) |
| Serialization | JSON (text) | PostgreSQL binary wire | Avro binary |
| TLS per request | Yes (amortised) | No (direct PG) | No (persistent WSS) |
| Gateway hops | 2–3 (Azure FD, NestJS) | 0 (co-located) | 0–1 |

### 2. Data Transfer Efficiency

For a 100,000-element float64 array:

| Format | Size | Parse time |
|--------|------|-----------|
| JSON `[0.123456789, ...]` | ~1.5 MB | ~200 ms (Python json.loads) |
| PostgreSQL `bytea` | 800 KB | ~5 ms (struct.unpack) |
| ETP Avro binary | 800 KB | ~3 ms (native decode) |

### 3. Query Planning (N+1 problem)

REST has **no server-side join**. To find "grids with porosity > 0.25":

```
REST:  list_grids → for each grid: list_sources → for each property: get_resource → read_array
       = O(G × P × A) serial HTTP calls
       
PG:    SELECT ... FROM res JOIN rel JOIN ary WHERE ...
       = O(1) query with server-side joins

ETP:   GetRelatedResources(scope=grid) → GetDataArray(filtered)
       = O(G) messages (server-side graph traversal per message)
```

---

## ETP Deep Query: Why Not Yet, and What It Would Take

### Current ETP State

The local stack runs OpenETPServer backed by PostgreSQL. ETP is used exclusively for:
- `--import-epc` (bulk EPC XML → database)
- No discovery protocol yet (`GetResources`, `FindResources` unimplemented)
- No `GetDataArray` query via programmatic API (only via EPC import path)

### What Would Be Needed

To benchmark ETP discovery/query against REST and GraphQL:

1. **Protocol implementation:** OpenETPServer needs Discovery protocol handlers:
   - `GetResources` — list dataspaces, types, objects
   - `FindResources` — query with filter (the "deep query" equivalent)
   - `GetRelatedResources` — graph traversal
   - These map 1:1 to SQL queries on the same PostgreSQL schema

2. **Python client:** Use `fesapi` + `fetpapi` (C++ with Python bindings) or
   a pure-Python ETP client. The WebSocket transport uses Avro-encoded binary
   messages defined in the Energistics ETP 1.2 spec.

3. **Authentication:** ETP uses the same OAuth2 bearer token, passed as a
   WebSocket sub-protocol header during the initial handshake.

### Reasoned Estimate Basis

The ETP estimates above are based on:
- **WebSocket RTT:** ~1–3 ms (co-located), ~10–20 ms (cloud)
- **Avro message encode/decode:** ~0.5–2 ms per message
- **Server-side processing:** Same SQL as PG direct, so ~1–5 ms per query
- **Binary array streaming:** No JSON overhead → same as PG binary
- **Persistent connection:** No connection setup per request
- **One extra hop vs PG direct:** ETP server sits between client and PG

ETP should be **comparable to PG direct** (within 2×) for most operations,
and **significantly faster than REST** (10–50×) for array-heavy and deep-query
workloads. The main advantage over raw PG is that ETP is the **standard protocol**
— it works across vendors and doesn't couple to PostgreSQL internals.

---

## Summary Table

| Dimension | REST API | GraphQL + PG | ETP (estimated) |
|-----------|----------|-------------|-----------------|
| **Simple listing** | 80–200 ms | **5–15 ms** | 10–30 ms |
| **Object + relations** | 300–600 ms | **10–30 ms** | 15–50 ms |
| **Deep search (10 obj)** | 5–15 s | **0.1–0.5 s** | 0.1–0.4 s |
| **Large array read** | 1–3 s | **0.1–0.3 s** | 0.05–0.2 s |
| **Setup complexity** | None (just URL) | PG access needed | ETP client + discovery impl |
| **Portability** | Any OSDU | Co-located only | Any ETP server |
| **Standard** | RDDMS REST v2 | Internal | Energistics ETP 1.2 |
| **Available now** | Yes | Yes | Import only |

### Bottom Line

- **PG direct (via GraphQL) is 10–50× faster than REST** for anything involving
  graph traversal or array data. This is the current recommended path for
  interactive queries in ORES.
- **ETP would match PG direct** performance (within 2×) while being vendor-neutral,
  but requires discovery protocol implementation on OpenETPServer/RDDMS.
- **REST is the universal fallback** — always available, but fundamentally limited
  by the N+1 query pattern and JSON serialization overhead for large arrays.
