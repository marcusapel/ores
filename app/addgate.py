"""
Add DG page - Create and ingest a new BusinessDecision for an existing Reservoir.

Provides:
  GET  /add-dg               → render the addgate.html template
  GET  /add-dg/reservoirs    → JSON: list of Reservoir master-data records
  GET  /add-dg/wpc-search    → JSON: search for WPC records to link
  POST /add-dg/create        → JSON: build BD record, PUT to Storage API
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from . import osdu

log = logging.getLogger("rddms-admin.addgate")

router = APIRouter()
templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "templates"),
)


def _access_token(request: Request) -> str:
    from .common import access_token as _at
    return _at(request)


# ──────────────────────────────────────────────────────────────────────────────
# Page
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/add-dg", response_class=HTMLResponse, summary="Add DG: create new BusinessDecision")
async def add_dg_page(request: Request):
    """Render the Add DG form page."""
    reservoirs, decision_levels = await asyncio.gather(
        _search_reservoirs(request),
        _search_decision_levels(request),
    )
    return templates.TemplateResponse(
        request, "addgate.html",
        {"reservoirs": reservoirs, "decision_levels": decision_levels},
    )


# ──────────────────────────────────────────────────────────────────────────────
# JSON APIs
# ──────────────────────────────────────────────────────────────────────────────

# ── Decision-level reference data ──────────────────────────────────────────

_FALLBACK_LEVELS = [
    {"id": "DG1", "name": "DG1 - Identify & Assess"},
    {"id": "DG2", "name": "DG2 - Concept Select"},
    {"id": "DG3", "name": "DG3 - Define & Execute"},
    {"id": "DG4", "name": "DG4 - Operate & Improve"},
]


async def _search_decision_levels(
    request: Request,
) -> List[Dict[str, str]]:
    """Fetch reference-data--DecisionLevel records from OSDU.

    Returns a list of {"id": "<code>", "name": "<display label>", "record_id": "<full OSDU id>"}.
    Falls back to a hard-coded list when the search returns nothing.
    """
    at = _access_token(request)
    search_url = f"https://{osdu.OSDU_BASE_URL}/api/search/v2/query"
    hdr = osdu.headers(at)

    payload = {
        "kind": "osdu:wks:reference-data--DecisionLevel:*",
        "query": "*",
        "limit": 50,
        "returnedFields": ["id", "data.Code", "data.Name", "data.Description"],
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(search_url, json=payload, headers=hdr)
            if not r.is_success:
                log.warning("DecisionLevel search failed (%s): %s", r.status_code, r.text[:300])
                return _FALLBACK_LEVELS
            results = r.json().get("results", [])
    except Exception as exc:
        log.warning("DecisionLevel search error: %s", exc)
        return _FALLBACK_LEVELS

    if not results:
        return _FALLBACK_LEVELS

    out: List[Dict[str, str]] = []
    for rec in results:
        data = rec.get("data", {})
        code = data.get("Code", "") or data.get("Name", "")
        name = data.get("Name", "") or code
        desc = data.get("Description", "")
        display = f"{code} - {desc}" if desc and desc != name else name
        out.append({"id": code, "name": display, "record_id": rec.get("id", "")})

    out.sort(key=lambda x: x["id"])
    return out


async def _search_reservoirs(
    request: Request, query: str = "*", limit: int = 50,
) -> List[Dict[str, str]]:
    """Shared helper via common.search_reservoirs (parallel fetches, no N+1)."""
    at = _access_token(request)
    from .common import search_reservoirs
    return await search_reservoirs(at, query=query, limit=limit)


@router.get("/add-dg/reservoirs", summary="JSON: reservoir list")
async def reservoirs_json(request: Request):
    reservoirs = await _search_reservoirs(request)
    return JSONResponse(reservoirs)


@router.get("/add-dg/wpc-search", summary="JSON: search WPCs by kind")
async def wpc_search(
    request: Request,
    kind: str = Query("", description="Kind to search for"),
    q: str = Query("*", description="Search query"),
    limit: int = Query(20),
):
    """Search for WPC records of a given kind - used to populate link dropdowns."""
    at = _access_token(request)
    search_url = f"https://{osdu.OSDU_BASE_URL}/api/search/v2/query"
    storage_url = f"https://{osdu.OSDU_BASE_URL}/api/storage/v2/records"
    hdr = osdu.headers(at)

    if not kind:
        return JSONResponse([])

    payload = {
        "kind": kind,
        "query": q,
        "limit": min(int(limit), 50),
        "returnedFields": ["id", "kind", "version", "data.Name", "data.Description"],
    }

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(search_url, json=payload, headers=hdr)
        if not r.is_success:
            return JSONResponse([])
        results = r.json().get("results", [])

    out = []
    for rec in results:
        rid = rec.get("id", "")
        data = rec.get("data", {})
        name = data.get("Name", "") or data.get("Description", "") or rid
        out.append({"id": rid, "name": name, "kind": rec.get("kind", "")})

    return JSONResponse(out)


@router.post("/add-dg/create", summary="Create and ingest a new BusinessDecision")
async def create_bd(request: Request):
    """
    Build a BusinessDecision record from form data, PUT it to Storage API.

    Expects JSON body with fields:
      reservoir_id, name, description, decision_level, approval_status,
      decision_date, decision_due_date, decision_summary,
      rev_stats_id, rev_raw_id, production_profile_id,
      geolabelset_id, activity_id, risk_ids[], params_id, dataspace_id,
      custom_records[{label, id}]
    """
    at = _access_token(request)
    body = await request.json()

    reservoir_id = body.get("reservoir_id", "").strip()
    if not reservoir_id:
        raise HTTPException(400, "reservoir_id is required")

    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(400, "name is required")

    # ID prefix from the reservoir_id (e.g. "dev")
    id_prefix = reservoir_id.split(":")[0] if ":" in reservoir_id else "dev"

    # Generate a deterministic-ish BD ID from the name
    bd_slug = name.replace(" ", "-").replace("-", "-")[:80]
    bd_id = f"{id_prefix}:master-data--BusinessDecision:{bd_slug}:1"

    decision_level = body.get("decision_level", "DG1")
    approval_status = body.get("approval_status", "Pending")
    description = body.get("description", "")
    decision_date = body.get("decision_date", "")
    decision_due_date = body.get("decision_due_date", "")
    decision_summary = body.get("decision_summary", "")
    project_name = body.get("project_name", "")

    # Optional linked record IDs
    rev_stats_id = body.get("rev_stats_id", "").strip()
    rev_raw_id = body.get("rev_raw_id", "").strip()
    production_profile_id = body.get("production_profile_id", "").strip()
    geolabelset_id = body.get("geolabelset_id", "").strip()
    activity_id = body.get("activity_id", "").strip()
    params_id = body.get("params_id", "").strip()
    dataspace_id = body.get("dataspace_id", "").strip()
    collection_id = body.get("collection_id", "").strip()
    risk_ids = [r.strip() for r in body.get("risk_ids", []) if r.strip()]
    custom_records: List[Dict[str, str]] = body.get("custom_records", [])

    # ACL and legal from OSDU defaults
    acl = {
        "owners": osdu.DEFAULT_OWNERS,
        "viewers": osdu.DEFAULT_VIEWERS,
    }
    legal = {
        "legaltags": [osdu.DEFAULT_LEGAL_TAG],
        "otherRelevantDataCountries": osdu.DEFAULT_COUNTRIES,
    }

    # Build Parameters[] array
    parameters: List[Dict[str, Any]] = []

    if rev_raw_id:
        parameters.append({
            "Title": "In-place volumes raw (per realisation)",
            "Selection": "Raw per-realisation volumes",
            "ParameterKindID": f"{id_prefix}:reference-data--ParameterKind:DataObject:1",
            "ParameterRoleID": f"{id_prefix}:reference-data--ParameterRole:Input:1",
            "DataObjectParameter": rev_raw_id,
            "Keys": [{"ParameterKey": "artifact", "StringParameterKey": "InPlaceVol-raw"}],
        })

    if rev_stats_id:
        parameters.append({
            "Title": "In-place volume statistics (P10/P50/P90)",
            "Selection": "Aggregated statistics for the assessment",
            "ParameterKindID": f"{id_prefix}:reference-data--ParameterKind:DataObject:1",
            "ParameterRoleID": f"{id_prefix}:reference-data--ParameterRole:Input:1",
            "DataObjectParameter": rev_stats_id,
            "Keys": [{"ParameterKey": "artifact", "StringParameterKey": "InPlaceVol-stats"}],
        })

    if production_profile_id:
        parameters.append({
            "Title": "Production profile",
            "Selection": "Production forecast / profile linked to the decision",
            "ParameterKindID": f"{id_prefix}:reference-data--ParameterKind:DataObject:1",
            "ParameterRoleID": f"{id_prefix}:reference-data--ParameterRole:Input:1",
            "DataObjectParameter": production_profile_id,
            "Keys": [{"ParameterKey": "artifact", "StringParameterKey": "ProductionProfile"}],
        })

    if geolabelset_id:
        parameters.append({
            "Title": "GeoLabelSet",
            "Selection": "Headline KPI values per segment",
            "ParameterKindID": f"{id_prefix}:reference-data--ParameterKind:DataObject:1",
            "ParameterRoleID": f"{id_prefix}:reference-data--ParameterRole:Input:1",
            "DataObjectParameter": geolabelset_id,
            "Keys": [{"ParameterKey": "artifact", "StringParameterKey": "GeoLabelSet"}],
        })

    if params_id:
        parameters.append({
            "Title": "Input parameters",
            "Selection": "Per-segment input parameters",
            "ParameterKindID": f"{id_prefix}:reference-data--ParameterKind:DataObject:1",
            "ParameterRoleID": f"{id_prefix}:reference-data--ParameterRole:Input:1",
            "DataObjectParameter": params_id,
            "Keys": [{"ParameterKey": "artifact", "StringParameterKey": "ColumnBasedTable-params"}],
        })

    if dataspace_id:
        parameters.append({
            "Title": "GeoModelDataspace",
            "Selection": "RDDMS ETP dataspace with geomodel EPC files",
            "ParameterKindID": f"{id_prefix}:reference-data--ParameterKind:DataObject:1",
            "ParameterRoleID": f"{id_prefix}:reference-data--ParameterRole:InputReference:1",
            "DataObjectParameter": dataspace_id,
            "Keys": [{"ParameterKey": "artifact", "StringParameterKey": "ETPDataspace"}],
        })

    if collection_id:
        parameters.append({
            "Title": "PersistedCollection",
            "Selection": "Persisted collection of related records",
            "ParameterKindID": f"{id_prefix}:reference-data--ParameterKind:DataObject:1",
            "ParameterRoleID": f"{id_prefix}:reference-data--ParameterRole:InputReference:1",
            "DataObjectParameter": collection_id,
            "Keys": [{"ParameterKey": "artifact", "StringParameterKey": "PersistedCollection"}],
        })

    # User-defined arbitrary records
    for crec in custom_records:
        clabel = crec.get("label", "").strip()
        cid = crec.get("id", "").strip()
        if clabel and cid:
            parameters.append({
                "Title": clabel,
                "Selection": f"User-defined record: {clabel}",
                "ParameterKindID": f"{id_prefix}:reference-data--ParameterKind:DataObject:1",
                "ParameterRoleID": f"{id_prefix}:reference-data--ParameterRole:Input:1",
                "DataObjectParameter": cid,
                "Keys": [{"ParameterKey": "artifact", "StringParameterKey": clabel.replace(' ', '-')}],
            })

    # Reservoir is always added as a parameter
    parameters.append({
        "Title": "Reservoir scope",
        "Selection": "Master-data context for the decision",
        "ParameterKindID": f"{id_prefix}:reference-data--ParameterKind:DataObject:1",
        "ParameterRoleID": f"{id_prefix}:reference-data--ParameterRole:InputReference:1",
        "DataObjectParameter": reservoir_id,
    })

    # Build PriorActivityIDs
    prior_activity_ids: List[str] = []
    if activity_id:
        prior_activity_ids.append(activity_id)
    elif rev_raw_id or rev_stats_id or params_id:
        prior_activity_ids = [x for x in [rev_raw_id, rev_stats_id, params_id] if x]

    # Build the record
    bd_data: Dict[str, Any] = {
        "Name": name,
        "Description": description,
        "ProjectName": project_name,
        "DecisionLevelID": f"{id_prefix}:reference-data--DecisionLevel:{decision_level}:1",
        "ApprovalStatusID": f"{id_prefix}:reference-data--DecisionApprovalStatus:{approval_status}:1",
        "RiskIDs": risk_ids,
        "PriorActivityIDs": prior_activity_ids,
        "Parameters": parameters,
        "ancestry": {
            "parents": [activity_id] if activity_id else [],
            "children": [],
        },
    }

    if decision_date:
        bd_data["DecisionDate"] = decision_date
    if decision_due_date:
        bd_data["DecisionDueDate"] = decision_due_date
    if decision_summary:
        bd_data["DecisionSummary"] = decision_summary

    bd_record = {
        "id": bd_id,
        "kind": "osdu:wks:master-data--BusinessDecision:1.0.0",
        "acl": acl,
        "legal": legal,
        "data": bd_data,
    }

    # PUT to Storage API
    storage_url = f"https://{osdu.OSDU_BASE_URL}/api/storage/v2/records"
    hdr = osdu.headers(at)

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.put(storage_url, json=[bd_record], headers=hdr)
            status = r.status_code
            resp_body = r.text[:2000]
    except Exception as e:
        log.error("Storage API PUT failed: %s", e)
        return JSONResponse(
            {"ok": False, "error": str(e)},
            status_code=502,
        )

    if status in (200, 201):
        log.info("BD created: %s (status=%d)", bd_id, status)
        return JSONResponse({
            "ok": True,
            "bd_id": bd_id,
            "status": status,
            "parameters_count": len(parameters),
            "risk_count": len(risk_ids),
            "response": resp_body,
        })
    else:
        log.warning("BD ingest failed (%d): %s", status, resp_body)
        return JSONResponse(
            {"ok": False, "bd_id": bd_id, "status": status, "response": resp_body},
            status_code=status,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Create Collaboration Project
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/add-dg/create-cp", summary="Create and ingest a new CollaborationProject")
async def create_cp(request: Request):
    """
    Build a CollaborationProject record from form data, PUT it to Storage API.

    Schema: osdu:wks:master-data--CollaborationProject:1.0.0
    Inherits: AbstractProject, AbstractProjectActivity (Parameters[]).

    Expects JSON body with fields:
      name, description, purpose, lifecycle_status, begin_date, end_date,
      namespace, parent_bd_id, dataspace_id, reservoir_id, collection_id,
      activity_id, contributor_owners, contributor_viewers,
      custom_records[{label, id}]
    """
    at = _access_token(request)
    body = await request.json()

    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(400, "name is required")

    description = body.get("description", "").strip()
    purpose = body.get("purpose", "").strip()
    lifecycle_status = body.get("lifecycle_status", "Open").strip()
    begin_date = body.get("begin_date", "").strip()
    end_date = body.get("end_date", "").strip()
    namespace = body.get("namespace", "").strip()
    parent_bd_id = body.get("parent_bd_id", "").strip()
    dataspace_id = body.get("dataspace_id", "").strip()
    reservoir_id = body.get("reservoir_id", "").strip()
    collection_id = body.get("collection_id", "").strip()
    activity_id = body.get("activity_id", "").strip()
    contributor_owners = body.get("contributor_owners", "").strip()
    contributor_viewers = body.get("contributor_viewers", "").strip()
    custom_records: List[Dict[str, str]] = body.get("custom_records", [])

    # Derive ID prefix
    id_prefix = "dev"
    for ref in [parent_bd_id, reservoir_id, dataspace_id, collection_id]:
        if ref and ":" in ref:
            id_prefix = ref.split(":")[0]
            break

    # Generate CP ID
    cp_slug = name.replace(" ", "-")[:80]
    cp_uuid = str(uuid.uuid4())[:8]
    cp_id = f"{id_prefix}:master-data--CollaborationProject:{cp_slug}-{cp_uuid}:1"

    # Auto-generate namespace if empty
    if not namespace:
        namespace = f"project-{uuid.uuid4()}"

    # ACL and legal from OSDU defaults
    acl = {
        "owners": osdu.DEFAULT_OWNERS,
        "viewers": osdu.DEFAULT_VIEWERS,
    }
    legal = {
        "legaltags": [osdu.DEFAULT_LEGAL_TAG],
        "otherRelevantDataCountries": osdu.DEFAULT_COUNTRIES,
    }

    # Build Parameters[] (same pattern as BusinessDecision)
    parameters: List[Dict[str, Any]] = []

    if dataspace_id:
        parameters.append({
            "Title": "GeoModelDataspace",
            "Selection": "RDDMS ETP dataspace with geomodel data",
            "ParameterKindID": f"{id_prefix}:reference-data--ParameterKind:DataObject:",
            "ParameterRoleID": f"{id_prefix}:reference-data--ParameterRole:InputReference:",
            "DataObjectParameter": dataspace_id,
            "Keys": [{"ParameterKey": "artifact", "StringParameterKey": "ETPDataspace"}],
        })

    if reservoir_id:
        parameters.append({
            "Title": "Reservoir scope",
            "Selection": "Master-data context for the project",
            "ParameterKindID": f"{id_prefix}:reference-data--ParameterKind:DataObject:",
            "ParameterRoleID": f"{id_prefix}:reference-data--ParameterRole:InputReference:",
            "DataObjectParameter": reservoir_id,
        })

    if collection_id:
        parameters.append({
            "Title": "PersistedCollection",
            "Selection": "Persisted collection of related records",
            "ParameterKindID": f"{id_prefix}:reference-data--ParameterKind:DataObject:",
            "ParameterRoleID": f"{id_prefix}:reference-data--ParameterRole:InputReference:",
            "DataObjectParameter": collection_id,
            "Keys": [{"ParameterKey": "artifact", "StringParameterKey": "PersistedCollection"}],
        })

    if activity_id:
        parameters.append({
            "Title": "Activity",
            "Selection": "Related workflow activity",
            "ParameterKindID": f"{id_prefix}:reference-data--ParameterKind:DataObject:",
            "ParameterRoleID": f"{id_prefix}:reference-data--ParameterRole:Input:",
            "DataObjectParameter": activity_id,
        })

    # User-defined arbitrary records
    for crec in custom_records:
        clabel = crec.get("label", "").strip()
        cid = crec.get("id", "").strip()
        if clabel and cid:
            parameters.append({
                "Title": clabel,
                "Selection": f"User-defined record: {clabel}",
                "ParameterKindID": f"{id_prefix}:reference-data--ParameterKind:DataObject:",
                "ParameterRoleID": f"{id_prefix}:reference-data--ParameterRole:Input:",
                "DataObjectParameter": cid,
                "Keys": [{"ParameterKey": "artifact", "StringParameterKey": clabel.replace(' ', '-')}],
            })

    # Build the data block
    cp_data: Dict[str, Any] = {
        "ProjectName": name,
        "Description": description,
        "Namespace": namespace,
        "LifecycleStatusID": f"{id_prefix}:reference-data--CollaborationProjectLifecycleStatus:{lifecycle_status}:",
    }

    if purpose:
        cp_data["Purpose"] = purpose
    if begin_date:
        cp_data["ProjectBeginDate"] = begin_date + "T00:00:00Z"
    if end_date:
        cp_data["ProjectEndDate"] = end_date + "T00:00:00Z"
    if parent_bd_id:
        cp_data["ParentProjectID"] = parent_bd_id

    if parameters:
        cp_data["Parameters"] = parameters

    # ProjectContributorACL (optional)
    if contributor_owners or contributor_viewers:
        owners_list = [o.strip() for o in contributor_owners.split(",") if o.strip()] if contributor_owners else osdu.DEFAULT_OWNERS
        viewers_list = [v.strip() for v in contributor_viewers.split(",") if v.strip()] if contributor_viewers else osdu.DEFAULT_VIEWERS
        cp_data["ProjectContributorACL"] = {
            "owners": owners_list,
            "viewers": viewers_list,
        }

    # TrustedCollectionID: link to collection if provided
    if collection_id:
        cp_data["TrustedCollectionID"] = collection_id

    cp_record = {
        "id": cp_id,
        "kind": "osdu:wks:master-data--CollaborationProject:1.0.0",
        "acl": acl,
        "legal": legal,
        "data": cp_data,
    }

    # PUT to Storage API
    storage_url = f"https://{osdu.OSDU_BASE_URL}/api/storage/v2/records"
    hdr = osdu.headers(at)

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.put(storage_url, json=[cp_record], headers=hdr)
            status = r.status_code
            resp_body = r.text[:2000]
    except Exception as e:
        log.error("Storage API PUT (CP) failed: %s", e)
        return JSONResponse(
            {"ok": False, "error": str(e)},
            status_code=502,
        )

    if status in (200, 201):
        log.info("CP created: %s (status=%d)", cp_id, status)
        return JSONResponse({
            "ok": True,
            "cp_id": cp_id,
            "status": status,
            "parameters_count": len(parameters),
            "response": resp_body,
        })
    else:
        log.warning("CP ingest failed (%d): %s", status, resp_body)
        return JSONResponse(
            {"ok": False, "cp_id": cp_id, "status": status, "response": resp_body},
            status_code=status,
        )
