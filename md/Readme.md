# OSDU RDDMS admin UI — web client and demo toolkit

A FastAPI-based administrative UI for an OSDU-style RDDMS (Reservoir Data / Decision Management System) plus a demo pipeline toolkit for generating, ingesting and comparing Business Decisions, Volumes, Risks and Stratigraphy records.

---

## What the client can do

| Page | Purpose |
|------|---------|
| **RddmsAdmin** (`/`) | List and manage OSDU Dataspaces — create, lock, unlock, delete, build manifests |
| **RddmsResources** (`/keys`) | Browse dataspaces, record types, individual objects; inspect table & graph data |
| **OsduSearch** (`/search`) | Query the OSDU Search API and view results with kind-specific cards (BusinessDecision, REV, Risk, GeoLabelSet, etc.) |
| **Analyse** (`/analyse`) | Select a Reservoir, compare Business Decisions across decision gates (DG1→DG4) with volume/risk/economics deltas and charts |
| **Add DG** (`/add-dg`) | Create and ingest a new BusinessDecision for an existing Reservoir, linking REV, GeoLabelSet, Activity and Risk records |
| **Stratigraphy** (`/strat`) | Preview and ingest stratigraphic column records |
| **HowTo** (`/howto`) | Browse grouped markdown documentation articles (BD modelling, SeisInt, CRS, Stratigraphy) |

Key rendering features:

- **BD cards** — gradient header, headline volume KPIs (three-tier fallback: stat WPC → GeoLabelSet → ext.equinor), development concept, reservoir properties, economics, schedule, production forecast chart, alternatives, risk chips, uncertainties, authors & governance.
- **REV cards** — teal-themed with P10/P50/P90 headlines, metadata highlights, full volume table.
- **Analyse comparison** — gate timeline, side-by-side metric deltas (STOIIP, NPV, CAPEX, etc.), risk diff chips (added/removed/mitigated), property diffs, synthesis insights, Chart.js overlay charts.
- **Mermaid relationship graphs** — interactive record-relationship diagrams with ancestry, data references, type-based styling.
- **Local BD enrichment overlay** — OSDU silently drops custom `ext.equinor` keys during ingestion; the app loads manifests at startup and merges them back at render time.

## Requirements

### Runtime

| Requirement | Version |
|-------------|---------|
| Python | 3.11+ |
| FastAPI | 0.115+ |
| uvicorn | 0.32+ |
| httpx | (latest) |


Install all dependencies:

```bash
pip install -r requirements.txt
```

### Configuration (k8s YAML)

Config lives in two files under `k8s/`:

| File | In git? | Content |
|------|---------|---------|
| `k8s/configmap.yaml` | Yes | Hostnames, partitions, legal tags, app settings |
| `k8s/secret.yaml` | **No** (gitignored) | Tenant IDs, client IDs, tokens, API keys |
| `k8s/secret.yaml.template` | Yes | Empty template — copy to `secret.yaml` and fill in |

Each OSDU instance is defined by `INSTANCE_<NAME>_*` env vars split across both files.

### Frontend (CDN — no npm install)

- **Chart.js 4** — production forecast & comparison charts
- **Mermaid 10** — relationship graph rendering

### Quick start

```bash
# 1. Copy the secret template and fill in your credentials
cp k8s/secret.yaml.template k8s/secret.yaml

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the server
eval "$(python k8s/env_from_k8s.py)" && python -m uvicorn app.main:app --reload --port 8000 --host 127.0.0.1
```

Open <http://127.0.0.1:8000/> in a browser.

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
  _auth.py             # Central auth module — k8s/env/.env resolution & token minting
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

## Pipeline guide — adding a new field / decision gate dataset

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

Token minting is centralised in `demo/_auth.py` — the single source of truth for
k8s YAML loading, instance resolution (k8s → env → `.env`), and OAuth2 token exchange
(both `refresh_token` and `client_credentials` grants).

| File | Purpose |
|------|--------|
| `demo/_auth.py` | Shared module: `get_token()`, `load_instance()`, `api_headers()`, `base_url()` — imported by all demo scripts |
| `demo/gettoken.py` | Rich CLI: `--from-k8s`, `--list`, `--export`, `--json` — thin wrapper around `_auth` |
| `app/get_token.py` | Minimal CLI: `--shell bash\|pwsh`, `--instance` — also delegates to `_auth` |

---

## Performance & caching

| Feature | Module | Detail |
|---------|--------|--------|
| TTL cache | `app/cache.py` | `ttl_cache` decorator / `cached_call()` with per-key `asyncio.Lock` |
| Dataspace list | `app/osdu.py` | Cached 120 s |
| Reservoir search | `app/common.py` | Cached 90 s |
| API concurrency | `app/osdu.py` | `API_SEMAPHORE` (default 20, override via `OSDU_MAX_CONCURRENT`) |
| Parallel fetches | `app/addgate.py`, `app/analyse.py`, `app/keys_router.py` | `asyncio.gather` for independent OSDU calls |

---

## Tests

```bash
python -m pytest tests/ -v
```

135+ tests covering auth, routes, k8s loading, token minting, caching, and tokenstore.

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
