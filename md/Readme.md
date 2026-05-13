# ORES - Architecture & Operations Guide

> Comprehensive reference for the ORES web client, demo pipelines, authentication, deployment and internals.
> For quick-start instructions see the [root readme](../readme.md).

---

## Table of Contents

1. [What the client can do](#what-the-client-can-do)
2. [Requirements](#requirements)
3. [Authentication & sessions](#authentication--sessions)
4. [Kubernetes deployment](#kubernetes-deployment)
5. [Project layout](#project-layout)
6. [Demo pipelines](#demo-pipelines)
7. [Demo datasets](#demo-datasets)
8. [Token tools](#token-tools)
9. [Performance & caching](#performance--caching)
10. [BD enrichment overlay](#bd-enrichment-overlay)
11. [Tests](#tests)
12. [Links](#links)

---

## What the client can do

| Page | Purpose |
|------|---------|
| **ResDdmsAdmin** (`/`) | List and manage OSDU Dataspaces - create, lock, unlock, delete, build manifests |
| **ResDdmsQuery** (`/keys`) | Browse dataspaces, record types, individual objects; inspect table & graph data |
| **Resqml3D** (`/viz`) | Multi-object 3D viewer – render entire dataspaces or selected RESQML objects in Three.js |
| **GlobalSearch** (`/search`) | Query OSDU Search API with kind-specific cards (BusinessDecision, REV, Risk, GeoLabelSet) |
| **AnalyseDG** (`/analyse`) | Compare Business Decisions across decision gates (DG1-DG4) with volume/risk/economics deltas and charts |
| **Add DG** (`/add-dg`) | Create and ingest BusinessDecision, Activity, ActivityTemplate, CollaborationProject, PersistedCollection and generic records. See [Activity guide](/howto/activity) |
| **Stratigraphy** (`/strat`) | Preview and ingest stratigraphic column records |
| **GraphQL** (`/graphql`) | GraphiQL IDE for RESQML deep-search queries |
| **HowTo** (`/howto`) | Browse grouped markdown documentation articles |

### Key rendering features

- **BD cards** - gradient header, headline volume KPIs (three-tier fallback: stat WPC - GeoLabelSet - ext.equinor), development concept, reservoir properties, economics, schedule, production forecast chart, alternatives, risk chips, uncertainties, authors & governance.
- **REV cards** - teal-themed with P10/P50/P90 headlines, metadata highlights, full volume table.
- **Analyse comparison** - gate timeline, side-by-side metric deltas (STOIIP, NPV, CAPEX, etc.), risk diff chips, property diffs, Chart.js overlay charts.
- **Mermaid relationship graphs** - interactive record-relationship diagrams with ancestry, data references, type-based styling.

---

## Requirements

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.11+ | 3.12 recommended |
| FastAPI | 0.115+ | |
| uvicorn | 0.32+ | |
| httpx | latest | |
| strawberry-graphql | 0.220+ | GraphQL schema + FastAPI integration |
| asyncpg | 0.29+ | Direct PostgreSQL (optional, for GraphQL deep-search) |
| Chart.js 4 | CDN | No npm install |
| Mermaid 10 | CDN | No npm install |

### Configuration (k8s YAML)

| File | In git? | Content |
|------|---------|---------|
| `k8s/configmap.yaml` | Yes | Hostnames, partitions, legal tags, app settings |
| `k8s/secret.yaml` | **No** (gitignored) | Tenant IDs, client IDs, tokens, API keys |
| `k8s/secret.yaml.template` | Yes | Empty template - copy to `secret.yaml` and fill in |

Each OSDU instance is defined by `INSTANCE_<NAME>_*` env vars split across both files.

---

## Authentication & sessions

The auth middleware resolves an access token for every request using a **fallback chain**. Each step is tried in order; the first to succeed wins:

| Priority | Strategy | Source | When it kicks in |
|----------|----------|--------|------------------|
| 0 | **Instance token** | `INSTANCE_<NAME>_REFRESH_TOKEN` or `_CLIENT_SECRET` | Always tried first — zero-click, shared across all browser sessions |
| 1 | **Env token** | Top-level `REFRESH_TOKEN` env var | Legacy single-instance setups (migration aid) |
| 2 | **Per-user PKCE** | User's own Azure AD sign-in | Fallback when steps 0 & 1 fail — user clicks "Sign in with Microsoft" |
| 3 | **Redirect** | — | No token at all — browser gets `/login-page`, API gets `401` |

### Per-instance flexibility

Different instances can use different credentials. The middleware doesn't care — it calls `inst.get_access_token()` which tries `refresh_token` first, then `client_credentials`.

| Instance | Secrets configured | `auth_mode` | Behaviour |
|----------|-------------------|-------------|----------|
| `eqndev` | `_REFRESH_TOKEN` | `refresh_token` | Auto-token via shared RT; PKCE fallback if RT expires |
| `eqndev` | `_CLIENT_SECRET` | `client_credentials` | Auto-token via service principal; PKCE fallback if secret expires |
| `eqndev` | Both | `refresh_token+client_credentials` | Tries RT first, then SP, then PKCE |
| `preship` | `_CLIENT_SECRET` | `client_credentials` | Service principal only; PKCE fallback if secret expires |

> **Key point:** PKCE login is **always available** regardless of the instance's primary auth mode.
> The "Sign in with Microsoft" button appears on every page and on the login page.
> This means an expired client secret doesn't lock users out — they can still sign in with their own Equinor account.

### For admins — minting & rotating the shared refresh token

The shared refresh token gives all visitors zero-click OSDU access.
An admin mints it once via the CLI helper and stores it in `k8s/secret.yaml`:

```bash
# Step 1 — generate PKCE auth URL
python demo/mint_refresh_token.py

# Step 2 — sign in in your browser; copy the localhost:8400 callback URL
python demo/mint_refresh_token.py --callback "<callback URL>"
```

The script prints the refresh token. Update the secrets:

```yaml
# k8s/secret.yaml
INSTANCE_EQNDEV_CLIENT_ID:      "21b442a9-6c1c-4551-b234-afdf010dd3be"
INSTANCE_EQNDEV_SCOPE:          "https://energy.azure.com/.default openid offline_access"
INSTANCE_EQNDEV_REFRESH_TOKEN:  "<token from script>"
```

For Radix deployments, set the same values in **Radix Console → ores → dev → Secrets**.

**Token rotation:** Azure AD may issue a new refresh token on every use.
The middleware auto-rotates it in memory (`auth.py` line ~82), so the original
token in `secret.yaml` becomes stale silently. If the pod restarts and the
old token no longer works, re-run `mint_refresh_token.py`.

**App registration checklist (Azure Portal → App registrations → `21b442a9-...`):**

| Setting | Value |
|---------|-------|
| Redirect URIs (Web) | `http://localhost:8400/callback` (CLI minting) |
|  | `http://localhost:8000/auth/callback` (local dev) |
|  | `https://web-ores-dev.c3.radix.equinor.com/auth/callback` (Radix dev) |
|  | `https://web-ores.c3.radix.equinor.com/auth/callback` (Radix prod) |
| Supported account types | Accounts in this organizational directory only (Equinor) |
| Allow public client flows | Yes (recommended, but optional if `client_secret` is always supplied) |
| API permissions | `https://energy.azure.com/.default` (Energy Platform) |

> **Tip:** CLI arguments `--client-id`, `--tenant`, `--scope` let you mint
> tokens for any app registration, not just the default `21b442a9` app.

---

### For users — signing in

End users **do not need any setup**. Authentication is fully automatic:

| Scenario | What happens |
|----------|--------------|
| Shared token is healthy (Mode 0) | Every visitor is authenticated instantly — no login required |
| Shared token expired / missing | User sees the **Sign in with Microsoft** button on the login page |
| After clicking Sign in | Browser redirects to Azure AD (Equinor tenant), user signs in |
| After Azure AD sign-in | Tokens are exchanged via PKCE, stored server-side — user lands on `/` |
| Subsequent visits (same browser) | Session cookie (30 days) re-uses stored tokens — no re-login |
| Access token expires (~1 h) | Silently refreshed from the per-user refresh token |
| Pod restart | Session cookie + SQLite lookup = seamless, no re-login |
| Session cookie expires (>30 d) | User must sign in again |
| Logout | Click **Logout** — session + stored tokens are cleared |

**Who can sign in:**

- Any Equinor employee whose Entra ID account has been granted access
  to the OSDU Energy Platform (the `energy.azure.com` API resource).
- No per-user configuration, token minting, or admin action is required.
- The app registration's audience and tenant restriction controls who
  is allowed (single-tenant: Equinor directory only).

**What users see on the login page:**

1. Instance selector (if multiple OSDU instances are configured).
2. Status badge showing whether the shared token is healthy.
3. **"Sign in with Microsoft"** button.
4. Note explaining that signing in is optional when the shared token works.

---

### Per-user PKCE login (technical detail)

When no shared instance token is configured (or it fails), the app performs an OAuth2 Authorization Code + PKCE exchange:

**Flow:**

1. User clicks "Sign in with Microsoft" → redirected to Azure AD.
2. Azure AD authenticates the user (Equinor SSO) and redirects back to `/auth/callback`.
3. App exchanges the authorization code for `access_token` + `refresh_token` + `id_token`.
4. `id_token` decoded to extract user's stable Azure AD Object-ID (`oid`) and UPN.
5. `refresh_token` persisted to local SQLite (`app/tokenstore.py`) keyed by `(oid, instance_name)`.
6. **30-day** signed session cookie set in browser — carries only `oid` + `instance_name`.

**Subsequent visits:**

- Valid session cookie + fresh AT cache → served immediately.
- Expired AT → silently refreshed from encrypted RT in SQLite.
- Server restarted → `oid` from session cookie looks up persisted RT, mints new AT (no re-login).
- Session cookie expired (>30 days) → user must log in again.

Users only re-authenticate if their Azure AD refresh token expires (90 days inactivity) or they click **Logout**.

### Token database

| Setting | Default | Override |
|---------|---------|----------|
| DB path (k8s) | `/data/ores_tokens.db` | `TOKEN_DB_PATH` env var |
| DB path (local) | `./ores_tokens.db` | `TOKEN_DB_PATH` env var |
| Encryption | Fernet (derived from `SECRET_KEY`) | Automatic when `cryptography` installed |

Schema: `sessions(oid, instance_name, refresh_token_enc, upn, updated_at)` with PK `(oid, instance_name)`.
Refresh tokens encrypted at rest. Access tokens cached in-memory only. Session cookie carries only `oid` + `instance_name`.

---

## Kubernetes deployment

```bash
kubectl apply -k k8s/
```

### Required secrets

| Key | Purpose |
|-----|---------|
| `SECRET_KEY` | Signs session cookies and encrypts refresh tokens - must be identical across replicas |
| `INSTANCE_<NAME>_TENANT_ID` | AD tenant for the OSDU instance |
| `INSTANCE_<NAME>_CLIENT_ID` | App registration client ID |
| `INSTANCE_<NAME>_REFRESH_TOKEN` | Shared refresh token (optional - enables zero-click mode) |
| `INSTANCE_<NAME>_CLIENT_SECRET` | Service principal secret (alternative to refresh token) |

### Persisting the token database

Mount a PVC at `/data` so refresh tokens survive pod restarts:

```yaml
# PVC (add to k8s/)
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ores-data
  namespace: ores
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 100Mi
```

```yaml
# In deployment.yaml
volumeMounts:
  - name: ores-data
    mountPath: /data
volumes:
  - name: ores-data
    persistentVolumeClaim:
      claimName: ores-data
```

> `replicas: 1` because SQLite does not support concurrent writers across pods.
> For multi-replica HA, replace SQLite with PostgreSQL or Redis.
>
> Set `HTTPS_ONLY=true` in production behind TLS to mark cookies as `Secure`.

---

## Project layout

```text
app/
  main.py              # FastAPI app: routes, BD enrichment, volume helpers
  auth.py              # PKCE sign-in, token exchange & refresh
  tokenstore.py        # SQLite-backed persistent refresh-token store
  common.py            # Shared helpers (access_token, search_reservoirs)
  osdu.py              # OSDU API client (Search, Storage, Workflow)
  cache.py             # Async TTL cache with thundering-herd protection
  instances.py         # OsduInstance dataclass & instance resolution
  ingest_router.py     # Manifest ingestion endpoints
  keys_router.py       # /keys page: browse dataspaces, types, objects
  resqml_viz.py        # RESQML 3D: PG SQL → XML/array → geometry JSON
  graphql_router.py    # GraphQL deep-search: PG-native + REST fallback
  analyse.py           # Analyse page: reservoir comparison across DGs
  addgate.py           # Add DG page: create new BusinessDecision records
  strat.py             # Stratigraphy manifest builders
  templates/           # Jinja2 HTML templates
  static/              # JS/CSS assets

demo/
  _auth.py             # Central auth module - token minting for all scripts
  gettoken.py          # CLI: mint token, list instances
  run_pipeline.py      # Generic cross-platform pipeline runner
  drogon/              # Drogon DG1 pipeline
  drogon_dg2/          # Drogon DG2 pipeline
  seisint/             # Volantis seismic interpretation pipeline
  strat/               # Stratigraphic column manifests and tools

test/                  # pytest test suite (147 tests)
md/                    # Documentation guides (rendered via /howto)
k8s/                   # Kubernetes manifests
```

---

## Demo pipelines

The Drogon pipeline (`demo/drogon/`) generates ~19 OSDU records from a single FMU export CSV:

| Step | Script | Output |
|------|--------|--------|
| 0 | `split_valysar.py` | Split CSV - volumes + parameters |
| 0b | `genrefpropertytypes_drogon.py` | Reference data (PropertyTypes, FacetRoles) |
| 1 | `genmaster_drogon.py` | Reservoir + Segments + WorkProduct |
| 2-3 | `genrawmanifest` / `genstatmanifest` | Raw & Stat REV |
| 4 | `genparamsmanifest_drogon.py` | Parameters ColumnBasedTable |
| 5 | `gen_risk_drogon.py` + `gen_activity_drogon.py` | Risk + Activity records |
| 6 | `gen_businessdecision_drogon.py` | BusinessDecision (DG1) with full linkage |
| 7-8 | `gengeolabelset` / `manifest2records` | GeoLabelSet + split to individual files |
| 9 | `ingest_records_batch.py` | PUT to OSDU Storage API |

```bash
python demo/run_pipeline.py                          # default: drogon_dg2
python demo/run_pipeline.py demo/drogon               # DG1 full pipeline
python demo/run_pipeline.py demo/drogon --skip-ingest  # generate only
python demo/run_pipeline.py demo/drogon --dry-run      # preview commands
python demo/run_pipeline.py --show demo/drogon_dg2     # show steps
python demo/run_pipeline.py --list                     # list profiles
```

The runner auto-discovers generator scripts by naming convention.

---

## Demo datasets

| Dataset | Pipeline | Records | Purpose |
|---------|----------|---------|---------|
| **Drogon DG1** | `demo/drogon/` | ~19 | Identify & Assess (7 segments, 3 facies) |
| **Drogon DG2** | `demo/drogon_dg2/` | ~31 | Concept Select (porosity scenario, PersistedCollection) |
| **Volantis SeisInt** | `demo/seisint/` | 66 | Seismic interpretation (faults, HCP, StructureMaps via RDDMS) |

---

## Token tools

Token minting is centralised in `demo/_auth.py` - single source of truth for
k8s YAML loading, instance resolution, and OAuth2 token exchange.

| File | Purpose |
|------|---------|
| `demo/_auth.py` | `get_token()`, `load_instance()`, `api_headers()`, `base_url()` |
| `demo/gettoken.py` | CLI: `--from-k8s`, `--list`, `--export`, `--json` |
| `app/get_token.py` | Minimal CLI: `--shell bash\|pwsh`, `--instance` |

```bash
python demo/gettoken.py eqndev --from-k8s      # mint a token
python demo/gettoken.py --list                  # list instances
```

---

## Performance & caching

| Feature | Module | Detail |
|---------|--------|--------|
| TTL cache | `app/cache.py` | `ttl_cache` decorator with per-key `asyncio.Lock` |
| Dataspace list | `app/osdu.py` | Cached 120 s |
| Reservoir search | `app/common.py` | Cached 90 s |
| API concurrency | `app/osdu.py` | Semaphore (default 20, `OSDU_MAX_CONCURRENT`) |
| Parallel fetches | analyse, addgate, keys | `asyncio.gather` for independent calls |

---

## BD enrichment overlay

OSDU's `BusinessDecision` schema drops custom `ext.equinor` keys during workflow ingestion.

**Workaround** (`app/main.py`):
1. `_load_bd_enrichments()` scans BD manifests at startup, caches `ext.equinor` data by record ID.
2. `_apply_bd_local_enrichment()` merges cached fields into OSDU-fetched records at render time. OSDU data wins for keys that exist in both.

---

## RESQML 3D Viewer (`/viz`)

The Resqml3D page renders RESQML objects from any dataspace in a shared
Three.js scene.  The backend constructs geometry JSON entirely from SQL
queries against the OpenETPServer PostgreSQL database — no REST calls in
the base case.

### Data flow: PostgreSQL → JSON

The OpenETPServer stores RESQML data in a PostgreSQL database with a
fixed schema per dataspace:

```text
admin.spaces   path, uid, dbfile  →  schema name (e.g. "ds_0001")
<schema>.res   guid, name, obj_id, typ_id          →  resource index
<schema>.obj   id, xml                              →  full RESQML XML
<schema>.typ   id, xml, uri_id                       →  type registry
<schema>.ary   obj_id, path, type, dim1..4, usize    →  array metadata
<schema>.bin   ary_id, idx, value (bytea)             →  array binary chunks
```

For **every object** the flow is:

```
┌──────────────┐   asyncpg     ┌──────────────────┐
│  Browser     │ ◄──── JSON ── │  resqml_viz.py   │
│  (viz.html)  │               │                  │
└──────────────┘               │  1. schema lookup│
                               │  2. XML parse    │
                               │  3. array decode │
                               │  4. build JSON   │
                               └────────┬─────────┘
                                        │ SQL
                               ┌────────▼─────────┐
                               │   PostgreSQL      │
                               │   (OpenETPServer) │
                               └──────────────────┘
```

**Step by step (example: PointSetRepresentation):**

1. **Schema lookup** — `admin.spaces` maps the dataspace path
   (e.g. `demo/Drogon`) to a PG schema name (e.g. `ds_0001`).
2. **Resource lookup** — `<schema>.res WHERE guid=$uuid` returns the
   internal `obj_id` and the object title.
3. **XML retrieval** — `<schema>.obj WHERE id=$obj_id` returns the full
   RESQML XML.  For types that encode geometry in XML (Grid2d lattice
   origin, offsets, CRS references) we parse it with `xml.etree`.
4. **Array listing** — `<schema>.ary WHERE obj_id=$obj_id` returns the
   paths and metadata for each HDF5-equivalent array
   (e.g. `points_patch0/points`, `zValues`).
5. **Binary decode** — `<schema>.bin WHERE ary_id=$id ORDER BY idx`
   returns binary chunks.  We concatenate them and `struct.unpack` into
   `float64`/`float32`/`int32` depending on `ary.type`.
6. **JSON construction** — the decoded arrays become `{kind, title,
   positions, indices, zmin, zmax, ...}` ready for Three.js.

This means: **no REST API, no HTTP overhead, no JSON roundtrips through
the RDDMS service**.  A single SQL connection reads XML metadata and raw
binary array data directly.

### Backend cascade (3 tiers)

Each viz fetch cascades through up to three backends:

| Priority | Backend | Env var | When used |
|----------|---------|---------|----------|
| 1 | Local PG | `GRAPHQL_PG_CONN_STRING` | Co-located with OpenETPServer (fastest, <50ms) |
| 2 | Remote PG | `RDDMS_PG_CONN_STRING` | Direct SQL to cloud-hosted RDDMS DB (prepared, not yet on ADME) |
| 3 | REST API | *(always available)* | Universal fallback via `/api/reservoir-ddms/v2/` |

Both PG tiers use the **same SQL helpers** (`_pg_schema_for_dataspace`,
`_pg_list_arrays`, `_pg_read_array`) — they just differ in which
`asyncpg` pool is used (local vs remote).

### Supported RESQML types

| RESQML Type | `kind` | Geometry source | Rendering |
|-------------|--------|----------------|-----------|
| Grid2dRepresentation | `surface` | XML lattice + z-value array | Triangulated mesh |
| TriangulatedSetRepresentation | `surface` | points + triangle-index arrays | Triangle mesh |
| PointSetRepresentation | `points` | points array | 3D point cloud |
| PolylineSetRepresentation | `polylines` | points + node-count arrays | Multi-polyline (faults, contours) |
| WellboreTrajectoryRepresentation | `trajectory` | control-points array | 3D polyline |
| DeviationSurveyRepresentation | `trajectory` | MD/incl/azimuth → min-curvature XYZ | 3D polyline |
| WellboreMarkerFrameRepresentation | `markers` | MD array + XML marker labels | Labelled 3D points |

### API endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/viz` | Dedicated multi-object 3D viewer page |
| GET | `/keys/viz/objects.json?ds=...` | List 3D-renderable objects grouped by type |
| POST | `/keys/viz/batch.json` | Batch-fetch geometry for up to 50 objects |
| GET | `/keys/object/geometry3d.json` | Single-object geometry (used by `/keys` page) |
| GET | `/keys/object/map.png` | Grid2d depth-map PNG rendering |

### Frontend (`viz.html`)

The viewer is a self-contained page with:
- **Layer panel** — dataspace selector, objects grouped by type, per-object
  checkboxes, select-all / deselect-all, load-selected / clear-scene.
- **Three.js viewport** — shared scene for all loaded objects, orbit/pan/zoom
  controls, auto-rotation, depth-coloured surfaces, palette-coloured lines.
- **Batch loading** — objects are fetched in parallel chunks of 20 via the
  batch API to keep load times reasonable.
- **Legend + HUD** — live object count, vertex count, colour key.

---

```bash
python -m pytest test/ -v
```

147 tests covering: auth modes, PKCE flow, token refresh, routes, k8s loading, instance discovery, token minting, caching, tokenstore, multiuser sessions.

---

## Links

| Resource | Path |
|----------|------|
| App entry | `app/main.py` |
| Auth | `app/auth.py` |
| Token store | `app/tokenstore.py` |
| OSDU client | `app/osdu.py` |
| Demo auth | `demo/_auth.py` |
| Pipeline runner | `demo/run_pipeline.py` |
| Activity guide | [/howto/activity](/howto/activity) |
| BD guide | [/howto/bd-demo](/howto/bd-demo) |
| SeisInt guide | [/howto/seismic-interp](/howto/seismic-interp) |
| Strat guide | [/howto/strat-column](/howto/strat-column) |
| CRS guide | [/howto/crs-guide](/howto/crs-guide) |
| Risk guide | [/howto/risk](/howto/risk) |
| Uncertainty | [/howto/uncertainty](/howto/uncertainty) |
| Volumes | [/howto/volumes](/howto/volumes) |
| GeoLabelSet | [/howto/geolabelset](/howto/geolabelset) |
| Query guide | [/howto/query-guide](/howto/query-guide) |
