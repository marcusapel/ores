
from __future__ import annotations
import asyncio
import os
import re
import secrets
import urllib.parse
import logging
import json
from typing import List, Dict, Any, Optional, Set

import httpx
from httpx import HTTPStatusError
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from starlette.middleware.sessions import SessionMiddleware

# App modules
from .schemahandler import extract_osdu_links
from .schemahandler import extract_metadata_generic
from app.ingest_router import router as ingest_router
from . import osdu
from .auth import (
    router as auth_router,
    tokens_from_env,
    tokens_from_session,
    AUTH_MODE,
    PUBLIC_PATHS,
)
from .instances import get_instances, get_active, set_active, get_active_name
import markdown as _md
from pathlib import Path as _Path
from .strat import router as strat_router
from .analyse import router as analyse_router
from .addgate import router as addgate_router
from .keys_router import router as keys_router
from .graphql_router import router as graphql_router, close_pool as _close_gql_pool
from .search_router import router as search_router
from .search_router import _friendly_value

# ──────────────────────────────────────────────────────────────────────────────
# App setup & logging
# ──────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
log = logging.getLogger("rddms-admin")

app = FastAPI(title="RDDMS Admin")

# ── Stable secret key (must be identical across workers) ─────────────────────
_SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_hex(16)
_HTTPS_ONLY = os.getenv("HTTPS_ONLY", "false").lower() in ("1", "true", "yes")

# Security headers & cache hardening
@app.middleware("http")
async def no_transform_headers(request: Request, call_next):
    resp: Response = await call_next(request)
    resp.headers.setdefault("Cache-Control", "no-store, no-transform")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    return resp

# Auth middleware: env-token primary → per-user session fallback → redirect to /login
@app.middleware("http")
async def inject_access_token(request: Request, call_next):
    """
    Resolve an access_token and attach it to request.state.
    Priority:
      0) Active instance token (client_credentials / instance refresh)
      1) REFRESH_TOKEN from env (default instance)
      2) Per-user session token (PKCE)
      3) Redirect to /login
    """
    path = request.url.path

    # Let public paths through without a token
    if path in PUBLIC_PATHS or path.startswith("/static"):
        return await call_next(request)

    access_token: str | None = None

    # 0. Try active instance's own token (client_credentials or refresh)
    try:
        inst = get_active()
        inst_token = await inst.get_access_token()
        if inst_token:
            access_token = inst_token
    except Exception as e:
        log.warning("Instance token mint failed: %s", e)

    # 1. Try shared env-token (default instance, refresh_token from env)
    if not access_token:
        try:
            env_tokens = await tokens_from_env()
            if env_tokens:
                access_token = env_tokens.get("access_token")
        except Exception as e:
            log.warning("Env-token mint failed: %s", e)

    # 2. Fallback - per-user session token (PKCE flow)
    if not access_token:
        try:
            sess_tokens = await tokens_from_session(request)
            if sess_tokens:
                access_token = sess_tokens.get("access_token")
        except Exception as e:
            log.warning("Session token failed: %s", e)

    # 3. No token at all - redirect to login page (for browser) or 401 (for API)
    if not access_token:
        if path.startswith("/api"):
            return JSONResponse({"error": "Authentication required. No env token and no session."}, status_code=401)
        return RedirectResponse("/login-page")

    request.state.access_token = access_token
    return await call_next(request)

# Attach routers & static
# Session middleware - added LAST so it is outermost and runs FIRST,
# making request.session available to all inner middleware.
app.add_middleware(
    SessionMiddleware,
    secret_key=_SECRET_KEY,
    session_cookie="ores_session",
    max_age=30 * 24 * 3600,    # 30-day session cookie (RT kept alive via tokenstore)
    same_site="lax",
    https_only=_HTTPS_ONLY,    # set HTTPS_ONLY=true in prod behind TLS
)

app.include_router(auth_router)  # keeps /auth diagnostics
app.include_router(ingest_router, prefix="/api")
app.include_router(strat_router)
app.include_router(analyse_router)
app.include_router(addgate_router)
app.include_router(keys_router)
app.include_router(graphql_router)
app.include_router(search_router)


# Close shared httpx client on shutdown (#9)
@app.on_event("shutdown")
async def _shutdown():
    await osdu.close_shared_client()
    await _close_gql_pool()


app.mount(
    "/static",
    StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")),
    name="static",
)
templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "templates")
)
# Make auth_mode available in every template (for nav Sign-out link)
templates.env.globals["auth_mode"] = AUTH_MODE


# ─── Favicon (avoid 404 noise in logs) ───────────────────────────────────── #

_FAVICON_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    b'<circle cx="16" cy="16" r="14" fill="#FF1243"/>'
    b'<text x="16" y="22" font-size="16" text-anchor="middle" fill="#fff" '
    b'font-family="sans-serif" font-weight="bold">O</text></svg>'
)


@app.get("/favicon.ico", include_in_schema=False)
async def _favicon():
    return Response(content=_FAVICON_SVG, media_type="image/svg+xml")


def _jinja_pretty_val(val):
    """Jinja filter: prettify metadata values that may contain JSON."""
    if val is None:
        return "-"
    s = str(val)
    # Try to parse residual JSON strings and re-format them
    if s.startswith(("[", "{")):
        try:
            obj = json.loads(s)
            return _friendly_value(obj, 600)
        except (json.JSONDecodeError, ValueError):
            pass
    return s


templates.env.filters["pretty_val"] = _jinja_pretty_val

# ── Startup banner with PID (visible in terminal for easy kill) ──────────────
import sys as _sys
_pid = os.getpid()
_banner = (
    f"\n"
    f"  ╔══════════════════════════════════════════════╗\n"
    f"  ║  ORES  -  OSDU Record Explorer & Stratigraphy ║\n"
    f"  ║  PID: {_pid:<39d} ║\n"
    f"  ║  Kill: kill {_pid:<34d} ║\n"
    f"  ╚══════════════════════════════════════════════╝\n"
)
print(_banner, file=_sys.stderr, flush=True)
log.info("ORES starting - PID %d", _pid)

# Log routes at startup (helps when a route goes missing)
log.info("Routes registered: %d routes", len(app.routes))
log.debug("Route paths: %s", [getattr(r, "path", str(r)) for r in app.routes])

# ──────────────────────────────────────────────────────────────────────────────
# OSDU instance switching
# ──────────────────────────────────────────────────────────────────────────────

# Initialise instance registry at import time (reads INSTANCE_* env vars)
_all_instances = get_instances()
log.info("OSDU instances loaded: %s (active=%s)", list(_all_instances.keys()), get_active_name())

# Re-sync template globals after instance init (auth_mode may have changed)
from .auth import AUTH_MODE as _AUTH_MODE_AFTER_INIT
templates.env.globals["auth_mode"] = _AUTH_MODE_AFTER_INIT

# Add /api/instances/* to PUBLIC_PATHS so they work before auth
# (switching / adding happens before a valid token exists for the new instance)
PUBLIC_PATHS.add("/api/instances")
PUBLIC_PATHS.add("/api/instances/switch")
PUBLIC_PATHS.add("/api/instances/add")


@app.get("/api/instances")
async def api_instances():
    """Return all registered OSDU instances and which is active."""
    insts = get_instances()
    return {
        "active": get_active_name(),
        "instances": {
            name: {
                "name": inst.name,
                "hostname": inst.hostname,
                "data_partition_id": inst.data_partition_id,
                "auth_mode": inst.auth_mode,
            }
            for name, inst in insts.items()
        },
    }


@app.post("/api/instances/switch")
async def api_switch_instance(name: str = Form(...)):
    """Switch the active OSDU instance."""
    try:
        inst = set_active(name)
        # Re-sync Jinja globals after auth.py update
        from .auth import AUTH_MODE as _am
        templates.env.globals["auth_mode"] = _am
        # Try to mint a token immediately to validate connectivity
        token = await inst.get_access_token()
        return {
            "ok": True,
            "active": name,
            "hostname": inst.hostname,
            "partition": inst.data_partition_id,
            "auth_mode": inst.auth_mode,
            "token_ok": token is not None,
        }
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except Exception as e:
        log.exception("Instance switch failed")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/instances/add")
async def api_add_instance(request: Request):
    """Add a new OSDU instance to k8s YAML files, register it, and activate it."""
    from .instances import _instances, _load_instances, OsduInstance

    form = await request.form()
    name = (form.get("name") or "").strip().lower()
    if not name or not name.isalnum():
        return JSONResponse({"ok": False, "error": "Name must be non-empty alphanumeric"}, status_code=400)
    if name in _instances:
        return JSONResponse({"ok": False, "error": f"Instance '{name}' already exists"}, status_code=409)

    PREFIX = f"INSTANCE_{name.upper()}_"

    # ── Sanitize form values for safe YAML inclusion (#14) ──
    def _safe(v: str) -> str:
        return re.sub(r'["\n\r\\]', '', (v or '')).strip()

    # ── Collect fields from form ──
    config_fields = {
        "HOSTNAME": _safe(form.get("hostname", "")),
        "DATA_PARTITION_ID": _safe(form.get("data_partition_id", "opendes")),
        "AUTHORITY": _safe(form.get("authority", "osdu")),
        "SCHEMA_SOURCE": _safe(form.get("schema_source", "wks")),
        "DEFAULT_LEGAL_TAG": _safe(form.get("default_legal_tag", "")),
        "DEFAULT_OWNERS": _safe(form.get("default_owners", "")),
        "DEFAULT_VIEWERS": _safe(form.get("default_viewers", "")),
        "DEFAULT_COUNTRIES": _safe(form.get("default_countries", "NO")),
    }
    secret_fields = {
        "TENANT_ID": _safe(form.get("tenant_id", "")),
        "CLIENT_ID": _safe(form.get("client_id", "")),
        "CLIENT_SECRET": _safe(form.get("client_secret", "")),
        "SCOPE": _safe(form.get("scope", "")),
        "REFRESH_TOKEN": _safe(form.get("refresh_token", "")),
    }

    if not config_fields["HOSTNAME"]:
        return JSONResponse({"ok": False, "error": "Hostname is required"}, status_code=400)

    k8s_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "k8s")
    cm_path = os.path.join(k8s_dir, "configmap.yaml")
    sec_path = os.path.join(k8s_dir, "secret.yaml")

    try:
        # ── Try to persist to k8s YAML files (will fail on read-only FS like Radix) ──
        files_written = False
        try:
            cm_lines = [f"\n  # ── \"{name}\" - added via ORES UI ──"]
            for field, val in config_fields.items():
                cm_lines.append(f'  {PREFIX}{field}: "{val}"')
            with open(cm_path, "a") as f:
                f.write("\n".join(cm_lines) + "\n")

            sec_lines = [f"\n  # ── \"{name}\" - added via ORES UI ──"]
            for field, val in secret_fields.items():
                if val:
                    sec_lines.append(f'  {PREFIX}{field}: "{val}"')
                else:
                    sec_lines.append(f'  # {PREFIX}{field}: ""')
            with open(sec_path, "a") as f:
                f.write("\n".join(sec_lines) + "\n")
            files_written = True
        except OSError as io_err:
            log.info("Could not write k8s YAMLs (%s) - registering in-memory only", io_err)

        # ── Set env vars so _load_instances picks them up ──
        for field, val in config_fields.items():
            os.environ[f"{PREFIX}{field}"] = val
        for field, val in secret_fields.items():
            if val:
                os.environ[f"{PREFIX}{field}"] = val

        # ── Register and activate ──
        _instances.clear()
        _load_instances()
        inst = set_active(name)

        # Re-sync Jinja globals
        from .auth import AUTH_MODE as _am
        templates.env.globals["auth_mode"] = _am

        token = await inst.get_access_token()
        log.info("Added and activated instance '%s' → %s (persisted=%s)", name, inst.hostname, files_written)

        return {
            "ok": True,
            "active": name,
            "hostname": inst.hostname,
            "partition": inst.data_partition_id,
            "auth_mode": inst.auth_mode,
            "token_ok": token is not None,
            "persisted": files_written,
        }

    except Exception as e:
        log.exception("Failed to add instance '%s'", name)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ──────────────────────────────────────────────────────────────────────────────
# Login landing page (per-user PKCE mode)
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/login-page", response_class=HTMLResponse)
async def login_page(request: Request):
    """Serve the sign-in landing page (only reached when no env token is set)."""
    insts = get_instances()
    return templates.TemplateResponse(request, "login.html", {
        "instances": {
            n: {"hostname": i.hostname, "partition": i.data_partition_id, "auth_mode": i.auth_mode}
            for n, i in insts.items()
        },
        "active_instance": get_active_name(),
    })

# ──────────────────────────────────────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────────────────────────────────────

def _access_token(request: Request) -> str:
    from .common import access_token as _at
    return _at(request)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers - volume / BD enrichment
# ──────────────────────────────────────────────────────────────────────────────


def _normalize_volumes(data_block: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize OSDU ColumnBasedTable / ReservoirEstimatedVolumes data to:
    {
      "KeyColumns": [ {ColumnName, ColumnRole, ValueType, ...}, ... ],
      "Columns":    [ {ColumnName, ColumnRole, ValueType, ...}, ... ],
      "ColumnValues": { "<ColumnName>": [v0, v1, ...], ... }
    }
    Handles two layouts:
      - REV/GLS records: table nested under data['Volumes']
      - ColumnBasedTable records: table at the top level of data{}
    Handles cases where ColumnValues may arrive as a dict or a list of objects.
    Leaves other shapes untouched (best-effort).
    """
    # Look for the table in data['Volumes'] (REV), data['Table'] (CBT), or top-level
    vol = (data_block or {}).get("Volumes", {}) or {}
    if not vol.get("ColumnValues"):
        vol = (data_block or {}).get("Table", {}) or {}
    if not vol.get("ColumnValues") and (data_block or {}).get("ColumnValues"):
        vol = data_block
    key_cols = vol.get("KeyColumns", []) or []
    value_cols = vol.get("Columns", []) or []
    raw_vals = vol.get("ColumnValues", {}) or {}

    if isinstance(raw_vals, dict):
        col_values = raw_vals
    elif isinstance(raw_vals, list):
        # list of dicts like {"ColumnName": "...", "Values": [...]}
        if raw_vals and all(isinstance(x, dict) for x in raw_vals):
            out: Dict[str, List[Any]] = {}
            for x in raw_vals:
                name = x.get("ColumnName") or x.get("name")
                vals = (
                    x.get("Values")
                    or x.get("values")
                    or x.get("Data")
                    or x.get("data")
                    or []
                )
                if name:
                    out[name] = vals if isinstance(vals, list) else [vals]
            col_values = out
        else:
            col_values = raw_vals
    else:
        col_values = raw_vals

    return {
        "KeyColumns": key_cols,
        "Columns": value_cols,
        "ColumnValues": col_values,
    }


async def _enrich_bd_volumes(
    data_block: Dict[str, Any],
    client: httpx.AsyncClient,
    storage_url: str,
    hdr: dict,
) -> Dict[str, Any]:
    """For BusinessDecision records, fetch volumes from the stat REV WPC
    referenced in ``Parameters``.

    Returns a normalized volumes dict (may be empty if nothing found).
    The strategy:
      1. Walk ``data.Parameters`` for entries whose ``DataObjectParameter``
         points to a ReservoirEstimatedVolumes WPC.
      2. Prefer the one tagged ``REV-stats``; fall back to any REV WPC.
      3. Fetch that record and return its ``_normalize_volumes()`` output.
    """
    params = data_block.get("Parameters") or []
    if not isinstance(params, list):
        return {}

    stat_id: str = ""
    any_rev_id: str = ""
    for p in params:
        if not isinstance(p, dict):
            continue
        dop = p.get("DataObjectParameter") or ""
        if "ReservoirEstimatedVolumes" not in dop:
            continue
        # Check StringParameterKey for "stats"
        keys = p.get("Keys") or []
        is_stat = any(
            "stat" in (kv.get("StringParameterKey") or "").lower()
            for kv in keys if isinstance(kv, dict)
        )
        if is_stat:
            stat_id = dop
            break
        if not any_rev_id:
            any_rev_id = dop

    target_id = stat_id or any_rev_id
    if not target_id:
        return {}

    try:
        r = await client.get(f"{storage_url}/{target_id}", headers=hdr)
        if r.status_code != 200:
            return {}
        d = (r.json() or {}).get("data", {}) or {}
        return _normalize_volumes(d)
    except Exception as e:
        log.warning("[BD-VOLUMES] Failed to fetch stat REV %s: %s", target_id, e)
        return {}


# ──────────────────────────────────────────────────────────────────────────────
# GeoLabelSet & ColumnBasedTable dynamic enrichment
# ──────────────────────────────────────────────────────────────────────────────

def _normalize_geolabel(data_block: Dict[str, Any]) -> Dict[str, Any]:
    """Extract a flat, template-friendly dict from a GeoLabelSet record.

    Returns::

        {
          "volumes_by_segment": {
              "<SegmentID>": {"Oil.P90": v, "Oil.P50": v, "Oil.P10": v, ...},
              ...
          },
          "properties": {
              "Porosity": {"Channel": 0.22, "Crevasse": 0.17, ...},
              "NetToGross": 0.85,
              "Permeability": 450,
              ...
          },
          "uncertainty": {
              "Recoverable.P90": v, "Recoverable.P50": v, ...
              "RecoveryFactor.P90": v, ...
          },
          "raw_geolabels": <original GeoLabels block>,
        }
    """
    gl = (data_block or {}).get("GeoLabels") or {}
    cv = gl.get("ColumnValues") or {}
    if not cv:
        return {}

    segments = cv.get("SegmentID") or []
    facies = cv.get("Facies") or []
    n_rows = len(segments)

    # Identify value column names (exclude key columns)
    key_names = {c.get("ColumnName") for c in (gl.get("KeyColumns") or [])}
    val_col_names = [k for k in cv if k not in key_names]

    # Volumetric columns (Oil.P*, Recoverable.*, RecoveryFactor.*)
    vol_prefixes = ("Oil.", "Gas.", "AssociatedGas.", "Bulk.", "Net.",
                    "Pore.", "HydrocarbonPore.")
    unc_prefixes = ("Recoverable.", "RecoveryFactor.")
    # Property columns (everything else)

    volumes_by_seg: Dict[str, Dict[str, Any]] = {}
    properties: Dict[str, Any] = {}
    uncertainty: Dict[str, Any] = {}

    for i in range(n_rows):
        seg = segments[i] if i < len(segments) else "TOTAL"
        # Normalise common "totals" variants → canonical "TOTAL" key
        if seg.lower() in ("totals", "total", "grand total"):
            seg = "TOTAL"
        fac = facies[i] if i < len(facies) else "ALL"

        for col in val_col_names:
            vals = cv.get(col) or []
            v = vals[i] if i < len(vals) else None
            if v is None:
                continue

            if col.startswith(unc_prefixes):
                # Uncertainty summary (field-level, TOTAL/ALL)
                uncertainty[col] = v
            elif col.startswith(vol_prefixes):
                # Per-segment volumes
                seg_dict = volumes_by_seg.setdefault(seg, {})
                seg_dict[col] = v
            else:
                # Property column
                if fac != "ALL":
                    # Facies-specific property (e.g. Porosity per facies)
                    prop_dict = properties.setdefault(col, {})
                    if isinstance(prop_dict, dict):
                        prop_dict[fac] = v
                else:
                    # Field-level scalar
                    properties[col] = v

    return {
        "volumes_by_segment": volumes_by_seg,
        "properties": properties,
        "uncertainty": uncertainty,
        "raw_geolabels": gl,
    }


async def _enrich_bd_geolabel(
    data_block: Dict[str, Any],
    client: httpx.AsyncClient,
    storage_url: str,
    hdr: dict,
) -> Dict[str, Any]:
    """Fetch the GeoLabelSet referenced in BD Parameters[] and normalise it.

    Looks for a Parameters entry with StringParameterKey 'GeoLabelSet'.
    """
    params = data_block.get("Parameters") or []
    if not isinstance(params, list):
        return {}

    target_id = ""
    for p in params:
        if not isinstance(p, dict):
            continue
        dop = p.get("DataObjectParameter") or ""
        if "GeoLabelSet" not in dop:
            continue
        keys = p.get("Keys") or []
        if any("GeoLabelSet" in (kv.get("StringParameterKey") or "")
               for kv in keys if isinstance(kv, dict)):
            target_id = dop
            break
        if not target_id:
            target_id = dop

    if not target_id:
        return {}

    d: Optional[Dict[str, Any]] = None
    try:
        r = await client.get(f"{storage_url}/{target_id}", headers=hdr)
        if r.status_code == 200:
            d = (r.json() or {}).get("data", {}) or {}
        else:
            log.warning("[BD-GLS] GeoLabelSet %s returned %d", target_id, r.status_code)
    except Exception as e:
        log.warning("[BD-GLS] OSDU fetch failed for %s: %s", target_id, e)

    if not d:
        return {}

    try:
        result = _normalize_geolabel(d)
        if result:
            log.info("[BD-GLS] Loaded GeoLabelSet %s (%d segments, %d props)",
                     target_id,
                     len(result.get("volumes_by_segment", {})),
                     len(result.get("properties", {})))
        return result
    except Exception as e:
        log.warning("[BD-GLS] Failed to normalise GeoLabelSet %s: %s", target_id, e)
        return {}


# ──────────────────────────────────────────────────────────────────────────────
# BD enrichment: Activity record → bd_activity dict
# ──────────────────────────────────────────────────────────────────────────────

async def _enrich_bd_activity(
    data_block: Dict[str, Any],
    client: httpx.AsyncClient,
    storage_url: str,
    hdr: dict,
) -> Dict[str, Any]:
    """Fetch Activity record linked from BD PriorActivityIDs[].

    Returns a dict with the Activity's data block if found, keyed for
    easy template rendering: Name, Description, Parameters[], etc.
    """
    # Try PriorActivityIDs first
    prior_ids = data_block.get("PriorActivityIDs") or []
    if isinstance(prior_ids, str):
        prior_ids = [prior_ids]

    target_id = ""
    for pid in prior_ids:
        if isinstance(pid, str) and "Activity:" in pid and "ActivityTemplate" not in pid:
            target_id = pid
            break

    # Also check Parameters[] for ActivityTemplate or Activity refs
    if not target_id:
        params = data_block.get("Parameters") or []
        for p in params:
            if not isinstance(p, dict):
                continue
            dop = p.get("DataObjectParameter") or ""
            if "Activity:" in dop and "ActivityTemplate" not in dop:
                target_id = dop
                break

    if not target_id:
        return {}

    try:
        r = await client.get(f"{storage_url}/{target_id}", headers=hdr)
        if r.status_code == 200:
            full = r.json()
            d = full.get("data") or {}
            log.info("[BD-ACT] Loaded Activity %s: %s", target_id, d.get("Name", ""))

            # Resolve names for DataObjectParameter refs in Parameters
            param_labels: Dict[str, str] = {}
            params_list = d.get("Parameters") or []
            dop_ids = []
            for p in params_list:
                if isinstance(p, dict):
                    dop = p.get("DataObjectParameter") or ""
                    if dop and dop not in param_labels:
                        dop_ids.append(dop)
            # Parallel-fetch names (up to 15)
            async def _fetch_label(did: str) -> tuple:
                try:
                    lr = await client.get(f"{storage_url}/{did}", headers=hdr)
                    if lr.status_code == 200:
                        nm = (lr.json().get("data") or {}).get("Name") or ""
                        if nm:
                            return (did, nm)
                except Exception:
                    pass
                return (did, "")
            if dop_ids:
                results = await asyncio.gather(*[_fetch_label(d) for d in dop_ids[:15]])
                for did, nm in results:
                    if nm:
                        param_labels[did] = nm

            return {
                "id": full.get("id", target_id),
                "kind": full.get("kind", ""),
                "Name": d.get("Name", ""),
                "Description": d.get("Description", ""),
                "WorkflowStatus": d.get("WorkflowStatus", ""),
                "CreationDateTime": d.get("CreationDateTime", ""),
                "Originator": d.get("Originator", ""),
                "ActivityTemplateID": d.get("ActivityTemplateID", ""),
                "Parameters": params_list,
                "param_labels": param_labels,
            }
        else:
            log.warning("[BD-ACT] Activity %s returned %d", target_id, r.status_code)
    except Exception as e:
        log.warning("[BD-ACT] OSDU fetch failed for %s: %s", target_id, e)

    return {}


# ──────────────────────────────────────────────────────────────────────────────
# BD enrichment: discover Grid2d depth maps from linked RDDMS dataspaces
# ──────────────────────────────────────────────────────────────────────────────

def _is_proper_grid2d_map(title: str) -> bool:
    """Return True if the Grid2d title looks like an actual depth/property map.

    RESQML 2.0.1 has no dedicated table object, so resqpy DataFrames
    (parameter tables, volume tables) are stored as Grid2dRepresentation.
    Those should NOT be plotted as maps.

    Heuristic: real FMU maps have short prefixed names (DS_, TS_, GS_, …);
    table-disguised Grid2d have long titles with keywords like 'Parameters',
    'Volumes', 'Estimated', 'statistics', 'per realisation', etc.
    """
    t = title.strip()
    tl = t.lower()

    # Known table markers - skip these
    _TABLE_MARKERS = (
        "parameter", "volume", "estimated", "statistic",
        "per realisation", "per realization", "raw,", "(raw",
        "dataframe", "table",
    )
    if any(m in tl for m in _TABLE_MARKERS):
        return False

    # Known map-like prefixes (FMU convention)
    _MAP_PREFIXES = ("ds_", "ts_", "gs_", "fs_")
    if any(tl.startswith(p) for p in _MAP_PREFIXES):
        return True

    # Titles containing depth/horizon/surface keywords are maps
    _MAP_KEYWORDS = (
        "depth", "horizon", "surface", "geogrid", "simgrid",
        "extract", "interp", "filter", "velocity", "facies",
        "hum_", "gf_", "residual", "isochore", "thickness",
    )
    if any(k in tl for k in _MAP_KEYWORDS):
        return True

    # Short single-word or underscore-delimited names are likely maps
    if "_" in t and len(t) < 60:
        return True

    # Default: include (be inclusive rather than hiding data)
    return True


async def _enrich_bd_maps(
    data_block: Dict[str, Any],
    client: httpx.AsyncClient,
    storage_url: str,
    hdr: dict,
) -> Dict[str, List[Dict[str, Any]]]:
    """Discover Grid2dRepresentation objects in the BD's linked RDDMS dataspaces.

    Walks the BD ``Parameters[]`` for ETPDataspace refs, fetches each to
    extract the EML URI, then lists Grid2d objects in each dataspace via
    the Reservoir DDMS API.

    Returns a dict with two keys::

        {
          "maps":  [...],   # proper depth/property maps (plotted as images)
          "all":   [...],   # all Grid2d objects (shown in activity refs)
        }

    Each entry: ``{"ds", "uuid", "title", "ds_name"}``
    """
    params = data_block.get("Parameters") or []
    # Collect ETPDataspace record IDs
    ds_ids: List[str] = []
    for p in params:
        if not isinstance(p, dict):
            continue
        dop = p.get("DataObjectParameter") or ""
        if "etpdataspace" in dop.lower():
            ds_ids.append(dop)

    if not ds_ids:
        log.debug("[BD-MAPS] No ETPDataspace refs in Parameters[]")
        return {"maps": [], "all": []}

    all_objs: List[Dict[str, Any]] = []
    at = hdr.get("Authorization", "").replace("Bearer ", "")
    log.info("[BD-MAPS] Found %d ETPDataspace refs: %s", len(ds_ids), ds_ids)

    for ds_id in ds_ids[:3]:  # limit to 3 dataspaces
        try:
            # 1. Fetch the ETPDataspace OSDU record to get the EML URI
            r_ds = await client.get(f"{storage_url}/{ds_id}", headers=hdr)
            if r_ds.status_code != 200:
                continue
            ds_rec = r_ds.json()
            ds_data = ds_rec.get("data") or {}
            ds_name = ds_data.get("Name") or ""
            raw_uri = (ds_data.get("DatasetProperties") or {}).get("URI") or ""

            # Extract dataspace path from EML URI:
            #   eml:///dataspace('maap/drogon_dg') → maap/drogon_dg   (quoted)
            #   eml:///dataspace(maap/drogon_dg)   → maap/drogon_dg   (unquoted)
            ds_path = ""
            m = re.search(r"dataspace\(['\"]?([^'\")\s]+)['\"]?\)", raw_uri)
            if m:
                ds_path = m.group(1)
            # Fallback: also try Name field itself (often equals the ds path)
            if not ds_path and "/" in ds_name:
                ds_path = ds_name
            if not ds_path:
                log.warning("[BD-MAPS] Cannot extract ds_path from URI=%r name=%r", raw_uri, ds_name)
                continue

            # 2. List Grid2d objects in this dataspace
            enc = urllib.parse.quote(ds_path, safe="")
            grid2d_type = "resqml20.obj_Grid2dRepresentation"
            try:
                objs = await osdu.list_resources(at, enc, grid2d_type)
            except Exception:
                objs = []

            for obj in (objs or []):
                uid = obj.get("Uuid") or obj.get("UUID") or obj.get("uuid") or ""
                uri = obj.get("uri") or ""
                if not uid and "(" in uri and ")" in uri:
                    uid = uri.split("(")[-1].rstrip(")")
                # RDDMS listing returns title in "name"; individual fetch uses "Citation.Title"
                title = (
                    obj.get("name")
                    or (obj.get("Citation") or {}).get("Title")
                    or uid
                )
                if uid:
                    all_objs.append({
                        "ds": ds_path,
                        "uuid": uid,
                        "title": title,
                        "ds_name": ds_name or ds_path,
                    })
        except Exception as e:
            log.warning("[BD-MAPS] Failed to discover maps in %s: %s", ds_id, e)

    # Split: proper maps vs everything (tables stay only in activity refs)
    proper_maps = [o for o in all_objs if _is_proper_grid2d_map(o["title"])]

    # Sort proper maps: preferred hero first, then alphabetical by title.
    # Preference order: DS_extract_simgrid > DS_extract_geogrid > DS_extract.
    def _map_sort_key(mp: Dict[str, Any]) -> tuple:
        t = mp["title"].lower()
        for i, pref in enumerate(("ds_extract_simgrid", "ds_extract_geogrid", "ds_extract")):
            if t.startswith(pref):
                return (i, t)
        return (99, t)

    proper_maps.sort(key=_map_sort_key)

    # ── Enrich each proper map with horizon name (RepresentedInterpretation) ──
    # Parallel individual Grid2d fetches - lightweight (no array data).
    grid2d_type = "resqml20.obj_Grid2dRepresentation"

    async def _fetch_interpretation(mp: Dict[str, Any]) -> None:
        try:
            enc = urllib.parse.quote(mp["ds"], safe="")
            obj = await osdu.get_resource(at, enc, grid2d_type, mp["uuid"])
            norm = osdu._normalize_obj(obj, mp["uuid"])
            interp_ref = norm.get("RepresentedInterpretation") or {}
            interp_title = interp_ref.get("Title") or ""
            if interp_title:
                mp["interpretation"] = interp_title
        except Exception as e:
            log.debug("[BD-MAPS] interpretation fetch failed for %s: %s", mp["uuid"], e)

    if proper_maps:
        await asyncio.gather(*[_fetch_interpretation(mp) for mp in proper_maps])

    log.info("[BD-MAPS] Discovered %d Grid2d objects (%d proper maps, hero=%s) across %d dataspaces",
             len(all_objs), len(proper_maps),
             proper_maps[0]["title"] if proper_maps else "none", len(ds_ids))
    return {"maps": proper_maps, "all": all_objs, "maps_total": len(proper_maps)}


async def _enrich_bd_production(
    data_block: Dict[str, Any],
    client: httpx.AsyncClient,
    storage_url: str,
    hdr: dict,
) -> Dict[str, Any]:
    """Fetch ColumnBasedTable production forecast from BD Parameters[].

    The OSDU ColumnBasedTable 1.4.0 schema stores column data under
    ``data.Table``:

    - ``Table.KeyColumns`` – list of key column defs (e.g. Year)
    - ``Table.Columns`` – list of value column defs
    - ``Table.ColumnValues`` – **positional array** of objects, each with
      either ``IntegerColumn`` or ``NumberColumn`` holding the values.
      Index *i* in the array corresponds to the column at the same index
      in *KeyColumns + Columns*.

    Returns a flat dict with template-friendly names::

        {"Years": [...], "OilRate_kSm3d": [...], ...}
    """
    params = data_block.get("Parameters") or []
    if not isinstance(params, list):
        return {}

    target_id = ""
    for p in params:
        if not isinstance(p, dict):
            continue
        dop = p.get("DataObjectParameter") or ""
        if "ColumnBasedTable" not in dop:
            continue
        keys = p.get("Keys") or []
        if any("ProductionForecast" in (kv.get("StringParameterKey") or "")
               for kv in keys if isinstance(kv, dict)):
            target_id = dop
            break

    if not target_id:
        return {}

    d: Optional[Dict[str, Any]] = None
    try:
        r = await client.get(f"{storage_url}/{target_id}", headers=hdr)
        if r.status_code == 200:
            d = (r.json() or {}).get("data", {}) or {}
        else:
            log.warning("[BD-PROD] CBT %s returned %d", target_id, r.status_code)
    except Exception as e:
        log.warning("[BD-PROD] OSDU fetch failed for %s: %s", target_id, e)

    if not d:
        return {}

    try:
        return _parse_cbt_production(d, target_id)
    except Exception as e:
        log.warning("[BD-PROD] Failed to parse production CBT %s: %s", target_id, e)
        return {}


def _parse_cbt_production(d: Dict[str, Any], target_id: str = "") -> Dict[str, Any]:
    """Parse a ColumnBasedTable ``data`` block into template-friendly dict."""
    tbl = d.get("Table") or {}
    key_cols = tbl.get("KeyColumns") or []
    val_cols = tbl.get("Columns") or []
    col_values = tbl.get("ColumnValues") or []
    if not col_values:
        return {}

    # Build ordered column name list: KeyColumns first, then Columns
    all_col_defs = key_cols + val_cols
    if len(all_col_defs) != len(col_values):
        log.warning("[BD-PROD] Column count mismatch: %d defs vs %d value arrays",
                    len(all_col_defs), len(col_values))

    # Extract values from each positional entry
    # Each entry is {"IntegerColumn": [...]} or {"NumberColumn": [...]}
    col_data: Dict[str, list] = {}
    for i, cv_entry in enumerate(col_values):
        if not isinstance(cv_entry, dict):
            continue
        name = all_col_defs[i].get("ColumnName", f"col_{i}") if i < len(all_col_defs) else f"col_{i}"
        # Pick whichever typed array is present
        vals = (cv_entry.get("IntegerColumn")
                or cv_entry.get("NumberColumn")
                or cv_entry.get("StringColumn")
                or cv_entry.get("BooleanColumn")
                or [])
        col_data[name] = vals

    # Map CBT column names → template keys
    # Supports both generic names (OilRate) and OPM Flow names (FOPR)
    name_map = {
        # Generic / legacy names
        "OilRate": "OilRate_kSm3d",
        "GasRate": "GasRate_kSm3d",
        "WaterRate": "WaterRate_kSm3d",
        "WaterInjRate": "WaterInjRate_kSm3d",
        "YearlyOil": "YearlyOil_MSm3",
        "CumulativeOil": "CumOil_MSm3",
        "WaterCut": "WaterCut_pct",
        "RecoveryFactor": "RecoveryFactor_pct",
        "WellsOnline": "WellsOnline",
        # OPM Flow / Eclipse summary vector names
        "FOPR": "OilRate_kSm3d",
        "FGPR": "GasRate_kSm3d",
        "FWPR": "WaterRate_kSm3d",
        "FWIR": "WaterInjRate_kSm3d",
        "FOPT": "CumOil_MSm3",
        "FPR": "FPR_barsa",
        "FWCT": "WaterCut_pct",
        "FGOR": "FGOR",
        "ProducersOnline": "WellsOnline",
        "InjectorsOnline": "InjectorsOnline",
    }

    result: Dict[str, Any] = {}
    # Key column → Years (handles both "Year" and "Date" column names)
    for kc in key_cols:
        cn = kc.get("ColumnName", "")
        if cn in col_data:
            result["Years"] = col_data[cn]

    # Value columns
    for vc in val_cols:
        cn = vc.get("ColumnName", "")
        if cn in col_data:
            tpl_key = name_map.get(cn, cn)
            result[tpl_key] = col_data[cn]

    # Extract summary from ext.equinor.ForecastSummary if present
    ext_eq = (d.get("ext") or {}).get("equinor") or {}
    summary = ext_eq.get("ForecastSummary") or {}
    if summary:
        result["summary"] = summary
    # Also carry forward the Note
    note = ext_eq.get("Note") or d.get("Description") or ""
    if note:
        result["Note"] = note

    if result.get("Years"):
        log.info("[BD-PROD] Loaded production forecast: %d years, %d columns",
                 len(result["Years"]), len(result) - 1)
    return result


# ──────────────────────────────────────────────────────────────────────────────
# BD enrichment: DevelopmentConcept WPC → ext.equinor.DevelopmentConcept
# ──────────────────────────────────────────────────────────────────────────────

# Fields to extract from the DevelopmentConcept v4 WPC data block.
# v4 uses structured sub-objects with OSDU *ID fields; we pass them through wholesale.
_DEVCONCEPT_FIELDS = (
    "Name", "Summary", "DecisionGate", "DecisionLevelID",
    "FacilityConcept", "WellPlan", "DrainageStrategy",
    "ReservoirTarget", "ProductionTechnology",
    "ConceptID",
)


async def _enrich_bd_developmentconcept(
    data_block: Dict[str, Any],
    client: httpx.AsyncClient,
    storage_url: str,
    hdr: dict,
) -> None:
    """Fetch DevelopmentConcept WPC from BD Parameters[] and inject into
    ``data.ext.equinor.DevelopmentConcept`` so templates render it unchanged.

    Looks for a Parameters entry with StringParameterKey 'DevelopmentConcept'.
    Falls back to local demo record if OSDU fetch fails.
    Only overwrites ext.equinor.DevelopmentConcept when WPC data is found.
    """
    params = data_block.get("Parameters") or []
    if not isinstance(params, list):
        return

    # Find the DevelopmentConcept WPC reference
    target_id = ""
    for p in params:
        if not isinstance(p, dict):
            continue
        dop = p.get("DataObjectParameter") or ""
        if "DevelopmentConcept" not in dop:
            continue
        keys = p.get("Keys") or []
        if any("DevelopmentConcept" in (kv.get("StringParameterKey") or "")
               for kv in keys if isinstance(kv, dict)):
            target_id = dop
            break

    if not target_id:
        return

    d: Optional[Dict[str, Any]] = None
    try:
        r = await client.get(f"{storage_url}/{target_id}", headers=hdr)
        if r.status_code == 200:
            d = (r.json() or {}).get("data", {}) or {}
        else:
            log.warning("[BD-DC] DevelopmentConcept %s returned %d", target_id, r.status_code)
    except Exception as e:
        log.warning("[BD-DC] OSDU fetch failed for %s: %s", target_id, e)

    if not d:
        return

    # Extract DevelopmentConcept fields from the WPC data block
    dcon: Dict[str, Any] = {}
    for key in _DEVCONCEPT_FIELDS:
        if key in d:
            dcon[key] = d[key]

    if not dcon:
        return

    # Inject into ext.equinor.DevelopmentConcept
    ext_eq = data_block.setdefault("ext", {}).setdefault("equinor", {})
    ext_eq["DevelopmentConcept"] = dcon
    log.info("[BD-DC] Injected DevelopmentConcept from WPC %s (%d fields)",
             target_id, len(dcon))
# Pages & actions
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/", response_class=RedirectResponse, summary="Redirect to ORES landing")
async def root_redirect():
    return RedirectResponse("/ores", status_code=302)


@app.get("/admin", response_class=HTMLResponse, summary="Admin: list dataspaces")
async def home(request: Request):
    # Render the shell immediately - dataspaces loaded async via JS
    return templates.TemplateResponse(
        request, "admin.html",
        {
            "view": "home",
            "dataspaces": [],
            # Defaults for the "Create Dataspace" form (prefilled values)
            "ds_default": os.getenv("DEFAULT_DATASPACE", ""),
            "default_legal_tag": osdu.DEFAULT_LEGAL_TAG,
            "default_owners": ",".join(osdu.DEFAULT_OWNERS),
            "default_viewers": ",".join(osdu.DEFAULT_VIEWERS),
            "default_countries": ",".join(osdu.DEFAULT_COUNTRIES),
        },
    )


@app.get("/admin/dataspaces.json", summary="Async dataspace list for admin page")
async def admin_dataspaces_json(request: Request):
    """JSON endpoint for client-side dataspace loading (cached 120 s)."""
    try:
        at = _access_token(request)
        dataspaces = await osdu.list_dataspaces(at)
    except Exception as e:
        log.warning("admin_dataspaces_json failed: %s", e)
        dataspaces = []
    items = []
    for x in dataspaces:
        if isinstance(x, dict):
            p = x.get("path") or x.get("Path") or x.get("DataspaceId") or ""
            if p:
                items.append({"path": p, "uri": x.get("uri", "")})
        elif isinstance(x, str) and x:
            items.append({"path": x, "uri": ""})
    return JSONResponse({"items": items})

@app.post("/dataspaces/create", summary="Create a dataspace with default legal/ACL")
async def dataspaces_create(
    request: Request,
    path: str = Form(...),
    legal: str = Form(osdu.DEFAULT_LEGAL_TAG),
    owners: str = Form(",".join(osdu.DEFAULT_OWNERS)),
    viewers: str = Form(",".join(osdu.DEFAULT_VIEWERS)),
    countries: str = Form(",".join(osdu.DEFAULT_COUNTRIES)),
    custom_json: str = Form("", description="Optional JSON to merge into CustomData"),
):
    at = _access_token(request)

    # Parse optional JSON block
    extra_custom: Dict[str, Any] = {}
    if custom_json and custom_json.strip():
        try:
            extra_custom = json.loads(custom_json)
            if not isinstance(extra_custom, dict):
                raise ValueError("Custom data must be a JSON object")
        except Exception as ex:
            return templates.TemplateResponse(
                request, "admin.html",
                {
                    "view": "home",
                    "dataspaces": [],
                    "ds_default": os.getenv("DEFAULT_DATASPACE", ""),
                    "default_legal_tag": osdu.DEFAULT_LEGAL_TAG,
                    "default_owners": ",".join(osdu.DEFAULT_OWNERS),
                    "default_viewers": ",".join(osdu.DEFAULT_VIEWERS),
                    "default_countries": ",".join(osdu.DEFAULT_COUNTRIES),
                    "error": "Invalid custom JSON",
                    "error_detail": str(ex),
                },
                status_code=400,
            )

    try:
        await osdu.create_dataspace(
            at,
            path,
            legal_tag=legal,
            owners=[x.strip() for x in owners.split(",") if x.strip()],
            viewers=[x.strip() for x in viewers.split(",") if x.strip()],
            countries=[x.strip() for x in countries.split(",") if x.strip()],
            extra_custom=extra_custom,
        )
    except HTTPStatusError as e:
        r = e.response
        return templates.TemplateResponse(
            request, "admin.html",
            {
                "view": "home",
                "dataspaces": [],
                "ds_default": os.getenv("DEFAULT_DATASPACE", ""),
                "default_legal_tag": osdu.DEFAULT_LEGAL_TAG,
                "default_owners": ",".join(osdu.DEFAULT_OWNERS),
                "default_viewers": ",".join(osdu.DEFAULT_VIEWERS),
                "default_countries": ",".join(osdu.DEFAULT_COUNTRIES),
                "error": f"Create failed: {r.status_code} {r.reason_phrase}",
                "error_detail": (r.text[:2000] if r.text else ""),
            },
            status_code=400,
        )
    return RedirectResponse(url=f"/d/{urllib.parse.quote(path, safe='')}", status_code=302)
# ──────────────────────────────────────────────────────────────────────────────
# HowTo - rendered markdown articles from the md/ directory
# ──────────────────────────────────────────────────────────────────────────────

_MD_DIR = _Path(__file__).resolve().parent.parent / "md"

# ── HowTo article catalog ────────────────────────────────────────────────
# Grouped structure for the HowTo index page.
# Each section has a title and a list of items.
# Items can optionally carry ``children`` (sub-articles shown indented).
_HOWTO_SECTIONS: list[dict] = [
    {
        "title": "HowTo",
        "items": [
            {
                "slug": "ores-overview",
                "file": "Readme.md",
                "title": "ORES Overview",
                "desc": "Web client capabilities, project layout & pipeline guide",
            },
            {
                "slug": "query-guide",
                "file": "Query.md",
                "title": "Querying Data",
                "desc": "REST, ETP, GraphQL & OSDU Search - all query paths explained",
            },
            {
                "slug": "business-decision",
                "file": "BusinessDecision.md",
                "title": "Business Decision",
                "desc": "Model DG1–DG4 decisions as BusinessDecision records",
                "children": [
                    {"slug": "bd-demo",      "file": "BdDemo.md",       "title": "BD Demo",       "desc": "Drogon DG2 worked example"},
                    {"slug": "volumes",      "file": "Volumes.md",      "title": "Volumes",        "desc": "ReservoirEstimatedVolumes WPC & fmu-dataio mapping"},
                    {"slug": "geolabelset",  "file": "GeoLabelSet.md",  "title": "GeoLabelSet",   "desc": "Reservoir volumes & statistics manifests"},
                    {"slug": "risk",         "file": "Risk.md",         "title": "Risk",           "desc": "Subsurface risk data management"},
                    {"slug": "uncertainty",  "file": "Uncertainty.md",  "title": "Uncertainty",    "desc": "FMU ensemble / Monte Carlo in OSDU"},
                ],
            },
            {
                "slug": "seismic-interp",
                "file": "SeisInt.md",
                "title": "Seismic Interpretation",
                "desc": "M27 data model, RDDMS patterns & Volantis demo",
            },
            {
                "slug": "crs-guide",
                "file": "CrsGuide.md",
                "title": "CRS Guide",
                "desc": "RESQML ⇄ OSDU coordinate reference systems",
            },
            {
                "slug": "strat-column",
                "file": "StratColumn.md",
                "title": "Stratigraphy",
                "desc": "Data model, tooling & workflow",
            },
        ],
    },
]

# Flat lookup: slug → (filename, title)  - used by the article route
_HOWTO_FLAT: dict[str, tuple[str, str]] = {}
for _sec in _HOWTO_SECTIONS:
    for _item in _sec["items"]:
        _HOWTO_FLAT[_item["slug"]] = (_item["file"], _item["title"])
        for _child in _item.get("children", []):
            _HOWTO_FLAT[_child["slug"]] = (_child["file"], _child["title"])

_md_extensions = [
    "tables",
    "fenced_code",
    "toc",
    "attr_list",
    "md_in_html",
    "pymdownx.superfences",
]


def _render_md(filename: str) -> tuple[str, str]:
    """Read a markdown file and return (html_body, toc_html)."""
    md_path = _MD_DIR / filename
    if not md_path.is_file():
        raise HTTPException(404, f"Article not found: {filename}")
    source = md_path.read_text(encoding="utf-8")
    converter = _md.Markdown(extensions=_md_extensions, extension_configs={
        "toc": {"permalink": True, "toc_depth": "2-4"},
        "pymdownx.superfences": {
            "custom_fences": [{
                "name": "mermaid",
                "class": "mermaid",
                "format": lambda source, language, class_name, options, md, **kw: (
                    f'<pre class="mermaid">{source}</pre>'
                ),
            }],
        },
    })
    html_body = converter.convert(source)
    toc_html = getattr(converter, "toc", "")
    return html_body, toc_html


@app.get("/ores", response_class=HTMLResponse, summary="ORES landing page")
@app.get("/howto", response_class=HTMLResponse, include_in_schema=False)
async def howto_index(request: Request):
    insts = get_instances()
    return templates.TemplateResponse(
        request, "ores.html",
        {
            "sections": _HOWTO_SECTIONS,
            "instances": {n: {"hostname": i.hostname, "partition": i.data_partition_id, "auth_mode": i.auth_mode} for n, i in insts.items()},
            "active_instance": get_active_name(),
        },
    )


@app.get("/howto/{slug}", response_class=HTMLResponse, summary="HowTo article")
async def howto_article(request: Request, slug: str):
    entry = _HOWTO_FLAT.get(slug)
    if not entry:
        raise HTTPException(404, f"Unknown article: {slug}")
    filename, title = entry
    html_body, toc_html = _render_md(filename)
    # Find children for this slug (if it's a parent article)
    children: list[dict] = []
    for sec in _HOWTO_SECTIONS:
        for item in sec["items"]:
            if item["slug"] == slug:
                children = item.get("children", [])
                break
    return templates.TemplateResponse(
        request, "howto_article.html",
        {
            "title": title,
            "slug": slug,
            "toc_html": toc_html,
            "article_html": html_body,
            "sections": _HOWTO_SECTIONS,
            "children": children,
        },
    )

