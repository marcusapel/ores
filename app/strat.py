
from __future__ import annotations
import asyncio
import json
import logging
import os
import sys
import urllib.parse
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import httpx
from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from . import osdu
from . import auth as _auth

log = logging.getLogger("rddms-admin.strat")

# SMDA API key (Ocp-Apim-Subscription-Key) — used when PKCE login is unavailable
SMDA_API_KEY: str = os.getenv("SMDA_API_KEY", "")

# Import the stratcolumnhandler from demo/strat/
_handler_dir = os.path.join(os.path.dirname(__file__), "..", "demo", "strat")
if _handler_dir not in sys.path:
    sys.path.insert(0, os.path.abspath(_handler_dir))
try:
    from stratcolumnhandler import StratColumn as _StratColumn
except ImportError:
    _StratColumn = None

router = APIRouter()
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))

def _access_token(request: Request) -> str:
    at = getattr(request.state, "access_token", None)
    if not at:
        raise HTTPException(401, "Authentication failed")
    return at

async def _osdu_get_record(request: Request, record_id: str) -> dict:
    at = _access_token(request)
    base = f"https://{osdu.OSDU_BASE_URL}/api/storage/v2/records"
    url = f"{base}/{urllib.parse.quote(record_id, safe='')}"
    hdr = osdu.headers(at)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, headers=hdr)
        if r.status_code == 200:
            return r.json() or {}
        if r.status_code == 404:
            return {}
        r.raise_for_status()
    return {}

def _safe(lst):
    return lst if isinstance(lst, list) else []

def _as_id(x: Any) -> str:
    """
    Normalize inputs that can be:
      - a string id, or
      - an object with 'id' (Storage record ref), or
      - an object with '$ref'/'recordId' (defensive)
    Returns the ID as-is (no trailing-colon stripping) because some OSDU records
    (e.g. ICS2017 chrono) store the trailing ':' as part of their canonical ID.
    """
    if isinstance(x, str):
        return x.strip()
    if isinstance(x, dict):
        raw = x.get("id") or x.get("recordId") or x.get("$ref") or ""
        return raw.strip()
    return ""



def _get_data(rec):
    return rec.get("data") or {}

def _extract_ages(unit_rec: dict, chrono_rec: dict):
    """Return (topMa, baseMa) as floats, or (None, None)."""
    cd = _get_data(chrono_rec) if chrono_rec else {}
    ud = _get_data(unit_rec) if unit_rec else {}
    top = (
        cd.get("AgeBegin") or cd.get("TopMa") or cd.get("AgeBeginMa")
        or ud.get("OlderPossibleAge") or ud.get("TopMa")
    )
    base = (
        cd.get("AgeEnd") or cd.get("BaseMa") or cd.get("AgeEndMa")
        or ud.get("YoungerPossibleAge") or ud.get("BaseMa")
    )
    try:
        return (float(top), float(base))
    except (TypeError, ValueError):
        return (None, None)

def _flat_unit_fields(unit_rec: dict, chrono_rec: dict) -> dict:
    """Extract flat convenience fields from a unit + chrono pair."""
    ud = _get_data(unit_rec) if unit_rec else {}
    cd = _get_data(chrono_rec) if chrono_rec else {}
    top, base = _extract_ages(unit_rec, chrono_rec)
    name = ud.get("Name") or cd.get("Name") or ""
    color = cd.get("Colour") or cd.get("Color") or None
    code = cd.get("Code") or ""
    return {"name": name, "topMa": top, "baseMa": base, "color": color, "code": code}

def _label_from_ref_id(val: str) -> str:
    if not val:
        return ""

def _fmt_ma(v) -> str:
    """Format an age in Ma for display in synthetic unit labels."""
    if v is None:
        return "?"
    f = float(v)
    if f == int(f):
        return str(int(f))
    return f"{f:.2f}".rstrip("0").rstrip(".")
    parts = val.strip().split(":")
    if len(parts) >= 2 and parts[-1] == "":
        return parts[-2]
    return parts[-1] if parts else val

@router.get("/strat", response_class=HTMLResponse)
async def strat_page(request: Request):
    return templates.TemplateResponse("strat.html", {
        "request": request,
        "partition": osdu.DATA_PARTITION_ID or "data",
    })

@router.get("/api/strat/search.json")
async def strat_search(
    request: Request,
    q: str = Query("*"),
    limit: int = Query(20, ge=1, le=200),
):
    at = _access_token(request)
    search_url = f"https://{osdu.OSDU_BASE_URL}/api/search/v2/query"
    storage_url = f"https://{osdu.OSDU_BASE_URL}/api/storage/v2/records"
    hdr = osdu.headers(at)

    payload = {
        "kind": "osdu:wks:work-product-component--StratigraphicColumn:*",
        "query": q or "*",
        "limit": int(limit),
        "returnedFields": ["id", "kind", "version", "data.Name"],
        "trackTotalCount": True,
    }

    items: List[Dict[str, Any]] = []
    total = 0

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(search_url, headers=hdr, json=payload)
        r.raise_for_status()
        res = r.json() or {}
        total = res.get("totalCount") or len(res.get("results") or [])

        for rec in res.get("results") or []:
            rid = rec.get("id")
            if not rid:
                continue
            # prefer the projected name if present
            name = ((rec.get("data") or {}).get("Name")) or ""
            if not name:
                try:
                    rf = await client.get(f"{storage_url}/{rid}", headers=hdr)
                    if rf.status_code == 200:
                        full = rf.json() or {}
                        name = (full.get("data") or {}).get("Name") or ""
                except Exception:
                    pass
            items.append({
                "id": rid,
                "name": name or rid,
                "kind": rec.get("kind") or "",
                "version": rec.get("version"),
            })

    return JSONResponse({"items": items, "total": total})


def _ids(val: Any) -> List[str]:
    """Extract a list of record IDs from heterogeneous inputs."""
    if isinstance(val, list):
        out = []
        for item in val:
            s = _as_id(item)
            if s:
                out.append(s)
        return out
    s = _as_id(val)
    return [s] if s else []

async def _storage_fetch_many(request: Request, ids: List[str]) -> Dict[str, dict]:
    """Batch-fetch records.  Handles both UUID IDs (no trailing ':') and named IDs
    (trailing ':' is canonical).  After the first pass, any not-found IDs are retried
    with the colon toggled.  Results are keyed under both forms for easy lookup."""
    at = _access_token(request)
    base = f"https://{osdu.OSDU_BASE_URL}/api/storage/v2"
    hdr = osdu.headers(at)

    # Normalize heterogeneous inputs (str | dict) -> str ids
    norm_ids: List[str] = []
    for i in ids or []:
        s = _as_id(i)
        if s:
            norm_ids.append(s)

    # Dedupe while preserving order
    uniq = list(dict.fromkeys(norm_ids))
    if not uniq:
        return {}

    results: Dict[str, dict] = {}

    async def post_batch(client: httpx.AsyncClient, chunk: List[str]) -> List[str]:
        url = f"{base}/query/records:batch"
        r = await client.post(url, headers=hdr, json={"records": chunk})
        if r.status_code == 404:
            raise FileNotFoundError("records:batch not available")
        r.raise_for_status()
        data = r.json() or {}
        recs = data.get("records")
        not_found: List[str] = data.get("notFound") or []
        if isinstance(recs, list):
            for item in recs:
                if isinstance(item, dict):
                    rid = item.get("id") or (item.get("record") or {}).get("id")
                    body = item.get("record") or item
                    if rid and isinstance(body, dict):
                        results[rid] = body
        elif isinstance(data, list):
            for body in data:
                if isinstance(body, dict) and body.get("id"):
                    results[body["id"]] = body
        return not_found

    async def get_one(client: httpx.AsyncClient, rid: str, sem: asyncio.Semaphore) -> None:
        url = f"{base}/records/{urllib.parse.quote(rid, safe='')}"
        async with sem:
            r = await client.get(url, headers=hdr)
            if r.status_code == 200:
                results[rid] = r.json() or {}
            elif r.status_code != 404:
                r.raise_for_status()

    async with httpx.AsyncClient(timeout=30, http2=True) as client:
        chunks = [uniq[i:i+20] for i in range(0, len(uniq), 20)]
        try:
            # Pass 1: try all IDs as-is
            nf_lists = await asyncio.gather(*(post_batch(client, c) for c in chunks))
            all_not_found = [nf for sublist in nf_lists for nf in sublist]

            # Pass 2: retry not-found IDs with toggled trailing colon
            retry = []
            for nf_id in all_not_found:
                alt = nf_id[:-1] if nf_id.endswith(":") else nf_id + ":"
                if alt not in results:
                    retry.append(alt)
            if retry:
                retry_chunks = [retry[i:i+20] for i in range(0, len(retry), 20)]
                await asyncio.gather(*(post_batch(client, c) for c in retry_chunks))

        except FileNotFoundError:
            # Fallback: individual GET for each ID
            sem = asyncio.Semaphore(12)
            await asyncio.gather(*(get_one(client, rid, sem) for rid in uniq))
            # Retry with toggled colon for missing
            missing = [rid for rid in uniq if rid not in results]
            retry2 = []
            for rid in missing:
                alt = rid[:-1] if rid.endswith(":") else rid + ":"
                if alt not in results:
                    retry2.append(alt)
            if retry2:
                await asyncio.gather(*(get_one(client, rid, sem) for rid in retry2))

    # Store results under both colon forms for easy caller lookups
    for rid in list(results.keys()):
        body = results[rid]
        alt = rid[:-1] if rid.endswith(":") else rid + ":"
        if alt not in results:
            results[alt] = body

    return results
        

@router.get("/api/strat/column.json")
async def get_strat_column(
    request: Request,
    id: str = Query(..., description="StratigraphicColumn record id"),
    enrich: bool = Query(True, description="Fetch/attach full unit/chrono records"),
) -> JSONResponse:
    """
    Load a StratigraphicColumn (WPC) and return a model for a rank-by-age matrix:

      {
        "column": {...},  # the column WPC record
        "ranks": [
          {
            "rankName": "System" | "Series" | "Group" | "Formation" | ...,
            "isChrono": true|false,                # true if this rank lists Chronostratigraphy refs
            "rank": {...},                         # original rank record (optional use)
            "units": [                             # ordered (older → younger), non-overlapping per rank
              { "unit": {... or {}}, "chrono": {... or {} } },
              ...
            ]
          },
          ...
        ]
      }

    Notes:
      - A StratigraphicColumn contains an ordered list of StratigraphicColumnRankInterpretation.           (Worked Example)  [1](https://www.geeksforgeeks.org/python/fastapi-pydantic-2/)
      - Each RankInterpretation collects an ordered list of StratigraphicUnitInterpretation with the
        intention to create a column of non-overlapping intervals (base of one is top of next).            [1](https://www.geeksforgeeks.org/python/fastapi-pydantic-2/)
      - Chronostratigraphic ranks (Systems/Series) provide the time framework; we mark them as isChrono
        when rank lists ChronoStratigraphy references.                                                     (Authoring schema) [2](https://stackoverflow.com/questions/78049428/why-when-i-include-a-llama-index-module-do-i-get-pydantic-validation-errors-with)
    """
    # 1) Fetch the StratigraphicColumn WPC
    col = await _osdu_get_record(request, id)
    if not col or not isinstance(col, dict):
        raise HTTPException(404, detail="Column not found")

    kind = col.get("kind", "")
    if not kind.startswith("osdu:wks:work-product-component--StratigraphicColumn:"):
        raise HTTPException(400, detail="Record is not a StratigraphicColumn")

    dcol = _get_data(col)

    # 2) Read ordered rank IDs (use canonical key; tolerate alternates if present)
    rank_ids = _ids(
        dcol.get("StratigraphicColumnRankInterpretationSet")
        or dcol.get("RankInterpretationSet")
        or []
    )
    if not rank_ids:
        # Return minimal structure; UI will handle empty ranks
        return JSONResponse({"column": col, "ranks": []})

    # 3) Fetch all ranks in one go
    ranks_by_id = await _storage_fetch_many(request, rank_ids)

    # 4) Collect unit IDs and chrono IDs referenced by ranks (both rank-level chrono sets and unit-level pointers)
    unit_ids_all: List[str] = []
    chrono_ids_all: List[str] = []

    for rid in rank_ids:
        rk = ranks_by_id.get(rid) or {}
        drk = _get_data(rk)

        # Rank-level chrono references (Systems/Series)
        chrono_ids_all.extend(_ids(drk.get("ChronoStratigraphySet") or drk.get("ChronostratigraphySet")))

        # Rank-level unit interpretations (Groups/Formations or user-defined)
        unit_ids_all.extend(_ids(drk.get("StratigraphicUnitInterpretationSet")))

    # 5) Fetch units, then follow each unit’s chrono pointer (ChronoStratigraphyID) if present
    units_by_id = await _storage_fetch_many(request, unit_ids_all) if unit_ids_all else {}
    for u in units_by_id.values():
        ud = _get_data(u)
        cid = _as_id(ud.get("ChronoStratigraphyID") or ud.get("ChronostratigraphyID") or "")
        if cid:
            chrono_ids_all.append(cid)

    # 6) Fetch all chrono records (deduped by the batched helper)
    chron_by_id = await _storage_fetch_many(request, chrono_ids_all) if chrono_ids_all else {}

    # 7) Assemble ranks in the original order with:
    #      - rankName: from data.Name or the StratigraphicColumnRankUnitType label (System/Series/Group/Formation/…)
    #      - isChrono: True if rank lists chrono refs and has no unit interpretations (as per OSDU usage)
    #      - units:    ordered older→younger; rank-level Chrono entries first, then unit interpretations
    ranks_model: List[Dict[str, Any]] = []

    def _age_key(u: Dict[str, Any]):
        """Sort key using pre-computed flat fields: older (larger topMa) first."""
        top = u.get("topMa")
        base = u.get("baseMa")
        if top is not None and base is not None:
            return (-top, base)
        return (float("inf"), float("inf"))

    for rid in rank_ids:
        rk = ranks_by_id.get(rid)
        if not rk:
            continue
        drk = _get_data(rk)

        # Rank name: prefer explicit Name; otherwise derive from reference value StratigraphicColumnRankUnitType
        rank_name = (
            drk.get("Name")
            or _label_from_ref_id(drk.get("StratigraphicColumnRankUnitType") or "")
            or "Unspecified"
        )

        # Chrono-vs-Unit identification at rank level
        chrono_ids = _ids(drk.get("ChronoStratigraphySet") or drk.get("ChronostratigraphySet"))
        unit_ids   = _ids(drk.get("StratigraphicUnitInterpretationSet"))
        is_chrono_rank = bool(chrono_ids) and not bool(unit_ids)

        # Units bucket
        units_model: List[Dict[str, Any]] = []

        # A) Rank-level chrono items (Systems/Series): carry ages & colour from reference data
        for cid in chrono_ids:
            crec = chron_by_id.get(cid)
            if crec:
                ff = _flat_unit_fields(None, crec)
                units_model.append({"unit": {}, "chrono": crec, **ff})

        # B) Rank-level unit interpretations: attach chrono if the unit points to one (ChronoStratigraphyID)
        for uid in unit_ids:
            urec = units_by_id.get(uid)
            if not urec:
                continue
            ud = _get_data(urec)
            cid = _as_id(ud.get("ChronoStratigraphyID") or ud.get("ChronostratigraphyID") or "")
            cobj = chron_by_id.get(cid) if cid else {}
            ff = _flat_unit_fields(urec, cobj)
            units_model.append({"unit": urec, "chrono": cobj, **ff})

        # C) Order units older→younger for non-overlap per rank (as intended by the OSDU model)
        units_model.sort(key=_age_key)

        ranks_model.append({
            "rankName": rank_name,
            "isChrono": is_chrono_rank,
            "rank": rk,
            "unitCount": len(units_model),
            "units": units_model
        })

    # 8) Fill gaps: for each consecutive pair of ranks, if a unit at rank N
    #    has no children at rank N+1 (i.e. no unit at N+1 whose age range
    #    is contained within the N-unit), insert a synthetic placeholder
    #    unit at rank N+1. This ensures the hierarchical table has no
    #    undefined white cells — missing data is visually declared.
    #
    #    The OSDU / ICS chrono model is inherently hierarchical and
    #    non-overlapping per rank.  Gaps in the data therefore represent
    #    missing information (not geological gaps) and should be declared.
    #
    #    Synthetic placeholders cascade through subsequent ranks: if Eonothem
    #    "Hadean" has no Erathem children, the synthetic Erathem entry itself
    #    propagates to System, SubSystem, Series, etc. — ensuring every cell
    #    in the table is accounted for.  The `_originalName` field preserves
    #    the real ancestor name so labels stay clean (no nested nesting).
    def _is_contained(child_top, child_base, parent_top, parent_base, tol=0.5):
        """True if child age range fits within parent (with 0.5 Ma tolerance)."""
        if None in (child_top, child_base, parent_top, parent_base):
            return False
        return child_top <= parent_top + tol and child_base >= parent_base - tol

    for ri in range(len(ranks_model) - 1):
        parent_rank = ranks_model[ri]
        child_rank = ranks_model[ri + 1]
        child_units = child_rank["units"]

        new_children: List[Dict[str, Any]] = []
        for pu in parent_rank["units"]:
            p_top = pu.get("topMa")
            p_base = pu.get("baseMa")
            if p_top is None or p_base is None:
                continue
            # Use original ancestor name for clean labels (avoid nested "((...) — undifferentiated)")
            p_name = pu.get("_originalName") or pu.get("name") or ""

            # Find existing children at the next rank contained in this parent
            # (consider ALL units — real and synthetic — for containment)
            kids = [
                cu for cu in child_units
                if _is_contained(cu.get("topMa"), cu.get("baseMa"), p_top, p_base)
            ]

            if not kids:
                # No children at all → insert one synthetic placeholder
                # covering the full parent age range
                new_children.append({
                    "unit": {},
                    "chrono": {},
                    "name": f"({p_name} — undifferentiated)",
                    "_originalName": p_name,
                    "topMa": p_top,
                    "baseMa": p_base,
                    "color": None,
                    "code": "",
                    "_synthetic": True,
                })
            else:
                # Children exist but may not tile the full parent range.
                # Sort kids older-first and look for age gaps.
                # Only consider non-synthetic units for gap detection
                # (synthetics already placed from earlier rounds shouldn't
                # count as real coverage)
                real_kids = [k for k in kids if not k.get("_synthetic")]
                if not real_kids:
                    # All children are synthetic from earlier rounds — skip
                    continue
                kids_sorted = sorted(real_kids, key=lambda c: -(c.get("topMa") or 0))
                # Gap at top: parent.topMa → first child.topMa
                first_top = kids_sorted[0].get("topMa")
                if first_top is not None and p_top - first_top > 0.5:
                    new_children.append({
                        "unit": {}, "chrono": {},
                        "name": f"(not defined, {_fmt_ma(p_top)}–{_fmt_ma(first_top)} Ma)",
                        "_originalName": f"not defined {_fmt_ma(p_top)}–{_fmt_ma(first_top)} Ma",
                        "topMa": p_top, "baseMa": first_top,
                        "color": None, "code": "", "_synthetic": True,
                    })
                # Gaps between consecutive children
                for ci in range(len(kids_sorted) - 1):
                    cur_base = kids_sorted[ci].get("baseMa")
                    nxt_top = kids_sorted[ci + 1].get("topMa")
                    if cur_base is not None and nxt_top is not None and cur_base - nxt_top > 0.5:
                        new_children.append({
                            "unit": {}, "chrono": {},
                            "name": f"(not defined, {_fmt_ma(cur_base)}–{_fmt_ma(nxt_top)} Ma)",
                            "_originalName": f"not defined {_fmt_ma(cur_base)}–{_fmt_ma(nxt_top)} Ma",
                            "topMa": cur_base, "baseMa": nxt_top,
                            "color": None, "code": "", "_synthetic": True,
                        })
                # Gap at base: last child.baseMa → parent.baseMa
                last_base = kids_sorted[-1].get("baseMa")
                if last_base is not None and last_base - p_base > 0.5:
                    new_children.append({
                        "unit": {}, "chrono": {},
                        "name": f"(not defined, {_fmt_ma(last_base)}–{_fmt_ma(p_base)} Ma)",
                        "_originalName": f"not defined {_fmt_ma(last_base)}–{_fmt_ma(p_base)} Ma",
                        "topMa": last_base, "baseMa": p_base,
                        "color": None, "code": "", "_synthetic": True,
                    })

        if new_children:
            # Deduplicate synthetic entries that cover overlapping age ranges
            # (can happen when umbrella units like "Precambrian" overlap real units)
            deduped: List[Dict[str, Any]] = []
            seen_ranges: set = set()
            for nc in new_children:
                key = (round(nc.get("topMa", 0), 2), round(nc.get("baseMa", 0), 2))
                if key not in seen_ranges:
                    seen_ranges.add(key)
                    deduped.append(nc)
            child_rank["units"] = child_rank["units"] + deduped
            child_rank["units"].sort(key=_age_key)
            child_rank["unitCount"] = len(child_rank["units"])

    # 9) Return column model
    return JSONResponse({
        "column": col,
        "ranks": ranks_model
    })


# =====================================================================
# IMPORT / CONVERT / INGEST endpoints
# =====================================================================

@router.post("/api/strat/import/ow")
async def import_ow_json(
    request: Request,
    file: UploadFile = File(...),
    partition: str = Form("data"),
):
    """Upload an OpenWorks JSON file; convert it to an OSDU WPC bundle.

    Returns the OSDU bundle JSON (ready for ingestion via /api/strat/ingest).
    """
    if _StratColumn is None:
        raise HTTPException(500, "stratcolumnhandler not available on this deployment")

    try:
        raw = await file.read()
        doc = json.loads(raw)
    except Exception as e:
        raise HTTPException(400, f"Invalid JSON file: {e}")

    try:
        col = _StratColumn.from_openworks_json(doc)
        bundle = col.to_osdu_bundle(partition=partition)
    except Exception as e:
        raise HTTPException(422, f"Conversion error: {e}")

    return JSONResponse({
        "source": "openworks",
        "columnName": col.name,
        "rankCount": len(col.ranks),
        "unitCount": sum(len(r.units) for r in col.ranks if r.kind == "litho"),
        "bundle": bundle,
    })


async def _smda_auth(request: Request) -> dict:
    """Return SMDA auth kwargs for stratcolumnhandler calls.

    Returns a dict with access_token and/or api_key.
    The API Gateway needs both a Bearer token (targeting the SMDA resource)
    and an Ocp-Apim-Subscription-Key.
    """
    kw: dict = {}
    token = await _auth.smda_access_token(request)
    if token:
        kw["access_token"] = token
    if SMDA_API_KEY:
        kw["api_key"] = SMDA_API_KEY
    if not kw:
        raise HTTPException(
            403,
            "SMDA auth not available. Set SMDA_API_KEY in .env, "
            "or run 'az login' to authenticate.",
        )
    if not kw.get("access_token"):
        raise HTTPException(
            403,
            "SMDA Bearer token not available. Run 'az login' to authenticate "
            "with your Equinor Entra ID.",
        )
    return kw


@router.post("/api/strat/import/smda")
async def import_smda_api(
    request: Request,
    column: str = Form(...),
    smda_url: str = Form("https://api.gateway.equinor.com"),
    partition: str = Form("data"),
):
    """Fetch a strat column from SMDA API and convert to OSDU WPC bundle."""
    if _StratColumn is None:
        raise HTTPException(500, "stratcolumnhandler not available")

    auth_kw = await _smda_auth(request)

    try:
        col = _StratColumn.from_smda_api(
            column,
            base_url=smda_url,
            **auth_kw,
            verify_ssl=False,
        )
        bundle = col.to_osdu_bundle(partition=partition)
    except Exception as e:
        raise HTTPException(422, f"SMDA fetch/convert error: {e}")

    return JSONResponse({
        "source": "smda",
        "columnName": col.name,
        "rankCount": len(col.ranks),
        "unitCount": sum(len(r.units) for r in col.ranks if r.kind == "litho"),
        "bundle": bundle,
    })


@router.get("/api/strat/smda/columns.json")
async def list_smda_columns(
    request: Request,
    smda_url: str = Query("https://api.gateway.equinor.com"),
):
    """List available strat column identifiers from the SMDA API Gateway.

    Calls GET /smda/v2.0/smda-api/strat-column with pagination and returns
    a de-duplicated, sorted list of column identifiers.
    """
    auth_kw = await _smda_auth(request)

    url = f"{smda_url.rstrip('/')}/smda/v2.0/smda-api/strat-column"
    headers: dict[str, str] = {
        "Accept": "application/json",
        "Cache-Control": "no-cache",
    }
    if auth_kw.get("access_token"):
        headers["Authorization"] = f"Bearer {auth_kw['access_token']}"
    if auth_kw.get("api_key"):
        headers["Ocp-Apim-Subscription-Key"] = auth_kw["api_key"]

    all_rows: list[dict] = []
    page = 1
    items_per_page = 100

    try:
        async with httpx.AsyncClient(timeout=60, verify=False) as client:
            while True:
                params = {
                    "_page": page,
                    "_items": items_per_page,
                    "_order": "asc",
                    "_aggregation_include_buckets": "true",
                }
                r = await client.get(url, params=params, headers=headers)
                if not r.is_success:
                    body = r.text[:400].strip()
                    log.warning("SMDA strat-column list failed (%s): %s",
                                r.status_code, body or "(empty body)")
                    if r.status_code == 401:
                        detail = (
                            "SMDA API returned 401 Unauthorized. "
                            "The Bearer token may have expired or target the wrong audience. "
                            "Run 'az login' to re-authenticate."
                        )
                        if body:
                            detail += f" Gateway response: {body[:200]}"
                        raise HTTPException(401, detail)
                    raise HTTPException(r.status_code,
                                        f"SMDA API error: {body or '(empty response)'}")

                data = r.json()
                # SMDA gateway returns {"data": {"pages":N, "hits":N, "results":[...]}}
                inner = data.get("data", data) if isinstance(data, dict) else data
                if isinstance(inner, dict):
                    rows = inner.get("results", inner.get("value", []))
                    if not isinstance(rows, list):
                        rows = []
                elif isinstance(inner, list):
                    rows = inner
                else:
                    rows = []

                all_rows.extend(rows)

                # Check if there are more pages
                total = None
                if isinstance(inner, dict):
                    total = inner.get("hits", inner.get("total",
                            inner.get("totalCount", inner.get("_total"))))
                if total is not None:
                    try:
                        total = int(total)
                    except (TypeError, ValueError):
                        total = None

                if total is not None and len(all_rows) >= total:
                    break
                if len(rows) < items_per_page:
                    break  # last page
                page += 1
                if page > 50:  # safety limit
                    break
    except httpx.HTTPError as exc:
        log.warning("SMDA strat-column request error: %s", exc)
        raise HTTPException(502, f"SMDA API request failed: {exc}")

    # Extract unique column identifiers
    names = sorted(set(
        str(row.get("strat_column_identifier", "") or row.get("identifier", "")).strip()
        for row in all_rows
        if (row.get("strat_column_identifier") or row.get("identifier", "")).strip()
    ))

    log.info("SMDA strat-column list: %d columns from %d rows (%d pages)",
             len(names), len(all_rows), page)

    return JSONResponse({"columns": names, "total": len(names)})


@router.post("/api/strat/storage/put")
async def storage_put_strat_records(
    request: Request,
):
    """Directly PUT strat column records to OSDU Storage (bypassing workflow).

    Body:
    {
      "bundle": {"records": [...]},
      "partition": "data"
    }
    """
    at = _access_token(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON body")

    bundle = body.get("bundle")
    if not isinstance(bundle, dict) or "records" not in bundle:
        raise HTTPException(400, "Body must include 'bundle' with 'records' array")

    records = bundle["records"]
    base = f"https://{osdu.OSDU_BASE_URL}"
    url = f"{base}/api/storage/v2/records"
    hdr = osdu.headers(at)

    results = {"created": 0, "errors": []}
    # Upload in batches of 20
    async with httpx.AsyncClient(timeout=60) as client:
        for i in range(0, len(records), 20):
            batch = records[i:i + 20]
            try:
                r = await client.put(url, headers=hdr, json=batch)
                if r.status_code < 300:
                    resp = r.json()
                    results["created"] += resp.get("recordCount", len(batch))
                else:
                    results["errors"].append({
                        "batch": i,
                        "httpStatus": r.status_code,
                        "detail": r.text[:1000],
                    })
            except Exception as e:
                results["errors"].append({"batch": i, "error": str(e)})

    status = "ok" if not results["errors"] else "partial"
    return JSONResponse({
        "status": status,
        "totalRecords": len(records),
        **results,
    })


# =====================================================================
# RESQML CONVERSION & RDDMS INGEST
# =====================================================================

# Namespace for deterministic UUID5 generation from OSDU record IDs
_RESQML_NS = _uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def _det_uuid(seed: str) -> str:
    """Deterministic UUID5 from a seed string."""
    return str(_uuid.uuid5(_RESQML_NS, seed))


def _resqml_citation(title: str) -> dict:
    """Standard RESQML Citation block."""
    return {
        "$type": "eml20.Citation",
        "Title": title,
        "Originator": "ORES Strat Column Converter",
        "Creation": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "Format": "ORES [strat-to-rddms v1.0]",
    }


_CONTENT_TYPE_PREFIX = "application/x-resqml+xml;version=2.0;type="


def _resqml_ref(typ_short: str, uid: str, title: str) -> dict:
    """Build a RESQML DataObjectReference."""
    return {
        "$type": "eml20.DataObjectReference",
        "ContentType": f"{_CONTENT_TYPE_PREFIX}{typ_short}",
        "UUID": uid,
        "Title": title,
    }


def _osdu_column_to_resqml(model: dict) -> Dict[str, List[dict]]:
    """Convert a /api/strat/column.json model to RESQML 2.0.1 objects
    grouped by RDDMS type key.

    Returns a dict:  { "resqml20.obj_X": [obj, ...], ... }
    PUT order should be: features -> unit interpretations -> rank interpretations -> column
    """
    column = model.get("column") or {}
    col_data = (column.get("data") or {}) if isinstance(column, dict) else {}
    col_name = col_data.get("Name") or "Stratigraphic Column"
    col_id = column.get("id") or col_name

    by_type: Dict[str, List[dict]] = {
        "resqml20.obj_OrganizationFeature": [],
        "resqml20.obj_RockVolumeFeature": [],
        "resqml20.obj_StratigraphicUnitInterpretation": [],
        "resqml20.obj_StratigraphicColumnRankInterpretation": [],
        "resqml20.obj_StratigraphicColumn": [],
    }

    rank_refs: List[dict] = []

    for ri, rank in enumerate(model.get("ranks") or []):
        rank_name = rank.get("rankName") or f"Rank_{ri}"
        rank_uuid = _det_uuid(f"rank:{col_id}:{rank_name}")
        rank_feat_uuid = _det_uuid(f"rankfeat:{col_id}:{rank_name}")

        # OrganizationFeature for this rank
        by_type["resqml20.obj_OrganizationFeature"].append({
            "$type": "resqml20.obj_OrganizationFeature",
            "SchemaVersion": "2.0",
            "Uuid": rank_feat_uuid,
            "Citation": _resqml_citation(rank_name),
            "OrganizationKind": "stratigraphic",
        })

        unit_refs: List[dict] = []

        for ui, unit in enumerate(rank.get("units") or []):
            if unit.get("_synthetic"):
                continue  # skip gap-fill placeholders

            name = unit.get("name") or f"Unit_{ui}"
            unit_uuid = _det_uuid(f"unit:{col_id}:{rank_name}:{name}:{ui}")
            feat_uuid = _det_uuid(f"feat:{col_id}:{rank_name}:{name}:{ui}")

            # RockVolumeFeature (the feature this unit interprets)
            by_type["resqml20.obj_RockVolumeFeature"].append({
                "$type": "resqml20.obj_RockVolumeFeature",
                "SchemaVersion": "2.0",
                "Uuid": feat_uuid,
                "Citation": _resqml_citation(name),
            })

            # StratigraphicUnitInterpretation
            unit_obj: Dict[str, Any] = {
                "$type": "resqml20.obj_StratigraphicUnitInterpretation",
                "SchemaVersion": "2.0",
                "Uuid": unit_uuid,
                "Citation": _resqml_citation(name),
                "Domain": "depth",
                "InterpretedFeature": _resqml_ref(
                    "obj_RockVolumeFeature", feat_uuid, name,
                ),
            }

            # Store ages and colour as ExtraMetadata (RESQML extension point)
            extra: List[dict] = []
            if unit.get("topMa") is not None:
                extra.append({"Name": "OlderPossibleAge_Ma", "Value": str(unit["topMa"])})
            if unit.get("baseMa") is not None:
                extra.append({"Name": "YoungerPossibleAge_Ma", "Value": str(unit["baseMa"])})
            if unit.get("color"):
                extra.append({"Name": "Colour", "Value": unit["color"]})
            if unit.get("code"):
                extra.append({"Name": "ChronoCode", "Value": unit["code"]})
            if extra:
                unit_obj["ExtraMetadata"] = extra

            by_type["resqml20.obj_StratigraphicUnitInterpretation"].append(unit_obj)
            unit_refs.append(_resqml_ref(
                "obj_StratigraphicUnitInterpretation", unit_uuid, name,
            ))

        # StratigraphicColumnRankInterpretation
        rank_obj: Dict[str, Any] = {
            "$type": "resqml20.obj_StratigraphicColumnRankInterpretation",
            "SchemaVersion": "2.0",
            "Uuid": rank_uuid,
            "Citation": _resqml_citation(rank_name),
            "Domain": "depth",
            "OrderingCriteria": "olderToYounger",
            "RankInStratigraphicColumn": ri,
            "InterpretedFeature": _resqml_ref(
                "obj_OrganizationFeature", rank_feat_uuid, rank_name,
            ),
            "StratigraphicUnits": unit_refs,
        }
        by_type["resqml20.obj_StratigraphicColumnRankInterpretation"].append(rank_obj)
        rank_refs.append(_resqml_ref(
            "obj_StratigraphicColumnRankInterpretation", rank_uuid, rank_name,
        ))

    # StratigraphicColumn
    col_uuid = _det_uuid(f"col:{col_id}")
    by_type["resqml20.obj_StratigraphicColumn"].append({
        "$type": "resqml20.obj_StratigraphicColumn",
        "SchemaVersion": "2.0",
        "Uuid": col_uuid,
        "Citation": _resqml_citation(col_name),
        "Ranks": rank_refs,
    })

    # Remove empty type buckets
    return {k: v for k, v in by_type.items() if v}


def _stratcol_to_model(col) -> dict:
    """Convert a StratColumn object (from stratcolumnhandler) into the model
    format expected by _osdu_column_to_resqml().

    This lets us reuse the same RESQML converter for SMDA-sourced columns
    that haven't been ingested to OSDU yet.
    """
    ranks_model = []
    for ri, r in enumerate(col.ranks):
        units_model = []
        if r.kind == "litho":
            for u in r.units:
                units_model.append({
                    "name": u.name,
                    "topMa": u.top_age_ma,
                    "baseMa": u.base_age_ma,
                    "color": u.color_html,
                    "code": None,
                })
        ranks_model.append({
            "rankName": r.name,
            "isChrono": r.kind == "chrono",
            "unitCount": len(units_model),
            "units": units_model,
        })
    return {
        "column": {"id": col.name, "data": {"Name": col.name}},
        "ranks": ranks_model,
    }


async def _push_resqml_to_rddms(
    at: str,
    resqml_by_type: Dict[str, List[dict]],
    dataspace: str,
    create_ds: bool,
    column_name: str,
) -> dict:
    """Shared helper: optionally create dataspace, PUT RESQML objects via transaction, return result dict.

    Uses the RDDMS v2 transactional write flow:
    1. POST /dataspaces/{ds}/transactions → txId
    2. PUT  /dataspaces/{ds}/resources?transactionId={txId}  (body = all objects)
    3. PUT  /dataspaces/{ds}/transactions/{txId}  (commit)
    """
    total_objects = sum(len(v) for v in resqml_by_type.values())

    if total_objects == 0:
        raise HTTPException(422, "No RESQML objects generated - column may be empty")

    # Optionally create the dataspace (check existence first to avoid
    # 401 errors when the user has write-access but not PutDataspaces admin).
    if create_ds:
        ds_exists = False
        try:
            existing = await osdu.list_dataspaces(at)
            ds_exists = any(
                d.get("Path") == dataspace or d.get("DataspaceId") == dataspace
                for d in existing
            )
        except Exception as e:
            log.warning("[RDDMS] Could not list dataspaces: %s", e)

        if ds_exists:
            log.info("[RDDMS] Dataspace %s already exists — skipping creation", dataspace)
        else:
            log.info("[RDDMS] Creating dataspace %s", dataspace)
            try:
                await osdu.create_dataspace(
                    at, dataspace,
                    legal_tag=osdu.DEFAULT_LEGAL_TAG,
                    owners=osdu.DEFAULT_OWNERS,
                    viewers=osdu.DEFAULT_VIEWERS,
                    countries=osdu.DEFAULT_COUNTRIES,
                )
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (400, 409):
                    log.info("[RDDMS] Dataspace %s already exists (%s)", dataspace, e.response.status_code)
                elif e.response.status_code in (401, 403):
                    log.warning(
                        "[RDDMS] No PutDataspaces permission (%s) — "
                        "continuing (dataspace may already exist)",
                        e.response.status_code,
                    )
                else:
                    raise HTTPException(
                        502,
                        f"Dataspace creation failed: {e.response.status_code} "
                        f"{e.response.text[:500]}",
                    )

    # Flatten all objects into a single list (RDDMS accepts mixed types)
    put_order = [
        "resqml20.obj_RockVolumeFeature",
        "resqml20.obj_OrganizationFeature",
        "resqml20.obj_StratigraphicUnitInterpretation",
        "resqml20.obj_StratigraphicColumnRankInterpretation",
        "resqml20.obj_StratigraphicColumn",
    ]
    all_objects: List[dict] = []
    type_counts: Dict[str, int] = {}
    for typ in put_order:
        objects = resqml_by_type.get(typ, [])
        if objects:
            all_objects.extend(objects)
            type_counts[typ] = len(objects)

    errors: List[dict] = []
    tx_id: Optional[str] = None

    try:
        # 1) Begin transaction
        log.info("[RDDMS] Beginning transaction on %s", dataspace)
        tx_id = await osdu.begin_transaction(at, dataspace)
        log.info("[RDDMS] Transaction started: %s", tx_id)

        # 2) PUT all objects in one call within the transaction
        log.info("[RDDMS] PUT %d objects into %s (tx=%s)", len(all_objects), dataspace, tx_id)
        resp = await osdu.put_resources(at, dataspace, all_objects, tx_id)
        log.info("[RDDMS] PUT resources succeeded: %s", resp)

        # 3) Commit the transaction
        await osdu.commit_transaction(at, dataspace, tx_id)
        log.info("[RDDMS] Transaction %s committed", tx_id)

    except httpx.HTTPStatusError as e:
        err = {
            "httpStatus": e.response.status_code,
            "detail": e.response.text[:1000],
        }
        errors.append(err)
        log.error("[RDDMS] Transaction write failed: %s", err)
        # Attempt rollback
        if tx_id:
            try:
                await osdu.cancel_transaction(at, dataspace, tx_id)
                log.info("[RDDMS] Transaction %s rolled back", tx_id)
            except Exception:
                log.warning("[RDDMS] Rollback failed for tx %s", tx_id)
    except Exception as e:
        errors.append({"error": str(e)})
        log.error("[RDDMS] Transaction write exception: %s", e)
        if tx_id:
            try:
                await osdu.cancel_transaction(at, dataspace, tx_id)
            except Exception:
                pass

    pushed_count = total_objects if not errors else 0
    failed_count = 0 if not errors else total_objects
    status = "ok" if not errors else "error"

    return {
        "status": status,
        "dataspace": dataspace,
        "columnName": column_name,
        "totalObjects": total_objects,
        "totalPushed": pushed_count,
        "totalFailed": failed_count,
        "types": type_counts if not errors else {},
        "errors": errors,
    }


async def _fetch_column_model(request: Request, column_id: str) -> dict:
    """Fetch a strat column model (reuses the same logic as column.json)."""
    col = await _osdu_get_record(request, column_id)
    if not col:
        raise HTTPException(404, "Column not found")

    dcol = _get_data(col)
    rank_ids = _ids(
        dcol.get("StratigraphicColumnRankInterpretationSet")
        or dcol.get("RankInterpretationSet")
        or []
    )
    if not rank_ids:
        return {"column": col, "ranks": []}

    ranks_by_id = await _storage_fetch_many(request, rank_ids)

    unit_ids_all: List[str] = []
    chrono_ids_all: List[str] = []
    for rid in rank_ids:
        rk = ranks_by_id.get(rid) or {}
        drk = _get_data(rk)
        chrono_ids_all.extend(_ids(drk.get("ChronoStratigraphySet") or drk.get("ChronostratigraphySet")))
        unit_ids_all.extend(_ids(drk.get("StratigraphicUnitInterpretationSet")))

    units_by_id = await _storage_fetch_many(request, unit_ids_all) if unit_ids_all else {}
    for u in units_by_id.values():
        ud = _get_data(u)
        cid = _as_id(ud.get("ChronoStratigraphyID") or ud.get("ChronostratigraphyID") or "")
        if cid:
            chrono_ids_all.append(cid)

    chron_by_id = await _storage_fetch_many(request, chrono_ids_all) if chrono_ids_all else {}

    def _age_key(u):
        top = u.get("topMa")
        base = u.get("baseMa")
        if top is not None and base is not None:
            return (-top, base)
        return (float("inf"), float("inf"))

    ranks_model: List[dict] = []
    for rid in rank_ids:
        rk = ranks_by_id.get(rid)
        if not rk:
            continue
        drk = _get_data(rk)
        rank_name = drk.get("Name") or "Unspecified"
        chrono_ids = _ids(drk.get("ChronoStratigraphySet") or drk.get("ChronostratigraphySet"))
        unit_ids = _ids(drk.get("StratigraphicUnitInterpretationSet"))
        is_chrono = bool(chrono_ids) and not bool(unit_ids)

        units_model: List[dict] = []
        for cid in chrono_ids:
            crec = chron_by_id.get(cid)
            if crec:
                ff = _flat_unit_fields(None, crec)
                units_model.append({"unit": {}, "chrono": crec, **ff})
        for uid in unit_ids:
            urec = units_by_id.get(uid)
            if not urec:
                continue
            ud = _get_data(urec)
            cid_ref = _as_id(ud.get("ChronoStratigraphyID") or "")
            cobj = chron_by_id.get(cid_ref) if cid_ref else {}
            ff = _flat_unit_fields(urec, cobj)
            units_model.append({"unit": urec, "chrono": cobj, **ff})

        units_model.sort(key=_age_key)
        ranks_model.append({
            "rankName": rank_name,
            "isChrono": is_chrono,
            "rank": rk,
            "unitCount": len(units_model),
            "units": units_model,
        })

    return {"column": col, "ranks": ranks_model}


@router.post("/api/strat/ingest/rddms")
async def ingest_strat_to_rddms(request: Request):
    """Fetch a StratigraphicColumn from OSDU, convert to RESQML 2.0.1 objects,
    and PUT them into a Reservoir DDMS v2 dataspace.

    Body:
    {
      "columnId": "<OSDU StratigraphicColumn record id>",
      "dataspace": "maap/strat",
      "createDataspace": true   // optional, creates the dataspace first
    }
    """
    at = _access_token(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON body")

    column_id = (body.get("columnId") or "").strip()
    dataspace = (body.get("dataspace") or "").strip()
    create_ds = body.get("createDataspace", False)

    if not column_id:
        raise HTTPException(400, "columnId is required")
    if not dataspace:
        raise HTTPException(400, "dataspace is required")

    # 1) Fetch the column model from OSDU
    log.info("[RDDMS] Fetching column %s for RESQML conversion", column_id)
    model = await _fetch_column_model(request, column_id)

    # 2) Convert to RESQML 2.0.1 objects
    resqml_by_type = _osdu_column_to_resqml(model)
    log.info("[RDDMS] Converted to %d RESQML objects across %d types",
             sum(len(v) for v in resqml_by_type.values()), len(resqml_by_type))

    # 3) Push to RDDMS (create dataspace + PUT objects)
    col_name = ((model.get("column") or {}).get("data") or {}).get("Name", "")
    result = await _push_resqml_to_rddms(at, resqml_by_type, dataspace, create_ds, col_name)
    return JSONResponse(result)


@router.post("/api/strat/smda/push-rddms")
async def smda_push_to_rddms(request: Request):
    """Fetch a strat column from SMDA, convert to RESQML 2.0.1, and push
    directly to a Reservoir DDMS v2 dataspace.

    Body:
    {
      "column": "NCS Lithostratigraphy",
      "smdaUrl": "https://api.gateway.equinor.com",   // optional
      "dataspace": "maap/strat",
      "createDataspace": true   // optional
    }
    """
    if _StratColumn is None:
        raise HTTPException(500, "stratcolumnhandler not available")

    at = _access_token(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON body")

    column_name = (body.get("column") or "").strip()
    smda_url = (body.get("smdaUrl") or "https://api.gateway.equinor.com").strip()
    dataspace = (body.get("dataspace") or "").strip()
    create_ds = body.get("createDataspace", False)

    if not column_name:
        raise HTTPException(400, "column is required")
    if not dataspace:
        raise HTTPException(400, "dataspace is required")

    # 1) Get SMDA auth and fetch column
    auth_kw = await _smda_auth(request)

    log.info("[SMDA→RDDMS] Fetching column '%s' from SMDA", column_name)
    try:
        col = _StratColumn.from_smda_api(
            column_name,
            base_url=smda_url,
            **auth_kw,
            verify_ssl=False,
        )
    except Exception as e:
        raise HTTPException(422, f"SMDA fetch error: {e}")

    # 2) Convert StratColumn → model → RESQML
    model = _stratcol_to_model(col)
    resqml_by_type = _osdu_column_to_resqml(model)
    log.info("[SMDA→RDDMS] Converted '%s' to %d RESQML objects",
             col.name, sum(len(v) for v in resqml_by_type.values()))

    # 3) Push to RDDMS
    result = await _push_resqml_to_rddms(at, resqml_by_type, dataspace, create_ds, col.name)
    return JSONResponse(result)


@router.get("/api/strat/dataspaces.json")
async def list_strat_dataspaces(request: Request):
    """List available RDDMS dataspaces (for the UI picker)."""
    at = _access_token(request)
    try:
        ds = await osdu.list_dataspaces(at)
        log.info("[RDDMS] Raw dataspaces response type=%s len=%s",
                 type(ds).__name__, len(ds) if isinstance(ds, (list, dict)) else "n/a")
        items = []
        if isinstance(ds, list):
            for d in ds:
                if isinstance(d, dict):
                    # Try multiple possible key names
                    path = (d.get("Path") or d.get("path")
                            or d.get("DataspaceId") or d.get("dataspaceId")
                            or d.get("uri") or d.get("name") or "")
                    if path:
                        items.append({"path": path, "label": path})
                elif isinstance(d, str):
                    items.append({"path": d, "label": d})
            if not items and ds:
                # Log first element to understand the format
                log.warning("[RDDMS] Could not parse dataspace entries. First: %s",
                            str(ds[0])[:500])
        elif isinstance(ds, dict):
            # Maybe the response is wrapped
            for key in ("value", "dataspaces", "items", "data"):
                if key in ds and isinstance(ds[key], list):
                    for d in ds[key]:
                        path = (d.get("Path") or d.get("path") or str(d)) if isinstance(d, dict) else str(d)
                        if path:
                            items.append({"path": path, "label": path})
                    break
        return JSONResponse({"dataspaces": items})
    except Exception as e:
        log.warning("[RDDMS] List dataspaces failed: %s", e)
        return JSONResponse({"dataspaces": [], "error": str(e)})


# =====================================================================
# RDDMS READ-BACK  (JSON)
# =====================================================================

@router.get("/api/strat/rddms/resources.json")
async def rddms_list_resources(
    request: Request,
    dataspace: str = Query(..., description="Dataspace path, e.g. maap/strat"),
):
    """List all resource types and their objects in an RDDMS dataspace."""
    at = _access_token(request)
    ds_enc = urllib.parse.quote(dataspace.strip(), safe="")
    try:
        types = await osdu.list_types(at, ds_enc)
    except Exception as e:
        raise HTTPException(502, f"list_types failed: {e}")

    result: List[dict] = []
    for t in (types if isinstance(types, list) else []):
        rtype = t.get("name", "")
        if not rtype:
            continue
        try:
            resources = await osdu.list_resources(at, ds_enc, rtype)
            for res in resources:
                result.append({
                    "type": rtype,
                    "uri": res.get("uri", ""),
                    "name": res.get("name", "(unnamed)"),
                })
        except Exception:
            pass

    return JSONResponse({"dataspace": dataspace, "resources": result, "count": len(result)})


@router.get("/api/strat/rddms/resource.json")
async def rddms_get_resource(
    request: Request,
    dataspace: str = Query(..., description="Dataspace path, e.g. maap/strat"),
    type: str = Query(..., description="RESQML type, e.g. resqml20.obj_StratigraphicColumn"),
    uuid: str = Query(..., description="Object UUID"),
):
    """Fetch a single RESQML object from RDDMS as JSON.

    Uses ``GET /dataspaces/{ds}/resources/{type}/{uuid}?$format=json``.
    """
    at = _access_token(request)
    ds_enc = urllib.parse.quote(dataspace.strip(), safe="")
    try:
        obj = await osdu.get_resource(at, ds_enc, type.strip(), uuid.strip())
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code,
                            f"RDDMS GET failed: {e.response.text[:500]}")
    return JSONResponse(obj)


@router.post("/api/strat/rddms/verify")
async def rddms_verify_column(request: Request):
    """Push a strat column to RDDMS then immediately fetch-back and verify.

    Body:
    {
      "columnId": "<OSDU record id>",   // option A: from OSDU
      "column": "<SMDA column name>",   // option B: from SMDA
      "smdaUrl": "...",                 // optional for option B
      "dataspace": "maap/strat",
      "createDataspace": false
    }

    Returns the push result augmented with a ``verification`` object
    containing per-object match status.
    """
    at = _access_token(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON body")

    dataspace = (body.get("dataspace") or "").strip()
    if not dataspace:
        raise HTTPException(400, "dataspace is required")

    create_ds = body.get("createDataspace", False)
    column_id = (body.get("columnId") or "").strip()
    column_name_smda = (body.get("column") or "").strip()

    # ── Build RESQML objects (same as the push endpoints) ──
    if column_id:
        model = await _fetch_column_model(request, column_id)
        col_name = ((model.get("column") or {}).get("data") or {}).get("Name", "")
        resqml_by_type = _osdu_column_to_resqml(model)
    elif column_name_smda:
        if _StratColumn is None:
            raise HTTPException(500, "stratcolumnhandler not available")
        smda_url = (body.get("smdaUrl") or "https://api.gateway.equinor.com").strip()
        auth_kw = await _smda_auth(request)
        try:
            col = _StratColumn.from_smda_api(column_name_smda, base_url=smda_url,
                                             **auth_kw, verify_ssl=False)
        except Exception as e:
            raise HTTPException(422, f"SMDA fetch error: {e}")
        model = _stratcol_to_model(col)
        col_name = col.name
        resqml_by_type = _osdu_column_to_resqml(model)
    else:
        raise HTTPException(400, "columnId or column (SMDA name) is required")

    # ── Push ──
    push_result = await _push_resqml_to_rddms(
        at, resqml_by_type, dataspace, create_ds, col_name)

    if push_result.get("status") != "ok":
        push_result["verification"] = {"status": "skipped", "reason": "push failed"}
        return JSONResponse(push_result)

    # ── Fetch-back & verify ──
    ds_enc = urllib.parse.quote(dataspace, safe="")
    verification: List[dict] = []
    all_ok = True

    for typ in [
        "resqml20.obj_RockVolumeFeature",
        "resqml20.obj_OrganizationFeature",
        "resqml20.obj_StratigraphicUnitInterpretation",
        "resqml20.obj_StratigraphicColumnRankInterpretation",
        "resqml20.obj_StratigraphicColumn",
    ]:
        for sent_obj in resqml_by_type.get(typ, []):
            uid = sent_obj["Uuid"]
            title = (sent_obj.get("Citation") or {}).get("Title", "?")
            entry: Dict[str, Any] = {"type": typ, "uuid": uid, "title": title}
            try:
                fetched = await osdu.get_resource(at, ds_enc, typ, uid)
                if isinstance(fetched, list):
                    fetched = fetched[0] if fetched else {}
                # Compare key fields
                mismatches: List[str] = []
                for field in ("$type", "Uuid", "SchemaVersion"):
                    if str(sent_obj.get(field)) != str(fetched.get(field)):
                        mismatches.append(f"{field}: sent={sent_obj.get(field)!r} got={fetched.get(field)!r}")
                s_cit = sent_obj.get("Citation") or {}
                r_cit = fetched.get("Citation") or {}
                for cf in ("Title", "Originator", "Format"):
                    if s_cit.get(cf) != r_cit.get(cf):
                        mismatches.append(f"Citation.{cf}: sent={s_cit.get(cf)!r} got={r_cit.get(cf)!r}")
                # ExtraMetadata count
                s_em = sent_obj.get("ExtraMetadata") or []
                r_em = fetched.get("ExtraMetadata") or []
                if len(s_em) != len(r_em):
                    mismatches.append(f"ExtraMetadata count: sent={len(s_em)} got={len(r_em)}")

                entry["match"] = len(mismatches) == 0
                if mismatches:
                    entry["mismatches"] = mismatches
                    all_ok = False
            except Exception as e:
                entry["match"] = False
                entry["error"] = str(e)
                all_ok = False
            verification.append(entry)

    push_result["verification"] = {
        "status": "ok" if all_ok else "mismatches",
        "checked": len(verification),
        "passed": sum(1 for v in verification if v.get("match")),
        "failed": sum(1 for v in verification if not v.get("match")),
        "objects": verification,
    }
    return JSONResponse(push_result)