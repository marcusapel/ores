# OSDU RDDMS admin UI - web client and demo toolkit

A FastAPI-based administrative UI for an OSDU-style RDDMS (Reservoir Data / Decision Management System) plus a demo pipeline toolkit for generating, ingesting and comparing Business Decisions, Volumes, Risks and Stratigraphy records.

---

## Quick start

```bash
# 1. Clone
git clone https://github.com/equinor/ores.git
cd ores

# 2. Install dependencies (Python 3.11+)
pip install -r requirements.txt

# 3. Configure secrets
cp k8s/secret.yaml.template k8s/secret.yaml
# Edit k8s/secret.yaml - fill in your OSDU tenant IDs, client IDs, tokens, API keys
# Edit k8s/configmap.yaml - verify hostnames, partitions, legal tags
# secret.yaml is gitignored - never commit it

# 4. Run
eval "$(python k8s/env_from_k8s.py)" && python -m uvicorn app.main:app --reload --port 8000 --host 127.0.0.1
```

Open <http://127.0.0.1:8000/> in a browser.

### Run with Docker (alternative)

```bash
docker build -t ores .
docker run -p 8000:8000 --env-file <(python k8s/env_from_k8s.py | sed 's/^export //') ores
```

### Troubleshooting

- **Missing env vars?** - Run `python k8s/env_from_k8s.py` standalone to inspect what gets exported.
- **Auth issues?** - Ensure `SECRET_KEY`, tenant IDs, and client IDs are set in `k8s/secret.yaml`.
- **Port in use?** - Change `--port 8000` to another port.

---

## Pages

| Route | Page | Purpose |
|-------|------|---------|
| `/` | RddmsAdmin | Manage OSDU Dataspaces - create, lock, unlock, delete, build manifests |
| `/keys` | RddmsResources | Browse dataspaces, record types, individual objects; inspect table & graph data |
| `/search` | OsduSearch | Query the OSDU Search API with kind-specific cards (BD, REV, Risk, GeoLabelSet) |
| `/analyse` | Analyse | Compare Business Decisions across decision gates (DG1→DG4) with volume/risk/economics deltas |
| `/add-dg` | Add DG | Create and ingest a new BusinessDecision, linking REV, GeoLabelSet, Activity and Risk records |
| `/strat` | Stratigraphy | Preview and ingest stratigraphic column records |
| `/howto` | HowTo | Grouped markdown documentation articles (BD modelling, SeisInt, CRS, Stratigraphy) |

### Key rendering features

- **BD cards** - gradient header, headline volume KPIs (three-tier fallback: stat WPC → GeoLabelSet → ext.equinor), development concept, reservoir properties, economics, schedule, production forecast chart, alternatives, risk chips, uncertainties, authors & governance.
- **REV cards** - teal-themed with P10/P50/P90 headlines, metadata highlights, full volume table.
- **Analyse comparison** - gate timeline, side-by-side metric deltas (STOIIP, NPV, CAPEX, etc.), risk diff chips, property diffs, synthesis insights, Chart.js overlay charts.
- **Mermaid relationship graphs** - interactive record-relationship diagrams with ancestry, data references, type-based styling.
- **Local BD enrichment overlay** - OSDU silently drops custom `ext.equinor` keys during ingestion; the app loads manifests at startup and merges them back at render time.

## Requirements

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.11+ | 3.12 recommended |
| FastAPI | 0.115+ | |
| uvicorn | 0.32+ | |
| httpx | latest | |
| Chart.js 4 | CDN | No npm install needed |
| Mermaid 10 | CDN | No npm install needed |

### Configuration (k8s YAML)

Config lives in two files under `k8s/`:

| File | In git? | Content |
|------|---------|---------|
| `k8s/configmap.yaml` | Yes | Hostnames, partitions, legal tags, app settings |
| `k8s/secret.yaml` | **No** (gitignored) | Tenant IDs, client IDs, tokens, API keys |
| `k8s/secret.yaml.template` | Yes | Empty template - copy to `secret.yaml` and fill in |

Each OSDU instance is defined by `INSTANCE_<NAME>_*` env vars split across both files.

---

## Authentication & persistent sessions

The app supports two authentication modes that are tried in order on every request:

| Priority | Mode | When used |
|----------|------|-----------|
| 1 | **Instance token** | `INSTANCE_<NAME>_REFRESH_TOKEN` or `INSTANCE_<NAME>_CLIENT_SECRET` set in `k8s/secret.yaml` - zero-click, shared across all users |
| 2 | **Per-user PKCE** | No shared token available - each browser user is redirected to Azure AD login once |

### Per-user PKCE login (remote/multi-user deployments)

When no shared instance token is configured (or for users who want their own identity), the app redirects to Azure AD, performs an OAuth2 Authorization Code + PKCE exchange, and stores the resulting tokens.

**What happens after first login:**

1. The user is redirected to Azure AD and signs in with their Equinor/Azure account.
2. The app receives an `access_token` + `refresh_token` and an `id_token`.
3. The `id_token` is decoded to extract the user's stable Azure AD Object-ID (`oid`) and UPN.
4. The `refresh_token` is persisted to a local SQLite database (`app/tokenstore.py`) keyed by `oid`.
5. A **30-day** signed session cookie is set in the browser.

**On subsequent visits (including after server restarts):**

- If the session cookie is still valid and the in-memory AT cache has a fresh token → served immediately.
- If the access token cache has expired → silently refreshed from the encrypted RT in SQLite.
- If the server restarted (memory cache empty) but the session cookie is still valid (within 30 days) → the `oid` in the cookie is used to look up the persisted refresh token from SQLite and mint a new access token automatically - **no re-login required**.
- If the session cookie itself has expired (browser deleted it) → user must log in again.

Users are only prompted to log in again if their Azure AD refresh token itself expires (typically 90 days of inactivity) or they explicitly click **Logout** (which also deletes the stored token from the DB).

### Token database

| Setting | Default | Override |
|---------|---------|----------|
| DB path (k8s) | `/data/ores_tokens.db` | `TOKEN_DB_PATH` env var |
| DB path (local) | `./ores_tokens.db` | `TOKEN_DB_PATH` env var |
| Encryption | Fernet (derived from `SECRET_KEY`) | Automatic when `cryptography` is installed |

The database is a single SQLite file with one table:

```
sessions(oid TEXT, instance_name TEXT, refresh_token_enc TEXT, upn TEXT, updated_at REAL)
PRIMARY KEY (oid, instance_name)
```

Refresh tokens are **encrypted at rest** using Fernet symmetric encryption derived from `SECRET_KEY`. Access tokens are cached **in-memory only** and never written to disk. The session cookie carries only the user's `oid` and `instance_name` - no tokens or personal data.

---

## Kubernetes deployment

### Basic deployment

```bash
# Apply all manifests
kubectl apply -k k8s/

# Or individually
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secret.yaml       # fill in from secret.yaml.template first
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
kubectl apply -f k8s/ingress.yaml
```

### Persisting the token database across pod restarts

Without a persistent volume the SQLite token DB lives in the pod's ephemeral filesystem and is lost on restart, forcing all users to re-login.  Mount a PVC at `/data`:

> **Note:** The deployment uses `replicas: 1` because SQLite does not support concurrent writers across pods.  For multi-replica HA, replace SQLite with a shared store (e.g. PostgreSQL, Redis).

**1. Create a PersistentVolumeClaim** (add to `k8s/` or inline in `deployment.yaml`):

```yaml
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

**2. Mount it in `k8s/deployment.yaml`:**

```yaml
spec:
  containers:
    - name: ores
      # ... existing config ...
      volumeMounts:
        - name: ores-data
          mountPath: /data
  volumes:
    - name: ores-data
      persistentVolumeClaim:
        claimName: ores-data
```

With this in place, the token DB at `/data/ores_tokens.db` survives pod restarts and redeployments, and remote users never need to re-authenticate.

### Required secrets (`k8s/secret.yaml`)

Copy `k8s/secret.yaml.template` → `k8s/secret.yaml` and fill in at minimum:

| Key | Purpose |
|-----|---------|
| `SECRET_KEY` | Signs session cookies **and** encrypts stored refresh tokens - must be identical across all replicas |
| `INSTANCE_<NAME>_TENANT_ID` | AD tenant for the OSDU instance |
| `INSTANCE_<NAME>_CLIENT_ID` | App registration client ID |
| `INSTANCE_<NAME>_REFRESH_TOKEN` | Shared refresh token (optional - enables zero-click mode) |
| `INSTANCE_<NAME>_CLIENT_SECRET` | Service principal secret (alternative to refresh token) |

> **Multi-replica deployments:** `SECRET_KEY` must be the same on every pod, otherwise session cookies from one pod are rejected by another and stored tokens cannot be decrypted.  Set it explicitly in `k8s/secret.yaml` rather than leaving it to auto-generate.
>
> Set `HTTPS_ONLY=true` in production deployments behind TLS to mark session cookies as `Secure`.

---

## Project layout

```text
app/
  main.py              # FastAPI app: routes, BD enrichment, volume helpers
  auth.py              # PKCE sign-in, token exchange & refresh
  tokenstore.py        # SQLite-backed persistent refresh-token store (per user)
  common.py            # Shared helpers (access_token, search_reservoirs, cached)
  osdu.py              # OSDU API client (Search, Storage, Workflow, semaphore)
  cache.py             # Async TTL cache with thundering-herd protection
  instances.py         # OsduInstance dataclass & instance resolution
  ingest_router.py     # Manifest ingestion endpoints
  keys_router.py       # /keys page: browse dataspaces, types, objects
  analyse.py           # Analyse page: reservoir comparison across DGs
  addgate.py           # Add DG page: create new BusinessDecision records
  schemahandler.py     # JSON schema & manifest helpers
  strat.py             # Stratigraphy manifest builders
  structuremap.py      # Structure-map helpers
  get_token.py         # CLI: mint access token (delegates to demo/_auth.py)
  templates/           # Jinja2 HTML templates
  static/              # JS/CSS assets

demo/
  _auth.py             # Central auth module - k8s/env/.env resolution & token minting
  gettoken.py          # CLI: mint token, list instances, --from-k8s, --export
  dataspacecopy.py     # Copy records between OSDU dataspaces
  run_pipeline.py      # Generic cross-platform pipeline runner
  drogon/              # Drogon DG1 pipeline (self-contained)
  drogon_dg2/          # Drogon DG2 pipeline (references drogon/ for shared data)
  seisint/             # Volantis seismic interpretation pipeline (RDDMS)
  strat/               # Stratigraphic column manifests and tools

tests/                 # pytest test suite (135+ tests)
md/                    # Documentation and guides (rendered via /howto)
k8s/                   # Kubernetes manifests (configmap, secret, deployment)
```

---

## Pipeline guide - adding a new field / decision gate dataset

See [md/BdDemo.md](md/BdDemo.md) for the full DG data model guide, including:

- Data input format (CSV structure, supported units, properties)
- Step-by-step pipeline walkthrough
- How to add a new property type or facet
- How to add a new field/reservoir to OSDU
- Reference data schemas and extension points

### Quick pipeline overview

The Drogon pipeline (`demo/drogon/`) generates ~19 OSDU records from a single FMU export CSV:

| Step | Script | Records |
|------|--------|---------|
| 0 | `split_valysar.py` | Split raw CSV → volumes + parameters |
| 0b | `genrefpropertytypes_drogon.py` + `genreffacetrole_drogon.py` | Reference data (PropertyTypes, FacetRoles) |
| 1 | `genmaster_drogon.py` | Reservoir + 7 Segments + WorkProduct |
| 2 | `genrawmanifest_drogon.py` | Raw REV (ColumnBasedTable with per-realisation volumes) |
| 3 | `genstatmanifest_drogon.py` | Stat REV (P10/P50/P90/Mean aggregated volumes) |
| 4 | `genparamsmanifest_drogon.py` | Parameters ColumnBasedTable (OWC, porosity) |
| 5 | `gen_risk_drogon.py` | Risk records |
| 5b | `gen_activity_drogon.py` | Activity + ActivityTemplate + ETPDataspace |
| 6 | `gen_businessdecision_drogon.py` | BusinessDecision (DG1) with full linkage |
| 6b | `gen_documents_drogon.py` | Document WPCs (SRA, CRA, PDO) |
| 7 | `gengeolabelset_drogon.py` | GeoLabelSet (headline KPI values) |
| 8 | `manifest2records_drogon.py` | Split manifests → individual record files |
| 9 | `ingest_records_batch.py` | PUT records to OSDU Storage API |

Run pipelines with the generic Python runner (cross-platform):

```bash
python demo/run_pipeline.py                          # default: drogon_dg2
python demo/run_pipeline.py demo/drogon               # DG1 full pipeline
python demo/run_pipeline.py demo/drogon_dg2            # DG2 pipeline
python demo/run_pipeline.py demo/drogon --skip-ingest  # generate only
python demo/run_pipeline.py demo/drogon --dry-run      # preview commands
python demo/run_pipeline.py --show demo/drogon_dg2     # show discovered steps
python demo/run_pipeline.py --list                     # list built-in profiles
```

The runner auto-discovers generator scripts by naming convention from any directory.
See `python demo/run_pipeline.py --help` for all options.

PowerShell scripts (`run_pipeline.ps1`) are kept for backward compatibility.

---

## Local BD enrichment overlay

OSDU's `BusinessDecision` schema only preserves **7 registered** `ext.equinor` keys. Custom keys (`ProductionProfile`, `Authors`, `DevelopmentConcept`, `ReservoirProperties`, `KeyEconomics`, `ScheduleMilestones`, etc.) are silently dropped during workflow ingestion.

**Workaround** (in `app/main.py`):

1. `_load_bd_enrichments()` scans BD manifests at startup and caches `ext.equinor` data by record ID.
2. `_apply_bd_local_enrichment()` merges cached fields into OSDU-fetched records at render time. OSDU data always wins for keys that exist in both.

---

## Demo datasets

| Dataset | Pipeline | Records | BD |
|---------|----------|---------|-----|
| **Drogon DG1** | `demo/drogon/` | ~19 | Identify & Assess (7 segments, 3 facies) |
| **Drogon DG2** | `demo/drogon_dg2/` | ~31 | Concept Select (porosity ×0.8 scenario, PersistedCollection) |
| **Volantis SeisInt** | `demo/seisint/` | ~22 | Seismic interpretation (StructureMap, BinGrid, Horizons via RDDMS) |

---

## Demo token tools

Token minting is centralised in `demo/_auth.py` - the single source of truth for
k8s YAML loading, instance resolution (k8s → env → `.env`), and OAuth2 token exchange
(both `refresh_token` and `client_credentials` grants).

| File | Purpose |
|------|---------|
| `demo/_auth.py` | Shared module: `get_token()`, `load_instance()`, `api_headers()`, `base_url()` - imported by all demo scripts |
| `demo/gettoken.py` | Rich CLI: `--from-k8s`, `--list`, `--export`, `--json` - thin wrapper around `_auth` |
| `app/get_token.py` | Minimal CLI: `--shell bash\|pwsh`, `--instance` - also delegates to `_auth` |

```bash
# Mint a token
python demo/gettoken.py eqndev --from-k8s

# List all discoverable instances
python demo/gettoken.py --list

# In a demo script
from _auth import get_token, api_headers
token = get_token("eqndev")
headers = api_headers("eqndev")
```

---

## Performance & caching

| Feature | Module | Detail |
|---------|--------|--------|
| TTL cache | `app/cache.py` | `ttl_cache` decorator / `cached_call()` with per-key `asyncio.Lock` (thundering-herd protection) |
| Dataspace list | `app/osdu.py` | Cached 120 s via `cached_call()` |
| Reservoir search | `app/common.py` | Cached 90 s via `cached_call()` |
| API concurrency | `app/osdu.py` | `API_SEMAPHORE` (default 20, override via `OSDU_MAX_CONCURRENT`) |
| Parallel fetches | `app/addgate.py`, `app/analyse.py`, `app/keys_router.py` | `asyncio.gather` for independent OSDU calls |

---

## Tests

```bash
python -m pytest tests/ -v          # run all tests
python -m pytest tests/ -v -x       # stop on first failure
```

The test suite uses **pytest** + **pytest-asyncio** and currently contains **135+** tests covering:

| File | Tests | Area |
|------|-------|------|
| `tests/test_auth.py` | 48 | Auth modes, PKCE flow, token refresh, diagnostics |
| `tests/test_routes.py` | 21 | Route rendering, strat column, admin page |
| `tests/test_gettoken.py` | 32 | k8s YAML loading, instance discovery, token minting |
| `tests/test_demo_auth.py` | 24 | Central auth module (`.env`, caching, backward compat) |
| `tests/test_tokenstore.py` | - | SQLite token store CRUD, encryption, multi-user |
| `tests/test_instances.py` | 2 | OsduInstance dataclass |
| `tests/test_cache.py` | 8 | TTL cache, thundering-herd lock, invalidation |

---

## Links

| Resource | Path |
|----------|------|
| App entry | `app/main.py` |
| Auth | `app/auth.py` |
| Token store | `app/tokenstore.py` |
| Cache layer | `app/cache.py` |
| Shared helpers | `app/common.py` |
| OSDU client | `app/osdu.py` |
| Keys/browse page | `app/keys_router.py` |
| Ingest API | `app/ingest_router.py` |
| Analyse page | `app/analyse.py` |
| Add DG page | `app/addgate.py` |
| CLI token tool (app) | `app/get_token.py` |
| Demo auth module | `demo/_auth.py` |
| CLI token tool (demo) | `demo/gettoken.py` |
| Drogon pipeline | `demo/drogon/` |
| Pipeline runner | `demo/run_pipeline.py` |
| BD modelling guide | `md/BusinessDecision.md` |
| BD demo (DG2) | `md/BdDemo.md` |
| Volume schemas | `md/Volumes.md` |
| GeoLabelSet | `md/GeoLabelSet.md` |
| Risk guide | `md/Risk.md` |
| Uncertainty | `md/Uncertainty.md` |
| FMU ↔ OSDU | `md/FmuOsdu.md` |
| Seismic interpretation | `md/SeisInt.md` |
| CRS guide | `md/CrsGuide.md` |
| Strat column | `md/StratColumn.md` |
