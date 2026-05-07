# ORES - OSDU RDDMS Explorer & Demo Toolkit

Administrative web UI and pipeline toolkit for OSDU Reservoir Data / Decision Management.
Browse dataspaces, compare Business Decisions across decision gates, manage seismic interpretations, stratigraphy columns, and ingest records.

## Quick start

```bash
git clone https://github.com/equinor/ores.git && cd ores
pip install -r requirements.txt

# Configure credentials
cp k8s/secret.yaml.template k8s/secret.yaml
# Fill in tenant ID, client ID, and either a refresh_token or client_secret

# Run
eval "$(python k8s/env_from_k8s.py)" && python -m uvicorn app.main:app --reload --port 8000
```

Open <http://127.0.0.1:8000/>

## What you need

| Requirement | Notes |
|-------------|-------|
| Python 3.11+ | 3.12 recommended |
| An OSDU instance | With Storage/Search API access |
| Azure AD credentials | Tenant ID + Client ID (+ refresh token or client secret) |

## Configuration

All config lives under `k8s/`:

| File | Purpose |
|------|---------|
| `configmap.yaml` | Hostnames, partitions, legal tags (checked in) |
| `secret.yaml` | Credentials - tenant IDs, tokens, keys (gitignored) |
| `secret.yaml.template` | Copy this to `secret.yaml` and fill in values |

Each OSDU instance is defined by `INSTANCE_<NAME>_*` env vars across both files.

### Auth modes

| Mode | Config needed |
|------|---------------|
| Shared token (zero-click) | Set `INSTANCE_<NAME>_REFRESH_TOKEN` or `_CLIENT_SECRET` in secret.yaml |
| Per-user PKCE | No shared token - users log in via Azure AD redirect (sessions persisted in SQLite) |

## Pages

| Route | Purpose |
|-------|---------|
| `/` | Manage OSDU Dataspaces |
| `/keys` | Browse record types and objects + GraphQL deep search |
| `/search` | Query OSDU Search API |
| `/analyse` | Compare BDs across decision gates |
| `/add-dg` | Create new BusinessDecision records |
| `/strat` | Stratigraphic column viewer |
| `/howto` | Documentation articles |
| `/graphql` | GraphiQL IDE for RESQML queries |
| `/api/graphql/query` | GraphQL endpoint (POST) |
| `/api/graphql/info` | GraphQL backend status (GET) |

## Demo pipelines

```bash
python demo/run_pipeline.py                    # default pipeline (drogon_dg2)
python demo/run_pipeline.py demo/drogon        # Drogon DG1
python demo/run_pipeline.py --list             # list available pipelines
python demo/run_pipeline.py --help             # all options
```

## Tests

```bash
python -m pytest test/ -v     # 147 tests
```

## GraphQL Deep Search

The `/keys` page includes a GraphQL panel for deep RESQML queries - object browsing,
relationship graph traversal, and array-level numerical filtering.

To enable direct PostgreSQL access (fastest, bypasses REST):

```bash
export GRAPHQL_PG_CONN_STRING="host=localhost port=5433 dbname=openetp user=tester password=tester"
```

Without this variable, queries fall back to the RDDMS REST API (always works with a valid token).

For local testing with Docker:

```bash
cd demo/epc && docker compose up -d   # PostgreSQL + OpenETPServer
./demo/epc/ingest.sh                   # Import Volve surfaces EPC
python demo/epc/test_graphql.py        # Verify all queries
```

See [md/Query.md](md/Query.md) for the full query guide.

## Docker

```bash
docker build -t ores .
docker run -p 8000:8000 --env-file <(python k8s/env_from_k8s.py | sed 's/^export //') ores
```

To enable GraphQL PostgreSQL access in Docker, add `-e GRAPHQL_PG_CONN_STRING="..."`.

## Documentation

Detailed guides live in [`md/`](md/Readme.md):

| Guide | Topic |
|-------|-------|
| [Readme](md/Readme.md) | Full architecture, auth, k8s deployment, project layout, caching |
| [BdDemo](md/BdDemo.md) | Business Decision data model & pipeline walkthrough |
| [BusinessDecision](md/BusinessDecision.md) | BD schema patterns (Parameters vs Collections) |
| [DevConcept](md/DevConcept.md) | DevelopmentConcept custom WPC schema |
| [SeisInt](md/SeisInt.md) | Seismic interpretation OSDU/RESQML model |
| [StratColumn](md/StratColumn.md) | Stratigraphic column data model |
| [CrsGuide](md/CrsGuide.md) | CRS mapping RESQML to OSDU |
| [FmuOsdu](md/FmuOsdu.md) | FMU-to-OSDU workflow |
| [Risk](md/Risk.md) | Risk register modelling |
| [Uncertainty](md/Uncertainty.md) | Uncertainty & volumes workflow |
| [Volumes](md/Volumes.md) | ReservoirEstimatedVolumes schema |
| [GeoLabelSet](md/GeoLabelSet.md) | GeoLabelSet headline KPIs |
| [Query](md/Query.md) | Querying data: REST, ETP, GraphQL & OSDU Search |
