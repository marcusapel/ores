
from __future__ import annotations
import asyncio
import os
import re
import secrets
import urllib.parse
import logging
import json
from typing import List, Dict, Any, Optional, Set

from dotenv import load_dotenv
load_dotenv()  # must run before any module reads os.getenv at import time

import httpx
from httpx import HTTPStatusError
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
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

    # 2. Fallback — per-user session token (PKCE flow)
    if not access_token:
        try:
            sess_tokens = await tokens_from_session(request)
            if sess_tokens:
                access_token = sess_tokens.get("access_token")
        except Exception as e:
            log.warning("Session token failed: %s", e)

    # 3. No token at all — redirect to login page (for browser) or 401 (for API)
    if not access_token:
        if path.startswith("/api"):
            return JSONResponse({"error": "Authentication required. No env token and no session."}, status_code=401)
        return RedirectResponse("/login-page")

    request.state.access_token = access_token
    return await call_next(request)

# Attach routers & static
# Session middleware — added LAST so it is outermost and runs FIRST,
# making request.session available to all inner middleware.
app.add_middleware(
    SessionMiddleware,
    secret_key=_SECRET_KEY,
    session_cookie="ores_session",
    max_age=8 * 3600,          # 8 h session lifetime
    same_site="lax",
    https_only=False,           # allow http in local dev; set True behind TLS in prod
)

app.include_router(auth_router)  # keeps /auth diagnostics
app.include_router(ingest_router, prefix="/api")
app.include_router(strat_router)
app.include_router(analyse_router)
app.include_router(addgate_router)
app.include_router(keys_router)

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

# Log routes at startup (helps when a route goes missing)
log.info("Routes registered: %s", [getattr(r, "path", str(r)) for r in app.routes])

# ──────────────────────────────────────────────────────────────────────────────
# OSDU instance switching
# ──────────────────────────────────────────────────────────────────────────────

# Initialise instance registry at import time (reads INSTANCE_* env vars)
_all_instances = get_instances()
log.info("OSDU instances loaded: %s (active=%s)", list(_all_instances.keys()), get_active_name())
# Add /api/instances/switch to PUBLIC_PATHS so it doesn't require a token
# (switching happens before a valid token exists for the new instance)
PUBLIC_PATHS.add("/api/instances")
PUBLIC_PATHS.add("/api/instances/switch")


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


# ──────────────────────────────────────────────────────────────────────────────
# Login landing page (per-user PKCE mode)
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/login-page", response_class=HTMLResponse)
async def login_page(request: Request):
    """Serve the sign-in landing page (only reached when no env token is set)."""
    return templates.TemplateResponse("login.html", {"request": request})

# ──────────────────────────────────────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────────────────────────────────────

def _access_token(request: Request) -> str:
    at = getattr(request.state, "access_token", None)
    if not at:
        raise HTTPException(401, "Authentication failed")
    return at


# ──────────────────────────────────────────────────────────────────────────────
# Helpers — volume / BD enrichment
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

    # Known table markers — skip these
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
            at = hdr.get("Authorization", "").replace("Bearer ", "")
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

    # Pick ONE representative map for the dashboard image.
    # Preference order: DS_extract_simgrid > DS_extract_geogrid > first proper map.
    hero_map: List[Dict[str, Any]] = []
    if proper_maps:
        _PREF = ("ds_extract_simgrid", "ds_extract_geogrid", "ds_extract")
        for pref in _PREF:
            for mp in proper_maps:
                if mp["title"].lower().startswith(pref):
                    hero_map = [mp]
                    break
            if hero_map:
                break
        if not hero_map:
            hero_map = [proper_maps[0]]

    log.info("[BD-MAPS] Discovered %d Grid2d objects (%d proper maps, hero=%s) across %d dataspaces",
             len(all_objs), len(proper_maps),
             hero_map[0]["title"] if hero_map else "none", len(ds_ids))
    return {"maps": hero_map, "all": all_objs, "maps_total": len(proper_maps)}


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

# Fields to extract from the DevelopmentConcept WPC data block
_DEVCONCEPT_FIELDS = (
    "Summary", "WellCount", "ContingentWells", "MultilateralWells",
    "TemplateSlots", "DrillingCentres", "ReservoirFormation", "FieldArea",
    "WaterDepth_m", "DistanceToHost_km", "HostFacility", "TargetStartUp",
    "FlowlineSpec", "SubseaBoostingPump", "WaterTreatmentCapacity_m3d",
    "InjectionStrategy", "WellPlan",
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


def _parse_kind_inputs(kind: str, kinds_extra: str) -> List[str]:
    """
    Build an ordered, de-duplicated list of kinds from:
      - primary 'kind' input
      - optional 'kinds_extra' (comma / semicolon / newline separated)
    """
    out: List[str] = []
    seen: Set[str] = set()

    candidates: List[str] = []
    if kind:
        candidates.append(kind)

    if kinds_extra:
        for token in re.split(r"[\n,;]+", kinds_extra):
            token = token.strip()
            if token:
                candidates.append(token)

    for k in candidates:
        if k and k not in seen:
            out.append(k)
            seen.add(k)
    return out


def _collect_manifest_kinds() -> List[Dict[str, Any]]:
    """Return a static ordered list of OSDU kinds for the search dropdown.

    Ordered: BusinessDecision first, then master-data alphabetically,
    then work-product-component / dataset / dev alphabetically.
    """
    # ── Static kind list (all kinds present in demo/ manifests) ──
    _KINDS: list[str] = [
        "osdu:wks:master-data--BusinessDecision:1.0.0",
        "osdu:wks:master-data--Reservoir:2.0.0",
        "osdu:wks:master-data--ReservoirSegment:2.0.0",
        "osdu:wks:master-data--Risk:1.2.0",
        "osdu:wks:master-data--LocalBoundaryFeature:1.1.0",
        "osdu:wks:dataset--ETPDataspace:1.0.0",
        "osdu:wks:work-product-component--Activity:1.0.0",
        "osdu:wks:work-product-component--ActivityTemplate:1.0.0",
        "osdu:wks:work-product-component--ColumnBasedTable:1.4.0",
        "osdu:wks:work-product-component--Document:1.2.0",
        "osdu:wks:work-product-component--GenericBinGrid:1.0.0",
        "osdu:wks:work-product-component--GenericRepresentation:1.2.0",
        "osdu:wks:work-product-component--GeoLabelSet:1.0.0",
        "osdu:wks:work-product-component--HorizonInterpretation:1.2.0",
        "osdu:wks:work-product-component--LocalBoundaryFeature:1.2.0",
        "osdu:wks:work-product-component--LocalModelCompoundCrs:1.2.0",
        "osdu:wks:work-product-component--ReservoirEstimatedVolumes:1.1.0",
        "osdu:wks:work-product-component--SeismicBinGrid:1.3.0",
        "osdu:wks:work-product-component--SeismicHorizon:2.1.0",
        "osdu:wks:work-product-component--StratigraphicColumn:1.2.0",
        "osdu:wks:work-product-component--StratigraphicColumnRankInterpretation:1.3.0",
        "osdu:wks:work-product-component--StratigraphicUnitInterpretation:1.3.0",
        "osdu:wks:work-product-component--PersistedCollection:1.2.0",
        "osdu:wks:work-product-component--StructureMap:1.0.0",
        "dev:wks:work-product-component--DevelopmentConcept:1.0.0",
        "osdu:wks:reference-data--ChronoStratigraphicScheme:1.0.0",
        "osdu:wks:reference-data--ChronoStratigraphy:1.0.0",
    ]
    return [{"kind": k} for k in _KINDS]


async def _reverse_lookup(
    record_id: str,
    client: httpx.AsyncClient,
    search_url: str,
    hdr: Dict[str, str],
) -> List[Dict[str, Any]]:
    """Find records that reference *record_id* (reverse relationships).

    Uses OSDU Search API with a wildcard query on the record ID.
    Returns a list of ``{id, role, source_path}`` dicts compatible with
    ``extract_osdu_links()`` output.
    """
    try:
        payload = {
            "kind": "*:*:*:*",
            "query": f'"{record_id}"',
            "limit": 20,
            "returnedFields": ["id", "kind"],
        }
        r = await client.post(search_url, headers=hdr, json=payload)
        if r.status_code != 200:
            log.debug("[REV-LOOKUP] search returned %d for %s", r.status_code, record_id)
            return []
        hits = r.json().get("results") or []
        refs: List[Dict[str, Any]] = []
        for h in hits:
            hid = h.get("id", "")
            if hid == record_id:
                continue  # skip self
            if "reference-data--" in hid:
                continue
            refs.append({
                "id": hid,
                "role": "referenced-by",
                "source_path": "(reverse lookup)",
            })
        if refs:
            log.info("[REV-LOOKUP] %s referenced by %d records", record_id, len(refs))
        return refs
    except Exception as e:
        log.debug("[REV-LOOKUP] Failed for %s: %s", record_id, e)
        return []

# ──────────────────────────────────────────────────────────────────────────────
# Pages & actions
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/", response_class=RedirectResponse, summary="Redirect to HowTo")
async def root_redirect():
    return RedirectResponse("/howto", status_code=302)


@app.get("/admin", response_class=HTMLResponse, summary="Admin: list dataspaces")
async def home(request: Request):
    try:
        at = _access_token(request)
        dataspaces = await osdu.list_dataspaces(at)
    except Exception as e:
        log.warning("List dataspaces failed: %s", e)
        dataspaces = []
    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "view": "home",
            "dataspaces": dataspaces,
            # Defaults for the "Create Dataspace" form (prefilled values)
            "ds_default": os.getenv("DEFAULT_DATASPACE", ""),
            "default_legal_tag": osdu.DEFAULT_LEGAL_TAG,
            "default_owners": ",".join(osdu.DEFAULT_OWNERS),
            "default_viewers": ",".join(osdu.DEFAULT_VIEWERS),
            "default_countries": ",".join(osdu.DEFAULT_COUNTRIES),
        },
    )

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
                "admin.html",
                {
                    "request": request,
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
            "admin.html",
            {
                "request": request,
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
# Shared record enrichment — used by both search_run() and view_record()
# ──────────────────────────────────────────────────────────────────────────────

# Keys whose values are large arrays / blobs — shown separately, not in metadata pairs
_HEAVY_DATA_KEYS = frozenset({
    "ColumnBasedTable", "Columns", "ColumnValues", "ColumnNames",
    "SpatialPoint.AsIngestedCoordinates.persistableReferenceCrs",
    "VirtualProperties.DefaultLocation.AsIngestedCoordinates.persistableReferenceCrs",
    "SpatialPoint.Wgs84Coordinates",
    "VirtualProperties.DefaultLocation.Wgs84Coordinates",
})


def _flatten_osdu_data(
    data: Dict[str, Any],
    max_str: int = 400,
) -> list:
    """Flatten an OSDU data{} block into [{name, value}, ...] pairs.

    Skips heavy array/blob keys and truncates long values.
    Nested dicts/lists are JSON-stringified (compact).
    """
    pairs = []
    for k in sorted(data.keys()):
        if k in _HEAVY_DATA_KEYS:
            continue
        v = data[k]
        if v is None:
            pairs.append({"name": k, "value": None})
        elif isinstance(v, (str, int, float, bool)):
            sv = v if not isinstance(v, str) or len(v) <= max_str else v[:max_str] + "…"
            pairs.append({"name": k, "value": sv})
        elif isinstance(v, list):
            if len(v) <= 5 and all(isinstance(x, (str, int, float, bool, type(None))) for x in v):
                pairs.append({"name": k, "value": ", ".join(str(x) for x in v)})
            else:
                s = json.dumps(v, ensure_ascii=False)
                pairs.append({"name": k, "value": s[:max_str] + "…" if len(s) > max_str else s})
        elif isinstance(v, dict):
            s = json.dumps(v, ensure_ascii=False)
            pairs.append({"name": k, "value": s[:max_str] + "…" if len(s) > max_str else s})
        else:
            pairs.append({"name": k, "value": str(v)[:max_str]})
    return pairs


async def _enrich_record(
    full: Dict[str, Any],
    client: httpx.AsyncClient,
    storage_url: str,
    search_url: str,
    hdr: dict,
) -> Dict[str, Any]:
    """Enrich a raw OSDU Storage record with volumes, links, labels, metadata.

    Returns a dict ready for template rendering.
    """
    rid = full.get("id", "")
    data_block = full.get("data", {}) or {}
    ancestry = data_block.get("ancestry", {}) or {}
    volumes = _normalize_volumes(data_block)

    # BusinessDecision: pull headline volumes + linked WPC data
    bd_geolabel: Dict[str, Any] = {}
    bd_production: Dict[str, Any] = {}
    bd_activity: Dict[str, Any] = {}
    bd_maps: Dict[str, Any] = {"maps": [], "all": []}
    if "businessdecision" in (full.get("kind") or "").lower():
        # Run all BD sub-enrichments in parallel
        vol_task = _enrich_bd_volumes(data_block, client, storage_url, hdr) \
            if not (volumes or {}).get("ColumnValues") else asyncio.sleep(0)
        gl_task = _enrich_bd_geolabel(data_block, client, storage_url, hdr)
        prod_task = _enrich_bd_production(data_block, client, storage_url, hdr)
        dc_task = _enrich_bd_developmentconcept(data_block, client, storage_url, hdr)
        act_task = _enrich_bd_activity(data_block, client, storage_url, hdr)
        map_task = _enrich_bd_maps(data_block, client, storage_url, hdr)
        vol_r, gl_r, prod_r, _, act_r, map_r = await asyncio.gather(
            vol_task, gl_task, prod_task, dc_task, act_task, map_task,
            return_exceptions=True,
        )
        if isinstance(vol_r, dict) and vol_r.get("ColumnValues"):
            volumes = vol_r
        if isinstance(gl_r, dict):
            bd_geolabel = gl_r
        if isinstance(prod_r, dict):
            bd_production = prod_r
        if isinstance(act_r, dict):
            bd_activity = act_r
        if isinstance(map_r, dict):
            bd_maps = map_r

    # Generic WPC/master-data links (exclude reference-data)
    links = extract_osdu_links(data_block) or []

    # Reverse-lookup: find records that reference this one
    rev_links = await _reverse_lookup(rid, client, search_url, hdr)
    fwd_ids = {l["id"] for l in links}
    for rl in rev_links:
        if rl["id"] not in fwd_ids:
            links.append(rl)

    # Hydrate labels for linked records (bounded, parallel)
    linked_labels: Dict[str, Dict[str, Any]] = {}
    try:
        unique_lids = []
        for l in links[:25]:
            lid = l.get("id")
            if lid and lid not in linked_labels:
                unique_lids.append(lid)
                linked_labels[lid] = {}

        async def _fetch_label(lid: str):
            try:
                r_link = await client.get(f"{storage_url}/{lid}", headers=hdr)
                if r_link.status_code == 200:
                    rr = r_link.json()
                    nm = (rr.get("data") or {}).get("Name")
                    entry: Dict[str, Any] = {
                        "name": nm or lid,
                        "kind": rr.get("kind"),
                        "version": rr.get("version"),
                    }
                    if "ETPDataspace" in (rr.get("kind") or ""):
                        entry["data"] = rr.get("data") or {}
                    return (lid, entry)
            except Exception:
                pass
            return (lid, {"name": lid})

        results = await asyncio.gather(*[_fetch_label(lid) for lid in unique_lids])
        for lid, entry in results:
            linked_labels[lid] = entry
    except Exception as e:
        log.warning("[ENRICH] Linked record name hydration failed: %s", e)

    # Compact metadata pairs from data{}
    # For OSDU Storage records, flatten all data{} key-value pairs directly.
    # For RDDMS objects (with Citation etc.), fall back to extract_metadata_generic.
    metadata_pairs: list = []
    try:
        if "Citation" in data_block or "$type" in data_block:
            # RDDMS/RESQML object — use schema-aware extractor
            md = extract_metadata_generic(
                data_block, ds="",
                typ=full.get("kind", "") or "",
                uuid=full.get("id", "") or "",
                arrays=None, max_string_len=300, max_preview_items=5,
            )
            metadata_pairs = [
                p for p in (md.get("pairs", []) or [])
                if not (str(p.get("name")).lower() == "uri"
                        and str(p.get("value") or "").startswith("eml:///"))
            ]
        else:
            # OSDU Storage record — flatten data{} directly
            metadata_pairs = _flatten_osdu_data(data_block)
    except Exception as e:
        log.warning("[ENRICH] metadata_pairs extraction failed for %s: %s", rid, e)

    # Parse DDMSDatasets URIs for direct RDDMS visualisation (non-BD records)
    ddms_refs: list[dict[str, str]] = []
    for duri in (data_block.get("DDMSDatasets") or []):
        if not isinstance(duri, str):
            continue
        ds_m = re.search(r"dataspace\(['\"]?([^'\")\s]+)['\"]?\)", duri)
        uuid_m = re.search(r"\(([0-9a-f-]{36})\)", duri)
        if ds_m and uuid_m:
            rtype = "map" if "Grid2dRepresentation" in duri else "other"
            ddms_refs.append({
                "ds": ds_m.group(1),
                "uuid": uuid_m.group(1),
                "uri": duri,
                "rtype": rtype,
            })

    return {
        "id": full.get("id"),
        "kind": full.get("kind"),
        "version": full.get("version"),
        "data": data_block,
        "ancestry_parents": ancestry.get("parents", []) or [],
        "ancestry_children": ancestry.get("children", []) or [],
        "volumes": volumes,
        "links": links,
        "linked_labels": linked_labels,
        "metadata_pairs": metadata_pairs,
        "bd_geolabel": bd_geolabel,
        "bd_production": bd_production,
        "bd_activity": bd_activity,
        "bd_maps": bd_maps,
        "ddms_refs": ddms_refs,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Search (OSDU search v2) — enrich with storage fetch, ancestry, links, metadata
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/search", response_class=HTMLResponse, summary="Search form (OSDU search v2)")
async def search_page(request: Request):
    # Pre-fill demo values
    kind_options = _collect_manifest_kinds()
    default_kind = "osdu:wks:master-data--BusinessDecision:1.0.0"
    return templates.TemplateResponse(
        "search.html",
        {
            "request": request,
            "kind": default_kind,
            "kinds_extra": "",
            "kind_options": kind_options,
            "q": "*",
            "limit": 50,
            "returnedFields": "id,kind,version",
        },
    )

@app.post("/search/run", response_class=HTMLResponse)
async def search_run(
    request: Request,
    kind: str = Form("osdu:wks:work-product-component--ReservoirEstimatedVolumes:1.1.0"),
    kinds_extra: str = Form(""),
    query: str = Form("*"),
    limit: int = Form(50),
):
    """
    Run an OSDU Search v2 query, then enrich each hit:
    • Fetch the full storage record (data{}).
    • Surface ancestry parents/children.
    • Normalize Volumes (ColumnBasedTable) for REV WPCs.
    • Extract WPC/master-data links from data{} (exclude reference-data).
    • Hydrate labels (Name/kind/version) for linked records (bounded).
    • Build compact metadata_pairs from data{}.
    Renders: templates/search.html with:
    results = {
      results: [{ id, kind, version, data, ancestry_parents, ancestry_children,
                  volumes, links, linked_labels, metadata_pairs }, ...],
      totalCount
    }
    """
    at = _access_token(request)
    search_url = f"https://{osdu.OSDU_BASE_URL}/api/search/v2/query"
    storage_url = f"https://{osdu.OSDU_BASE_URL}/api/storage/v2/records"
    hdr = osdu.headers(at)

    search_kinds = _parse_kind_inputs(kind, kinds_extra)
    if not search_kinds:
        search_kinds = [kind]

    try:
        enriched_results: List[Dict[str, Any]] = []
        seen_record_ids: Set[str] = set()
        merged_total_count = 0
        async with httpx.AsyncClient(timeout=60) as client:
            # ── Phase 1: Search all kinds (sequential — each is one API call) ──
            all_hit_ids: List[str] = []
            for current_kind in search_kinds:
                payload = {
                    "kind": current_kind,
                    "query": query,
                    "limit": int(limit),
                    "returnedFields": ["id", "kind", "version"],
                    "trackTotalCount": True,
                }
                r = await client.post(search_url, headers=hdr, json=payload)
                r.raise_for_status()
                res = r.json()
                merged_total_count += int(res.get("totalCount") or len(res.get("results", [])))
                log.info(
                    "[SEARCH] kind=%s status=%d hits=%d",
                    current_kind,
                    r.status_code,
                    len(res.get("results", [])),
                )
                for rec in res.get("results", []):
                    rid = rec.get("id")
                    if rid and rid not in seen_record_ids:
                        seen_record_ids.add(rid)
                        all_hit_ids.append(rid)
                    if len(all_hit_ids) >= int(limit):
                        break
                if len(all_hit_ids) >= int(limit):
                    break

            # ── Phase 2: Fetch full storage records in parallel ──────────────
            async def _fetch_full(rid: str):
                try:
                    r_full = await client.get(f"{storage_url}/{rid}", headers=hdr)
                    if r_full.status_code == 200:
                        return r_full.json()
                    log.warning("[SEARCH] Full record fetch failed for %s: %d", rid, r_full.status_code)
                except Exception as e:
                    log.warning("[SEARCH] Exception fetching %s: %s", rid, e)
                return None

            full_records = await asyncio.gather(*[_fetch_full(rid) for rid in all_hit_ids])
            valid_records = [f for f in full_records if f is not None]

            # ── Phase 3: Enrich all records in parallel ──────────────────────
            enriched_results = list(await asyncio.gather(*[
                _enrich_record(full, client, storage_url, search_url, hdr)
                for full in valid_records
            ]))

        return templates.TemplateResponse(
            "search.html",
            {
                "request": request,
                "results": {
                    "results": enriched_results,
                    "totalCount": merged_total_count or len(enriched_results),
                },
                "kind": "",
                "kinds_extra": "",
                "kind_options": _collect_manifest_kinds(),
                "selected_kinds": search_kinds,
                "q": "*",
                "limit": limit,
            },
        )
    except httpx.HTTPStatusError as e:
        r = e.response
        log.warning("[SEARCH] HTTP error: %s %s", r.status_code, r.text[:512] if r.text else "")
        return templates.TemplateResponse(
            "search.html",
            {
                "request": request,
                "error": f"Search failed: {r.status_code} {r.reason_phrase}",
                "error_detail": (r.text[:2000] if r.text else ""),
                "kind": kind,
                "kinds_extra": kinds_extra,
                "kind_options": _collect_manifest_kinds(),
                "q": query,
                "limit": limit,
            },
            status_code=r.status_code or 500,
        )
    except Exception as e:
        log.exception("[SEARCH] Unexpected error: %s", e)
        return templates.TemplateResponse(
            "search.html",
            {
                "request": request,
                "error": "Unexpected error",
                "error_detail": "See server logs",
                "kind": kind,
                "kinds_extra": kinds_extra,
                "kind_options": _collect_manifest_kinds(),
                "q": query,
                "limit": limit,
            },
            status_code=500,
        )

@app.get("/search/view/{record_id}", response_class=HTMLResponse)
async def view_record(request: Request, record_id: str):
    """Fetch a single record by ID and render it through the search template."""
    at = _access_token(request)
    storage_url = f"https://{osdu.OSDU_BASE_URL}/api/storage/v2/records"
    search_url = f"https://{osdu.OSDU_BASE_URL}/api/search/v2/query"
    hdr = osdu.headers(at)
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get(f"{storage_url}/{record_id}", headers=hdr)
            r.raise_for_status()
            full = r.json()

            enriched = await _enrich_record(
                full, client, storage_url, search_url, hdr)

        return templates.TemplateResponse(
            "search.html",
            {
                "request": request,
                "results": {"results": [enriched], "totalCount": 1},
                "kind": full.get("kind", ""),
                "kinds_extra": "",
                "kind_options": _collect_manifest_kinds(),
                "q": record_id,
                "limit": 1,
            },
        )
    except HTTPStatusError as e:
        return templates.TemplateResponse(
            "search.html",
            {
                "request": request,
                "error": f"Record fetch failed: {e.response.status_code}",
                "error_detail": (e.response.text[:2000] if e.response.text else ""),
                "kind": "",
                "kinds_extra": "",
                "kind_options": _collect_manifest_kinds(),
                "q": record_id,
                "limit": 1,
            },
            status_code=e.response.status_code or 500,
        )
    except Exception as e:
        log.exception("[VIEW] Unexpected error: %s", e)
        return templates.TemplateResponse(
            "search.html",
            {
                "request": request,
                "error": "Unexpected error",
                "error_detail": "See server logs",
                "kind": "",
                "kinds_extra": "",
                "kind_options": _collect_manifest_kinds(),
                "q": record_id,
                "limit": 1,
            },
            status_code=500,
        )


# ──────────────────────────────────────────────────────────────────────────────
# HowTo — rendered markdown articles from the md/ directory
# ──────────────────────────────────────────────────────────────────────────────

_MD_DIR = _Path(__file__).resolve().parent.parent / "md"

# ── HowTo article catalog ────────────────────────────────────────────────
# Grouped structure for the HowTo index page.
# Each section has a title and a list of items.
# Items can optionally carry ``children`` (sub-articles shown indented).
_HOWTO_SECTIONS: list[dict] = [
    {
        "title": "Ores Design and Usage",
        "items": [
            {
                "slug": "ores-overview",
                "file": "Readme.md",
                "title": "ORES Overview",
                "desc": "Web client capabilities, project layout & pipeline guide",
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
                "title": "Stratigraphic Column",
                "desc": "Data model, tooling & workflow",
            },
        ],
    },
]

# Flat lookup: slug → (filename, title)  — used by the article route
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


@app.get("/howto", response_class=HTMLResponse, summary="HowTo — documentation articles")
async def howto_index(request: Request):
    insts = get_instances()
    return templates.TemplateResponse(
        "howto.html",
        {
            "request": request,
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
        "howto_article.html",
        {
            "request": request,
            "title": title,
            "slug": slug,
            "toc_html": toc_html,
            "article_html": html_body,
            "sections": _HOWTO_SECTIONS,
            "children": children,
        },
    )

