
from __future__ import annotations
import asyncio
import json
import os
import sys
import urllib.parse
from typing import Any, Dict, List, Optional
import httpx
from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from . import osdu

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
    parts = val.strip().split(":")
    if len(parts) >= 2 and parts[-1] == "":
        return parts[-2]
    return parts[-1] if parts else val

@router.get("/strat", response_class=HTMLResponse)
async def strat_page(request: Request):
    return templates.TemplateResponse("strat.html", {"request": request})

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

    # 8) Return column model
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


@router.post("/api/strat/import/smda")
async def import_smda_api(
    request: Request,
    column: str = Form(...),
    smda_url: str = Form("https://opus.smda.equinor.com"),
    partition: str = Form("data"),
):
    """Fetch a strat column from SMDA OPUS API and convert to OSDU WPC bundle.

    Uses the caller's OSDU access token (if available) to authenticate
    against SMDA, since both use Azure AD (Equinor SSO).
    """
    if _StratColumn is None:
        raise HTTPException(500, "stratcolumnhandler not available")

    # Try to get access token for SMDA auth (same tenant)
    at = getattr(request.state, "access_token", None)

    try:
        col = _StratColumn.from_smda_api(
            column,
            base_url=smda_url,
            access_token=at,
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
    smda_url: str = Query("https://opus.smda.equinor.com"),
):
    """List available strat column identifiers from SMDA OPUS API."""
    if _StratColumn is None:
        raise HTTPException(500, "stratcolumnhandler not available")

    at = getattr(request.state, "access_token", None)
    try:
        names = _StratColumn.from_smda_api_list_columns(
            base_url=smda_url,
            access_token=at,
        )
    except Exception as e:
        raise HTTPException(502, f"SMDA API error: {e}")

    return JSONResponse({"columns": names, "total": len(names)})


@router.post("/api/strat/ingest")
async def ingest_strat_bundle(
    request: Request,
):
    """Ingest an OSDU WPC bundle (strat column records) via the OSDU Workflow Service.

    Body:
    {
      "bundle": {"records": [...]},
      "partition": "data"  // optional
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

    partition = body.get("partition") or osdu.DATA_PARTITION_ID or "data"
    base = f"https://{osdu.OSDU_BASE_URL}"

    # Build manifest envelope for the Osdu_ingest workflow
    records = bundle["records"]
    ref_data = [r for r in records if "reference-data--" in (r.get("kind") or "")]
    wpc_data = [r for r in records if "work-product-component--" in (r.get("kind") or "")]

    manifest = {
        "kind": "osdu:wks:Manifest:1.0.0",
        "ReferenceData": ref_data,
        "MasterData": [],
        "Data": {
            "Datasets": [],
            "WorkProductComponents": wpc_data,
            "WorkProduct": {},
        },
    }

    # POST to workflow service
    url = f"{base}/api/workflow/v1/workflow/Osdu_ingest/workflowRun"
    hdr = osdu.headers(at)
    payload = {
        "executionContext": {
            "Payload": {"data-partition-id": partition},
            "manifest": manifest,
        }
    }

    async with httpx.AsyncClient(timeout=60) as client:
        try:
            r = await client.post(url, headers=hdr, json=payload)
            if r.status_code >= 400:
                return JSONResponse({
                    "status": "error",
                    "httpStatus": r.status_code,
                    "detail": r.text[:2000],
                }, status_code=502)
            result = r.json()
        except Exception as e:
            raise HTTPException(502, f"Workflow ingestion failed: {e}")

    return JSONResponse({
        "status": "submitted",
        "recordCount": len(records),
        "refDataCount": len(ref_data),
        "wpcCount": len(wpc_data),
        "workflowResponse": result,
    })


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