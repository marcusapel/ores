"""
FastAPI router that stores a generated manifest in memory and immediately
POSTs it to the OSDU Workflow Service endpoint:
    /api/workflow/v1/workflow/Osdu_ingest/workflowRun

This keeps existing logic intact by adding a new handler that can be called
right after the manifest is created in the UI. It reuses the existing
access_token (obtained via refresh_token) from the session/cookies if present.

Environment variables expected (all optional, but recommended):
- OSDU_BASE_URL        e.g. https://equinordev.energy.azure.com
- DATA_PARTITION_ID    e.g. data
- APP_KEY              e.g. test-app or your app registration name

To register this router, add to your main app:
    from app.ingest_router import router as ingest_router
    app.include_router(ingest_router, prefix="/api")

No existing endpoints need to be modified; the UI can call POST /api/manifest/ingest
with the manifest JSON to trigger immediate ingestion.
"""
from __future__ import annotations

import os
import uuid
import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse
import httpx

router = APIRouter()

# Simple in-memory manifest store (last N manifests). Not for production.
_MAX_ITEMS = 100
_MANIFESTS: Dict[str, Dict[str, Any]] = {}


def _get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    return v if v is not None and v != "" else default


async def _post_workflow_run(
    *,
    base_url: str,
    partition: str,
    app_key: Optional[str],
    access_token: str,
    manifest: Dict[str, Any],
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """POST the manifest to the OSDU Workflow Service Osdu_ingest DAG.

    Builds the correct headers and body and returns parsed JSON response.
    """
    url = base_url.rstrip('/') + "/api/workflow/v1/workflow/Osdu_ingest/workflowRun"

    # Build headers – both header and Payload values are commonly used by providers.
    headers = {
        "Authorization": f"Bearer {access_token}",
        "data-partition-id": partition,
        "Content-Type": "application/json",
    }
    if app_key:
        headers["AppKey"] = app_key

    payload = {
        "executionContext": {
            "Payload": {
                "data-partition-id": partition,
            },
            "manifest": manifest,
        }
    }
    if app_key:
        payload["executionContext"]["Payload"]["AppKey"] = app_key
    if run_id:
        payload["runId"] = run_id

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=60.0)) as client:
        r = await client.post(url, headers=headers, content=json.dumps(payload))
        if r.status_code >= 400:
            detail = {
                "status": r.status_code,
                "reason": r.reason_phrase,
                "text": r.text[:2000],  # cap for safety
                "url": url,
            }
            raise HTTPException(status_code=502, detail={"message": "Workflow run failed", **detail})
        try:
            return r.json()
        except Exception:
            return {"status_code": r.status_code, "text": r.text}


def _find_access_token(request: Request) -> Optional[str]:
    """Try to retrieve the access_token from common places used in the app.

    We DO NOT mint new tokens here to keep the existing auth workflow intact.
    """
    # 1) Starlette session (requires SessionMiddleware configured in main.py)
    access_token = None
    try:
        session = getattr(request, 'session', None)
        if isinstance(session, dict):
            access_token = session.get('access_token') or session.get('token')
    except Exception:
        access_token = None

    # 2) Request state (some apps stash tokens here)
    if not access_token:
        try:
            access_token = getattr(request.state, 'access_token', None)
        except Exception:
            access_token = None

    # 3) Authorization header forwarded from the browser (if UI passes it)
    if not access_token:
        auth = request.headers.get('Authorization')
        if auth and auth.lower().startswith('bearer '):
            access_token = auth.split(' ', 1)[1]

    # 4) Cookie (if app sets a cookie named 'access_token')
    if not access_token:
        access_token = request.cookies.get('access_token')

    return access_token


@router.post("/manifest/ingest")
async def ingest_manifest(
    request: Request,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """Accepts a manifest (JSON) and immediately triggers Osdu_ingest workflow.

    Body schema:
    {
      "manifest": { ... },            # required manifest JSON
      "runId": "optional-guid",      # optional
      "partition": "data",           # optional override of DATA_PARTITION_ID
      "appKey": "my-app"             # optional override of APP_KEY
    }
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    manifest = body.get("manifest")
    if not isinstance(manifest, dict):
        raise HTTPException(status_code=400, detail="Body must include a 'manifest' object")

    # Store manifest in memory (cap to last N items)
    manifest_id = str(uuid.uuid4())
    _MANIFESTS[manifest_id] = manifest
    if len(_MANIFESTS) > _MAX_ITEMS:
        # remove oldest
        try:
            oldest_key = next(iter(_MANIFESTS.keys()))
            _MANIFESTS.pop(oldest_key, None)
        except StopIteration:
            pass

    # Resolve configuration
    base_url = _get_env("OSDU_BASE_URL")
    if not base_url:
        raise HTTPException(status_code=500, detail="OSDU_BASE_URL is not configured in the environment")
    # Normalise: osdu.py stores bare hostname; ensure we have a scheme for URL construction.
    if not base_url.startswith("http"):
        base_url = f"https://{base_url}"
    partition = body.get("partition") or _get_env("DATA_PARTITION_ID", "data")
    app_key = body.get("appKey") or _get_env("APP_KEY")
    run_id = body.get("runId") or str(uuid.uuid4())

    # Fetch access_token from existing session/auth flow
    access_token = _find_access_token(request)
    if not access_token:
        raise HTTPException(status_code=401, detail="access_token not found in session/headers/cookies")

    # Fire workflow call immediately
    try:
        workflow_response = await _post_workflow_run(
            base_url=base_url,
            partition=partition,
            app_key=app_key,
            access_token=access_token,
            manifest=manifest,
            run_id=run_id,
        )
    except HTTPException:
        raise
    except Exception as ex:
        raise HTTPException(status_code=502, detail={"message": "Failed to call Workflow Service", "error": str(ex)})

    return JSONResponse(
        {
            "status": "submitted",
            "manifestId": manifest_id,
            "runId": run_id,
            "workflowResponse": workflow_response,
        }
    )


@router.get("/manifest/last")
async def get_last_manifest() -> JSONResponse:
    """Returns the latest stored manifest (debug/helper)."""
    if not _MANIFESTS:
        raise HTTPException(status_code=404, detail="No manifests stored yet")
    # return the most recently inserted manifest
    last_key = list(_MANIFESTS.keys())[-1]
    return JSONResponse({"manifestId": last_key, "manifest": _MANIFESTS[last_key]})


# ======================================================================
# RDDMS → OSDU indexing endpoints
#
# These endpoints combine two steps:
#   1. Call RDDMS manifests/build to generate OSDU records from RESQML
#   2. Submit the resulting manifest to OSDU (Workflow or Storage API)
#
# Supported ingestion methods:
#   - "workflow"  → POST Osdu_ingest DAG  (default, requires workflow.creator)
#   - "storage"   → PUT  /api/storage/v2/records  (requires storage.creator)
# ======================================================================

async def _build_rddms_manifest(
    *,
    access_token: str,
    base_url: str,
    partition: str,
    dataspace: str,
    uris: Optional[list] = None,
    legal_tag: Optional[str] = None,
    owners: Optional[list] = None,
    viewers: Optional[list] = None,
    countries: Optional[list] = None,
    create_missing_refs: bool = True,
) -> Dict[str, Any]:
    """Call RDDMS manifests/build and return the generated OSDU manifest.

    If `uris` is provided, builds for those specific EML URIs.
    Otherwise builds for the entire dataspace.
    """
    url = base_url.rstrip("/") + "/api/reservoir-ddms/v2/manifests/build"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "data-partition-id": partition,
        "Content-Type": "application/json",
    }

    # Defaults matching osdu.py conventions
    partition_suffix = f"{partition}.dataservices.energy"
    legal_tag = legal_tag or f"{partition}-equinor-private-default"
    owners = owners or [f"data.default.owners@{partition_suffix}"]
    viewers = viewers or [f"data.default.viewers@{partition_suffix}"]
    countries = countries or ["NO"]

    if uris:
        body_uris = list(uris)
    else:
        body_uris = [f"eml:///dataspace('{dataspace}')"]

    body = {
        "uris": body_uris,
        "acl": {"owners": owners, "viewers": viewers},
        "legal": {"legaltags": [legal_tag], "otherRelevantDataCountries": countries},
        "createMissingReferences": create_missing_refs,
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, read=120.0)) as client:
        r = await client.post(url, headers=headers, json=body)
        if r.status_code >= 400:
            raise HTTPException(
                status_code=r.status_code,
                detail={
                    "message": "RDDMS manifests/build failed",
                    "status": r.status_code,
                    "reason": r.reason_phrase,
                    "text": r.text[:2000],
                },
            )
        return r.json() or {}


async def _ingest_via_storage(
    *,
    base_url: str,
    partition: str,
    access_token: str,
    manifest: Dict[str, Any],
) -> Dict[str, Any]:
    """PUT records to /api/storage/v2/records (direct Storage API).

    Extracts all WPC records from the manifest and sends them in one batch.
    Returns the Storage API response.
    """
    url = base_url.rstrip("/") + "/api/storage/v2/records"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "data-partition-id": partition,
        "Content-Type": "application/json",
    }

    # RDDMS manifests/build returns: {kind: "osdu:wks:Manifest:1.0.0", Data: {Datasets: [...], WorkProductComponents: [...]}}
    # Unwrap the Data envelope if present, then extract records from all sections.
    inner = manifest.get("Data", manifest)  # unwrap if envelope present
    records = []
    for section in ("WorkProductComponents", "Datasets", "ReferenceData", "MasterData", "WorkProducts"):
        items = inner.get(section) or []
        if isinstance(items, list):
            records.extend(items)

    if not records:
        raise HTTPException(
            status_code=400,
            detail="Manifest contains no records to ingest",
        )

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, read=120.0)) as client:
        r = await client.put(url, headers=headers, json=records)
        if r.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail={
                    "message": "Storage API PUT failed",
                    "status": r.status_code,
                    "reason": r.reason_phrase,
                    "text": r.text[:2000],
                },
            )
        try:
            return r.json()
        except Exception:
            return {"status_code": r.status_code, "text": r.text[:500]}


@router.post("/rddms/build")
async def rddms_build_manifest(request: Request) -> JSONResponse:
    """Build an OSDU manifest from RDDMS dataspace content (dry-run, no ingestion).

    Body:
    {
      "dataspace": "maap/drogon",          # required
      "uris": ["eml:///..."],              # optional: specific EML URIs (default: whole dataspace)
      "legalTag": "dev-RDDMS-Legal-Tag",   # optional
      "owners": ["data.default.owners@dev.dataservices.energy"],  # optional
      "viewers": ["data.default.viewers@dev.dataservices.energy"], # optional
      "countries": ["US"],                  # optional
      "createMissingRefs": true             # optional (default true)
    }
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    dataspace = body.get("dataspace")
    if not dataspace:
        raise HTTPException(status_code=400, detail="'dataspace' is required")

    access_token = _find_access_token(request)
    if not access_token:
        raise HTTPException(status_code=401, detail="access_token not found")

    base_url = _get_env("OSDU_BASE_URL")
    if not base_url:
        raise HTTPException(status_code=500, detail="OSDU_BASE_URL not configured")
    if not base_url.startswith("http"):
        base_url = f"https://{base_url}"

    partition = body.get("partition") or _get_env("DATA_PARTITION_ID", "data")

    manifest = await _build_rddms_manifest(
        access_token=access_token,
        base_url=base_url,
        partition=partition,
        dataspace=dataspace,
        uris=body.get("uris"),
        legal_tag=body.get("legalTag"),
        owners=body.get("owners"),
        viewers=body.get("viewers"),
        countries=body.get("countries"),
        create_missing_refs=body.get("createMissingRefs", True),
    )

    # Stash for later retrieval / inspection
    manifest_id = str(uuid.uuid4())
    _MANIFESTS[manifest_id] = manifest

    # Count records by section
    counts = {}
    for section in ("WorkProductComponents", "ReferenceData", "MasterData", "WorkProducts"):
        items = manifest.get(section)
        if isinstance(items, list) and items:
            counts[section] = len(items)

    return JSONResponse({
        "status": "ok",
        "manifestId": manifest_id,
        "dataspace": dataspace,
        "recordCounts": counts,
        "manifest": manifest,
    })


@router.post("/rddms/index")
async def rddms_index(request: Request) -> JSONResponse:
    """Build manifest from RDDMS and ingest into OSDU in one step.

    Body:
    {
      "dataspace": "maap/drogon",          # required
      "method": "workflow",                 # "workflow" (default) or "storage"
      "uris": ["eml:///..."],              # optional: specific EML URIs
      "legalTag": "...",                    # optional
      "owners": [...],                      # optional
      "viewers": [...],                     # optional
      "countries": [...],                   # optional
      "createMissingRefs": true,            # optional
      "partition": "dev",                   # optional override
      "appKey": "..."                       # optional (workflow method only)
    }
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    dataspace = body.get("dataspace")
    if not dataspace:
        raise HTTPException(status_code=400, detail="'dataspace' is required")

    method = (body.get("method") or "workflow").lower().strip()
    if method not in ("workflow", "storage"):
        raise HTTPException(status_code=400, detail="'method' must be 'workflow' or 'storage'")

    access_token = _find_access_token(request)
    if not access_token:
        raise HTTPException(status_code=401, detail="access_token not found")

    base_url = _get_env("OSDU_BASE_URL")
    if not base_url:
        raise HTTPException(status_code=500, detail="OSDU_BASE_URL not configured")
    if not base_url.startswith("http"):
        base_url = f"https://{base_url}"

    partition = body.get("partition") or _get_env("DATA_PARTITION_ID", "data")
    app_key = body.get("appKey") or _get_env("APP_KEY")

    # Step 1: Build manifest from RDDMS
    manifest = await _build_rddms_manifest(
        access_token=access_token,
        base_url=base_url,
        partition=partition,
        dataspace=dataspace,
        uris=body.get("uris"),
        legal_tag=body.get("legalTag"),
        owners=body.get("owners"),
        viewers=body.get("viewers"),
        countries=body.get("countries"),
        create_missing_refs=body.get("createMissingRefs", True),
    )

    # Stash manifest
    manifest_id = str(uuid.uuid4())
    _MANIFESTS[manifest_id] = manifest
    run_id = str(uuid.uuid4())

    # Step 2: Ingest
    if method == "workflow":
        ingest_response = await _post_workflow_run(
            base_url=base_url,
            partition=partition,
            app_key=app_key,
            access_token=access_token,
            manifest=manifest,
            run_id=run_id,
        )
        return JSONResponse({
            "status": "submitted",
            "method": "workflow",
            "manifestId": manifest_id,
            "runId": run_id,
            "dataspace": dataspace,
            "workflowResponse": ingest_response,
        })
    else:  # storage
        storage_response = await _ingest_via_storage(
            base_url=base_url,
            partition=partition,
            access_token=access_token,
            manifest=manifest,
        )
        return JSONResponse({
            "status": "submitted",
            "method": "storage",
            "manifestId": manifest_id,
            "dataspace": dataspace,
            "storageResponse": storage_response,
        })
