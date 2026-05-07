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
| **RddmsAdmin** (`/`) | List and manage OSDU Dataspaces - create, lock, unlock, delete, build manifests |
| **RddmsQuery** (`/keys`) | Browse dataspaces, record types, individual objects; inspect table & graph data |
| **OsduSearch** (`/search`) | Query OSDU Search API with kind-specific cards (BusinessDecision, REV, Risk, GeoLabelSet) |
| **Analyse** (`/analyse`) | Compare Business Decisions across decision gates (DG1-DG4) with volume/risk/economics deltas and charts |
| **Add DG** (`/add-dg`) | Create and ingest a new BusinessDecision, linking REV, GeoLabelSet, Activity and Risk records |
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

The app supports two authentication modes tried in order:

| Priority | Mode | When used |
|----------|------|-----------|
| 1 | **Instance token** | `INSTANCE_<NAME>_REFRESH_TOKEN` or `_CLIENT_SECRET` set in `k8s/secret.yaml` - zero-click, shared across all users |
| 2 | **Per-user PKCE** | No shared token - each browser user is redirected to Azure AD login once |

### Per-user PKCE login (remote/multi-user deployments)

When no shared instance token is configured, the app redirects to Azure AD, performs an OAuth2 Authorization Code + PKCE exchange, and stores the resulting tokens.

**Flow:**

1. User redirected to Azure AD, signs in with Equinor/Azure account.
2. App receives `access_token` + `refresh_token` + `id_token`.
3. `id_token` decoded to extract user's stable Azure AD Object-ID (`oid`) and UPN.
4. `refresh_token` persisted to local SQLite (`app/tokenstore.py`) keyed by `oid`.
5. **30-day** signed session cookie set in browser.

**Subsequent visits:**

- Valid session cookie + fresh AT cache - served immediately.
- Expired AT - silently refreshed from encrypted RT in SQLite.
- Server restarted - `oid` from session cookie looks up persisted RT, mints new AT (no re-login).
- Session cookie expired (>30 days) - user must log in again.

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

## Tests

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
| BD guide | [md/BdDemo.md](BdDemo.md) |
| SeisInt guide | [md/SeisInt.md](SeisInt.md) |
| Strat guide | [md/StratColumn.md](StratColumn.md) |
| CRS guide | [md/CrsGuide.md](CrsGuide.md) |
| FMU-OSDU | [md/FmuOsdu.md](FmuOsdu.md) |
| Risk guide | [md/Risk.md](Risk.md) |
| Uncertainty | [md/Uncertainty.md](Uncertainty.md) |
| Volumes | [md/Volumes.md](Volumes.md) |
| GeoLabelSet | [md/GeoLabelSet.md](GeoLabelSet.md) |
| Query guide | [md/Query.md](Query.md) |
| DevConcept | [md/DevConcept.md](DevConcept.md) |
