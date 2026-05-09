"""
Search router – extracted from main.py for maintainability.

Handles:
  GET  /search              – render search form
  POST /search/run          – OSDU record search (kinds)
  POST /search/schemas      – OSDU Schema Service search
  POST /search/refdata      – Reference-data record search
  GET  /search/view/{id}    – single record detail view
"""

from __future__ import annotations
import asyncio
import logging
import os
import re
from typing import Any, Dict, List, Set

import httpx
from httpx import HTTPStatusError
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from . import osdu
from .schemahandler import extract_osdu_links, extract_metadata_generic

log = logging.getLogger("rddms-admin.search")

router = APIRouter()
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


def _jinja_pretty_val(val):
    """Jinja filter: prettify metadata values that may contain JSON."""
    import json
    if val is None:
        return "-"
    s = str(val)
    if s.startswith(("[", "{")):
        try:
            obj = json.loads(s)
            return _friendly_value(obj, 600)
        except (json.JSONDecodeError, ValueError):
            pass
    return s


templates.env.filters["pretty_val"] = _jinja_pretty_val

# NOTE: auth_mode is set in templates.env.globals by main.py after instance
# init.  We no longer duplicate it here to avoid capturing a stale value
# (the import would snapshot AUTH_MODE before instances are loaded).


# ──────────────────────────────────────────────────────────────────────────────
# Utilities (private to this module)
# ──────────────────────────────────────────────────────────────────────────────

def _access_token(request: Request) -> str:
    from .common import access_token as _at
    return _at(request)


def _parse_kind_inputs(kind: str, kinds_extra: str) -> List[str]:
    """Build an ordered, de-duplicated list of kinds from primary + extra inputs."""
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
    """Return an alphabetically sorted list of OSDU kinds for the search dropdown."""
    _KINDS: list[str] = [
        "osdu:wks:dataset--ETPDataspace:1.0.0",
        "osdu:wks:master-data--BusinessDecision:1.0.0",
        "osdu:wks:master-data--CollaborationProject:1.0.0",
        "osdu:wks:master-data--Field:1.1.0",
        "osdu:wks:master-data--LocalBoundaryFeature:1.1.0",
        "osdu:wks:master-data--Organisation:1.1.0",
        "osdu:wks:master-data--Reservoir:2.0.0",
        "osdu:wks:master-data--ReservoirSegment:2.0.0",
        "osdu:wks:master-data--Risk:1.2.0",
        "osdu:wks:master-data--Well:1.1.0",
        "osdu:wks:master-data--Wellbore:1.2.0",
        "osdu:wks:work-product-component--Activity:1.0.0",
        "osdu:wks:work-product-component--ActivityTemplate:1.0.0",
        "osdu:wks:work-product-component--CollaborationProjectCollection:1.0.0",
        "osdu:wks:work-product-component--ColumnBasedTable:1.4.0",
        "osdu:wks:work-product-component--DevelopmentConcept:3.0.0",
        "osdu:wks:work-product-component--Document:1.2.0",
        "osdu:wks:work-product-component--GenericBinGrid:1.0.0",
        "osdu:wks:work-product-component--GenericRepresentation:1.2.0",
        "osdu:wks:work-product-component--GeoLabelSet:1.0.0",
        "osdu:wks:work-product-component--HorizonControlPoints:1.0.0",
        "osdu:wks:work-product-component--HorizonInterpretation:1.2.0",
        "osdu:wks:work-product-component--IjkGridRepresentation:1.0.0",
        "osdu:wks:work-product-component--LocalBoundaryFeature:1.2.0",
        "osdu:wks:work-product-component--LocalModelCompoundCrs:1.2.0",
        "osdu:wks:work-product-component--PersistedCollection:1.2.0",
        "osdu:wks:work-product-component--ReservoirEstimatedVolumes:1.1.0",
        "osdu:wks:work-product-component--SeismicBinGrid:1.3.0",
        "osdu:wks:work-product-component--SeismicHorizon:2.1.0",
        "osdu:wks:work-product-component--SeismicTraceData:1.3.0",
        "osdu:wks:work-product-component--StratigraphicColumn:1.2.0",
        "osdu:wks:work-product-component--StratigraphicColumnRankInterpretation:1.3.0",
        "osdu:wks:work-product-component--StratigraphicUnitInterpretation:1.3.0",
        "osdu:wks:work-product-component--StructureMap:1.0.0",
        "osdu:wks:work-product-component--WellLog:1.3.0",
        "osdu:wks:work-product-component--WellboreMarkerSet:1.2.0",
        "osdu:wks:work-product-component--WellboreTrajectory:1.2.0",
    ]
    return [{"kind": k} for k in _KINDS]


def _collect_refdata_kinds() -> List[Dict[str, Any]]:
    """Return an alphabetically sorted list of reference-data kinds.

    This is a comprehensive static list of known OSDU reference-data types.
    Additional types will be discovered dynamically via /search/api/refdata-kinds.
    """
    _REFDATA_KINDS: list[str] = [
        "osdu:wks:reference-data--AliasNameType:1.0.0",
        "osdu:wks:reference-data--BasinType:1.0.0",
        "osdu:wks:reference-data--ChronoStratigraphicScheme:1.0.0",
        "osdu:wks:reference-data--ChronoStratigraphy:1.0.0",
        "osdu:wks:reference-data--ColumnBasedTableType:1.0.0",
        "osdu:wks:reference-data--CoordinateReferenceSystem:1.0.0",
        "osdu:wks:reference-data--DecisionApprovalStatus:1.0.0",
        "osdu:wks:reference-data--DecisionLevel:1.0.0",
        "osdu:wks:reference-data--DocumentType:1.0.0",
        "osdu:wks:reference-data--ExplorationPhaseType:1.0.0",
        "osdu:wks:reference-data--FacetRole:1.1.0",
        "osdu:wks:reference-data--FieldDevelopmentPhaseType:1.0.0",
        "osdu:wks:reference-data--FluidType:1.0.0",
        "osdu:wks:reference-data--GeoLabelType:1.0.0",
        "osdu:wks:reference-data--GeologicTimePeriod:1.0.0",
        "osdu:wks:reference-data--IndexableElement:1.0.0",
        "osdu:wks:reference-data--LithoStratigraphicRank:1.0.0",
        "osdu:wks:reference-data--LithoStratigraphy:1.0.0",
        "osdu:wks:reference-data--LogCurveFamily:1.0.0",
        "osdu:wks:reference-data--LogCurveType:1.0.0",
        "osdu:wks:reference-data--MeasurementType:1.0.0",
        "osdu:wks:reference-data--OperatingEnvironment:1.0.0",
        "osdu:wks:reference-data--OrganisationType:1.0.0",
        "osdu:wks:reference-data--ParameterType:1.0.0",
        "osdu:wks:reference-data--PlayType:1.0.0",
        "osdu:wks:reference-data--PropertyType:1.0.0",
        "osdu:wks:reference-data--RepresentationType:1.0.0",
        "osdu:wks:reference-data--ReservoirEstimatedVolumePropertyType:1.0.0",
        "osdu:wks:reference-data--ResourceLifecycleStatus:1.0.0",
        "osdu:wks:reference-data--RiskAcceptanceCriteria:1.0.0",
        "osdu:wks:reference-data--RiskCategory:1.0.0",
        "osdu:wks:reference-data--RiskDiscipline:1.0.0",
        "osdu:wks:reference-data--RiskImpactType:1.0.0",
        "osdu:wks:reference-data--RiskProbabilityScale:1.0.0",
        "osdu:wks:reference-data--RiskSeverityScale:1.0.0",
        "osdu:wks:reference-data--RoleType:1.0.0",
        "osdu:wks:reference-data--SeismicAcquisitionType:1.0.0",
        "osdu:wks:reference-data--SeismicDimensionType:1.0.0",
        "osdu:wks:reference-data--SeismicDomainType:1.0.0",
        "osdu:wks:reference-data--SeismicProcessingType:1.0.0",
        "osdu:wks:reference-data--StratigraphicRoleType:1.0.0",
        "osdu:wks:reference-data--TrappingType:1.0.0",
        "osdu:wks:reference-data--UnitOfMeasure:1.0.0",
        "osdu:wks:reference-data--VerticalMeasurementType:1.0.0",
        "osdu:wks:reference-data--WellActivityType:1.0.0",
        "osdu:wks:reference-data--WellBoreStatusType:1.0.0",
        "osdu:wks:reference-data--WellStatusType:1.0.0",
        "osdu:wks:reference-data--WellType:1.0.0",
    ]
    return [{"kind": k} for k in _REFDATA_KINDS]


# ──────────────────────────────────────────────────────────────────────────────
# Record enrichment helpers (moved from main.py)
# ──────────────────────────────────────────────────────────────────────────────

# Keys whose values are large arrays / blobs - shown separately
_HEAVY_DATA_KEYS = frozenset({
    "ColumnBasedTable", "Columns", "ColumnValues", "ColumnNames",
    "SpatialPoint.AsIngestedCoordinates.persistableReferenceCrs",
    "VirtualProperties.DefaultLocation.AsIngestedCoordinates.persistableReferenceCrs",
    "SpatialPoint.Wgs84Coordinates",
    "VirtualProperties.DefaultLocation.Wgs84Coordinates",
})


def _friendly_value(v: Any, max_str: int = 400) -> str:
    """Convert a single value to a human-friendly string."""
    if v is None:
        return ""
    if isinstance(v, (str, int, float, bool)):
        s = str(v)
        return s if len(s) <= max_str else s[:max_str] + "…"
    if isinstance(v, dict):
        parts = []
        for dk, dv in v.items():
            sv = _friendly_value(dv, max_str=80)
            parts.append(f"{dk}: {sv}")
        s = "; ".join(parts)
        return s if len(s) <= max_str else s[:max_str] + "…"
    if isinstance(v, list):
        return _friendly_list(v, max_str)
    return str(v)[:max_str]


def _friendly_list(lst: list, max_str: int = 400) -> str:
    """Format a list for display."""
    if not lst:
        return ""
    if all(isinstance(x, (str, int, float, bool, type(None))) for x in lst):
        return ", ".join(str(x) for x in lst)
    if all(isinstance(x, dict) for x in lst):
        items = []
        for d in lst:
            parts = [f"{k}: {_friendly_value(dv, 80)}" for k, dv in d.items()]
            items.append("; ".join(parts))
        s = " │ ".join(items)
        return s if len(s) <= max_str else s[:max_str] + "…"
    s = ", ".join(_friendly_value(x, 80) for x in lst)
    return s if len(s) <= max_str else s[:max_str] + "…"


def _flatten_osdu_data(data: Dict[str, Any], max_str: int = 400) -> list:
    """Flatten an OSDU data{} block into [{name, value}, ...] pairs."""
    pairs = []
    for k in sorted(data.keys()):
        if k in _HEAVY_DATA_KEYS:
            continue
        v = data[k]
        if v is None:
            pairs.append({"name": k, "value": None})
        else:
            pairs.append({"name": k, "value": _friendly_value(v, max_str)})
    return pairs


async def _reverse_lookup(
    record_id: str,
    client: httpx.AsyncClient,
    search_url: str,
    hdr: Dict[str, str],
) -> List[Dict[str, Any]]:
    """Find records that reference *record_id* (reverse relationships)."""
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
                continue
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


async def _enrich_record_light(
    full: Dict[str, Any],
    client: httpx.AsyncClient,
    storage_url: str,
    search_url: str,
    hdr: dict,
) -> Dict[str, Any]:
    """Lightweight enrichment for the search results list.

    For most record types, does NOT call external APIs.
    For BusinessDecision records, fetches GeoLabelSet + stat REV volumes
    (2-3 targeted calls) so headline KPIs render in the list view.
    """
    from .main import (
        _normalize_volumes,
        _enrich_bd_volumes,
        _enrich_bd_geolabel,
        _enrich_bd_production,
    )

    rid = full.get("id", "")
    data_block = full.get("data", {}) or {}
    volumes = _normalize_volumes(data_block)

    # Extract links from within the record (no external fetches)
    links = extract_osdu_links(data_block) or []

    # Compact metadata pairs from data{}
    metadata_pairs: list = []
    try:
        if "Citation" in data_block or "$type" in data_block:
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
            metadata_pairs = _flatten_osdu_data(data_block)
    except Exception as e:
        log.warning("[ENRICH-LIGHT] metadata_pairs extraction failed for %s: %s", rid, e)

    result = dict(full)
    result["volumes"] = volumes
    result["links"] = links
    result["linked_labels"] = {}
    result["metadata_pairs"] = metadata_pairs
    # Parse DDMSDatasets URIs for direct RDDMS visualisation (string-only, no API calls)
    ddms_refs: list[dict[str, str]] = []
    for duri in (data_block.get("DDMSDatasets") or []):
        if not isinstance(duri, str):
            continue
        ds_m = re.search(r"dataspace\(['\"]?([^'\"\)\s]+)['\"]?\)", duri)
        uuid_m = re.search(r"\(['\"]?([0-9a-f-]{36})['\"]?\)", duri)
        if ds_m and uuid_m:
            rtype = "map" if "Grid2dRepresentation" in duri else "other"
            ddms_refs.append({
                "ds": ds_m.group(1),
                "uuid": uuid_m.group(1),
                "uri": duri,
                "rtype": rtype,
            })
    result["ddms_refs"] = ddms_refs
    # BD-specific enrichment: fetch GeoLabelSet + stat REV + production
    # for headline volumes (2-3 targeted calls, fast enough for list view)
    bd_geolabel: Dict[str, Any] = {}
    bd_production: Dict[str, Any] = {}
    bd_activity: Dict[str, Any] = {}
    if "businessdecision" in (full.get("kind") or "").lower():
        try:
            vol_task = _enrich_bd_volumes(data_block, client, storage_url, hdr) \
                if not (volumes or {}).get("ColumnValues") else asyncio.sleep(0)
            gl_task = _enrich_bd_geolabel(data_block, client, storage_url, hdr)
            prod_task = _enrich_bd_production(data_block, client, storage_url, hdr)
            vol_r, gl_r, prod_r = await asyncio.gather(
                vol_task, gl_task, prod_task,
                return_exceptions=True,
            )
            if isinstance(vol_r, dict) and vol_r.get("ColumnValues"):
                volumes = vol_r
                result["volumes"] = volumes
            if isinstance(gl_r, dict):
                bd_geolabel = gl_r
            if isinstance(prod_r, dict):
                bd_production = prod_r
        except Exception as e:
            log.warning("[ENRICH-LIGHT] BD enrichment failed for %s: %s", rid, e)
    result["bd_geolabel"] = bd_geolabel
    result["bd_production"] = bd_production
    result["bd_activity"] = bd_activity
    result["bd_maps"] = {"maps": [], "all": []}
    return result


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
    # Import BD enrichment helpers from main lazily (they depend on heavy logic there)
    from .main import (
        _normalize_volumes,
        _enrich_bd_volumes,
        _enrich_bd_geolabel,
        _enrich_bd_production,
        _enrich_bd_developmentconcept,
        _enrich_bd_activity,
        _enrich_bd_maps,
    )

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
                        "name": nm or None,
                        "kind": rr.get("kind"),
                        "version": rr.get("version"),
                    }
                    if "ETPDataspace" in (rr.get("kind") or ""):
                        entry["data"] = rr.get("data") or {}
                    return (lid, entry)
            except Exception:
                pass
            return (lid, {})

        results = await asyncio.gather(*[_fetch_label(lid) for lid in unique_lids])
        for lid, entry in results:
            linked_labels[lid] = entry
    except Exception as e:
        log.warning("[ENRICH] Linked record name hydration failed: %s", e)

    # Compact metadata pairs from data{}
    metadata_pairs: list = []
    try:
        if "Citation" in data_block or "$type" in data_block:
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
            metadata_pairs = _flatten_osdu_data(data_block)
    except Exception as e:
        log.warning("[ENRICH] metadata_pairs extraction failed for %s: %s", rid, e)

    # Parse DDMSDatasets URIs for direct RDDMS visualisation
    ddms_refs: list[dict[str, str]] = []
    for duri in (data_block.get("DDMSDatasets") or []):
        if not isinstance(duri, str):
            continue
        ds_m = re.search(r"dataspace\(['\"]?([^'\")\s]+)['\"]?\)", duri)
        uuid_m = re.search(r"\(['\"]?([0-9a-f-]{36})['\"]?\)", duri)
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
# Routes
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/search", response_class=HTMLResponse, summary="Search form (OSDU search v2)")
async def search_page(request: Request):
    kind_options = _collect_manifest_kinds()
    refdata_kinds = _collect_refdata_kinds()
    return templates.TemplateResponse(
        request, "search.html",
        {
            "kind": "",
            "kinds_extra": "",
            "kind_options": kind_options,
            "refdata_kinds": refdata_kinds,
            "q": "",
            "limit": 50,
            "returnedFields": "id,kind,version",
        },
    )


@router.post("/search/run", response_class=HTMLResponse)
async def search_run(
    request: Request,
    kind: str = Form(""),
    kinds_extra: str = Form(""),
    query: str = Form("*"),
    limit: int = Form(50),
):
    """Run an OSDU Search v2 query, then enrich each hit."""
    at = _access_token(request)
    search_url = f"https://{osdu.OSDU_BASE_URL}/api/search/v2/query"
    storage_url = f"https://{osdu.OSDU_BASE_URL}/api/storage/v2/records"
    hdr = osdu.headers(at)

    search_kinds = _parse_kind_inputs(kind, kinds_extra)
    if not search_kinds or all(not k.strip() for k in search_kinds):
        return templates.TemplateResponse(
            request, "search.html",
            {
                "error": "Please select a Kind before searching. Use the dropdown or type a kind like osdu:wks:master-data--BusinessDecision:1.0.0",
                "search_mode": "records",
                "kind": kind,
                "kinds_extra": kinds_extra,
                "kind_options": _collect_manifest_kinds(),
                "refdata_kinds": _collect_refdata_kinds(),
                "q": query,
                "limit": limit,
            },
        )

    try:
        enriched_results: List[Dict[str, Any]] = []
        seen_record_ids: Set[str] = set()
        merged_total_count = 0
        async with httpx.AsyncClient(timeout=60) as client:
            # Phase 1: Search all kinds
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
                    current_kind, r.status_code, len(res.get("results", [])),
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

            # Phase 2: Fetch full storage records in parallel
            _sem = osdu.API_SEMAPHORE

            async def _fetch_full(rid: str):
                async with _sem:
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

            # Phase 3: Light enrichment for the result list (no deep BD
            # enrichment - that happens on individual record view only).
            # This avoids 6+ extra API calls per record during search.
            enriched_results = []
            for full in valid_records:
                enriched_results.append(await _enrich_record_light(full, client, storage_url, search_url, hdr))

            enriched_results.sort(
                key=lambda r: ((r.get("data") or {}).get("Name") or r.get("id") or "").lower()
            )

        return templates.TemplateResponse(
            request, "search.html",
            {
                "results": {
                    "results": enriched_results,
                    "totalCount": merged_total_count or len(enriched_results),
                },
                "kind": "",
                "kinds_extra": "",
                "kind_options": _collect_manifest_kinds(),
                "refdata_kinds": _collect_refdata_kinds(),
                "selected_kinds": search_kinds,
                "q": "*",
                "limit": limit,
            },
        )
    except httpx.HTTPStatusError as e:
        r = e.response
        log.warning("[SEARCH] HTTP error: %s %s", r.status_code, r.text[:512] if r.text else "")
        return templates.TemplateResponse(
            request, "search.html",
            {
                "error": f"Search failed: {r.status_code} {r.reason_phrase}",
                "error_detail": (r.text[:2000] if r.text else ""),
                "kind": kind,
                "kinds_extra": kinds_extra,
                "kind_options": _collect_manifest_kinds(),
                "refdata_kinds": _collect_refdata_kinds(),
                "q": query,
                "limit": limit,
            },
            status_code=r.status_code or 500,
        )
    except Exception as e:
        log.exception("[SEARCH] Unexpected error: %s", e)
        return templates.TemplateResponse(
            request, "search.html",
            {
                "error": "Unexpected error",
                "error_detail": "See server logs",
                "kind": kind,
                "kinds_extra": kinds_extra,
                "kind_options": _collect_manifest_kinds(),
                "refdata_kinds": _collect_refdata_kinds(),
                "q": query,
                "limit": limit,
            },
            status_code=500,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Schema search (OSDU Schema Service)
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/search/schemas", response_class=HTMLResponse)
async def search_schemas(
    request: Request,
    query: str = Form("*"),
    limit: int = Form(50),
):
    """Search the OSDU Schema Service for registered schemas."""
    at = _access_token(request)
    schema_url = f"https://{osdu.OSDU_BASE_URL}/api/schema-service/v1/schema"
    hdr = osdu.headers(at)

    try:
        params: Dict[str, Any] = {"limit": min(int(limit), 500)}
        # The OSDU Schema Service only supports exact-match parameters.
        # For bare keywords and entity: prefix, we fetch a broad set and
        # filter locally (substring match) since the API doesn't do wildcards.
        local_filter_keyword = ""
        if query and query.strip() != "*":
            tokens = query.strip().split()
            for tok in tokens:
                if tok.startswith("authority:"):
                    params["authority"] = tok.split(":", 1)[1]
                elif tok.startswith("source:"):
                    params["source"] = tok.split(":", 1)[1]
                elif tok.startswith("entity:"):
                    # Use as local filter (service needs exact full entityType
                    # like "master-data--BusinessDecision", not just short name)
                    local_filter_keyword = tok.split(":", 1)[1].lower()
                elif tok.startswith("status:"):
                    params["status"] = tok.split(":", 1)[1].upper()
                elif tok.startswith("scope:"):
                    params["scope"] = tok.split(":", 1)[1].upper()
                else:
                    # Bare keyword – use as local substring filter
                    local_filter_keyword = tok.lower()

        # When doing local keyword filtering, fetch a large set from the API
        # (the keyword might not be in the first N results).
        if local_filter_keyword:
            params["limit"] = 1000

        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get(schema_url, headers=hdr, params=params)
            r.raise_for_status()
            data = r.json()

        schema_list = data.get("schemaInfos") or data.get("schemas") or []
        if isinstance(data, list):
            schema_list = data

        # Apply local keyword filter if needed (substring match on entityType/kind)
        if local_filter_keyword:
            schema_list = [
                s for s in schema_list
                if local_filter_keyword in (
                    (s.get("schemaIdentity") or {}).get("entityType", "")
                ).lower()
            ]

        table_rows = []
        for s in schema_list[:int(limit)]:
            identity = s.get("schemaIdentity") or {}
            table_rows.append({
                "authority": identity.get("authority", ""),
                "source": identity.get("source", ""),
                "entityType": identity.get("entityType", ""),
                "version": (
                    f"{identity.get('schemaVersionMajor', '')}"
                    f".{identity.get('schemaVersionMinor', '')}"
                    f".{identity.get('schemaVersionPatch', '')}"
                ),
                "status": s.get("status", ""),
                "scope": s.get("scope", ""),
                "kind": (
                    f"{identity.get('authority', '')}:"
                    f"{identity.get('source', '')}:"
                    f"{identity.get('entityType', '')}:"
                    f"{identity.get('schemaVersionMajor', '')}"
                    f".{identity.get('schemaVersionMinor', '')}"
                    f".{identity.get('schemaVersionPatch', '')}"
                ),
            })

        return templates.TemplateResponse(
            request, "search.html",
            {
                "search_mode": "schemas",
                "schema_results": table_rows,
                "schema_total": len(table_rows),
                "kind": "",
                "kinds_extra": "",
                "kind_options": _collect_manifest_kinds(),
                "refdata_kinds": _collect_refdata_kinds(),
                "schema_q": query,
                "limit": limit,
            },
        )
    except httpx.HTTPStatusError as e:
        r = e.response
        return templates.TemplateResponse(
            request, "search.html",
            {
                "error": f"Schema search failed: {r.status_code} {r.reason_phrase}",
                "error_detail": (r.text[:2000] if r.text else ""),
                "search_mode": "schemas",
                "kind": "",
                "kinds_extra": "",
                "kind_options": _collect_manifest_kinds(),
                "refdata_kinds": _collect_refdata_kinds(),
                "schema_q": query,
                "limit": limit,
            },
            status_code=r.status_code or 500,
        )
    except Exception as e:
        log.exception("[SCHEMA-SEARCH] Unexpected error: %s", e)
        return templates.TemplateResponse(
            request, "search.html",
            {
                "error": "Unexpected error searching schemas",
                "error_detail": str(e),
                "search_mode": "schemas",
                "kind": "",
                "kinds_extra": "",
                "kind_options": _collect_manifest_kinds(),
                "refdata_kinds": _collect_refdata_kinds(),
                "schema_q": query,
                "limit": limit,
            },
            status_code=500,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Reference Data search
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/search/refdata", response_class=HTMLResponse)
async def search_refdata(
    request: Request,
    kind: str = Form("osdu:wks:reference-data--*:*"),
    query: str = Form(""),
    limit: int = Form(50),
):
    """Search for reference-data records in OSDU."""
    at = _access_token(request)
    search_url = f"https://{osdu.OSDU_BASE_URL}/api/search/v2/query"
    hdr = osdu.headers(at)
    search_kind = kind.strip() if kind.strip() else "osdu:wks:reference-data--*:*"

    # Require a specific kind OR a non-wildcard query to avoid huge result sets
    q = query.strip() if query else ""
    if search_kind == "osdu:wks:reference-data--*:*" and (not q or q == "*"):
        return templates.TemplateResponse(
            request, "search.html",
            {
                "error": "Please select a specific reference-data kind or provide a query filter. Listing all reference data is too large.",
                "search_mode": "refdata",
                "kind": search_kind,
                "kinds_extra": "",
                "kind_options": _collect_manifest_kinds(),
                "refdata_kinds": _collect_refdata_kinds(),
                "refdata_q": query,
                "limit": limit,
            },
        )

    try:
        payload = {
            "kind": search_kind,
            "query": q if q else "*",
            "limit": min(int(limit), 500),
            "returnedFields": ["id", "kind", "version", "data.Name", "data.Code", "data.Description"],
            "trackTotalCount": True,
        }

        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(search_url, headers=hdr, json=payload)
            r.raise_for_status()
            res = r.json()

        total_count = int(res.get("totalCount") or 0)
        hits = res.get("results") or []

        table_rows = []
        for rec in hits:
            d = rec.get("data") or {}
            rec_kind = rec.get("kind") or ""
            entity_type = ""
            if "reference-data--" in rec_kind:
                entity_type = rec_kind.split("reference-data--")[1].split(":")[0]
            table_rows.append({
                "id": rec.get("id", ""),
                "kind": rec_kind,
                "entityType": entity_type,
                "name": d.get("Name", ""),
                "code": d.get("Code", ""),
                "description": d.get("Description", ""),
                "version": rec.get("version", ""),
            })

        return templates.TemplateResponse(
            request, "search.html",
            {
                "search_mode": "refdata",
                "refdata_results": table_rows,
                "refdata_total": total_count,
                "kind": search_kind,
                "kinds_extra": "",
                "kind_options": _collect_manifest_kinds(),
                "refdata_kinds": _collect_refdata_kinds(),
                "refdata_q": query,
                "limit": limit,
            },
        )
    except httpx.HTTPStatusError as e:
        r = e.response
        return templates.TemplateResponse(
            request, "search.html",
            {
                "error": f"Reference data search failed: {r.status_code} {r.reason_phrase}",
                "error_detail": (r.text[:2000] if r.text else ""),
                "search_mode": "refdata",
                "kind": search_kind,
                "kinds_extra": "",
                "kind_options": _collect_manifest_kinds(),
                "refdata_kinds": _collect_refdata_kinds(),
                "refdata_q": query,
                "limit": limit,
            },
            status_code=r.status_code or 500,
        )
    except Exception as e:
        log.exception("[REFDATA-SEARCH] Unexpected error: %s", e)
        return templates.TemplateResponse(
            request, "search.html",
            {
                "error": "Unexpected error searching reference data",
                "error_detail": str(e),
                "search_mode": "refdata",
                "kind": search_kind,
                "kinds_extra": "",
                "kind_options": _collect_manifest_kinds(),
                "refdata_kinds": _collect_refdata_kinds(),
                "refdata_q": query,
                "limit": limit,
            },
            status_code=500,
        )


# ──────────────────────────────────────────────────────────────────────────────
# JSON API endpoints for inline detail rendering (schema & record)
# ──────────────────────────────────────────────────────────────────────────────

from fastapi.responses import JSONResponse


@router.get("/search/api/schema/{kind:path}")
async def api_schema_detail(request: Request, kind: str):
    """Return the full schema definition JSON for a given kind."""
    at = _access_token(request)
    schema_url = f"https://{osdu.OSDU_BASE_URL}/api/schema-service/v1/schema/{kind}"
    hdr = osdu.headers(at)
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get(schema_url, headers=hdr)
            r.raise_for_status()
            return JSONResponse(r.json())
    except httpx.HTTPStatusError as e:
        return JSONResponse(
            {"error": f"{e.response.status_code} {e.response.reason_phrase}"},
            status_code=e.response.status_code or 500,
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/search/api/record/{record_id:path}")
async def api_record_detail(request: Request, record_id: str):
    """Return the full record JSON for a given record ID."""
    at = _access_token(request)
    storage_url = f"https://{osdu.OSDU_BASE_URL}/api/storage/v2/records/{record_id}"
    hdr = osdu.headers(at)
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get(storage_url, headers=hdr)
            r.raise_for_status()
            return JSONResponse(r.json())
    except httpx.HTTPStatusError as e:
        return JSONResponse(
            {"error": f"{e.response.status_code} {e.response.reason_phrase}"},
            status_code=e.response.status_code or 500,
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/search/api/refdata-kinds")
async def api_refdata_kinds(request: Request):
    """Discover all reference-data kinds from the OSDU schema service.

    Returns a sorted list that the frontend can use to populate the kind picker.
    Falls back to the static list if the schema service is unavailable.
    """
    at = _access_token(request)
    schema_url = f"https://{osdu.OSDU_BASE_URL}/api/schema-service/v1/schema"
    hdr = osdu.headers(at)
    try:
        params = {"authority": "*", "source": "*", "entityType": "*", "limit": 1000}
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(schema_url, headers=hdr, params=params)
            r.raise_for_status()
            data = r.json()

        schema_list = data.get("schemaInfos") or data.get("schemas") or []
        if isinstance(data, list):
            schema_list = data

        # Extract unique reference-data kinds
        refdata_kinds: set[str] = set()
        for s in schema_list:
            identity = s.get("schemaIdentity") or {}
            entity_type = identity.get("entityType", "")
            if entity_type.startswith("reference-data--"):
                kind_str = (
                    f"{identity.get('authority', 'osdu')}:"
                    f"{identity.get('source', 'wks')}:"
                    f"{entity_type}:"
                    f"{identity.get('schemaVersionMajor', '1')}"
                    f".{identity.get('schemaVersionMinor', '0')}"
                    f".{identity.get('schemaVersionPatch', '0')}"
                )
                refdata_kinds.add(kind_str)

        # Merge with static list
        static_kinds = {k["kind"] for k in _collect_refdata_kinds()}
        all_kinds = sorted(refdata_kinds | static_kinds)
        return JSONResponse({"kinds": all_kinds})
    except Exception as e:
        # Fall back to static list
        log.warning("[REFDATA-KINDS] Schema service fetch failed: %s", e)
        return JSONResponse({"kinds": [k["kind"] for k in _collect_refdata_kinds()]})


# ──────────────────────────────────────────────────────────────────────────────
# View single record
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/search/view/{record_id}", response_class=HTMLResponse)
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

            enriched = await _enrich_record(full, client, storage_url, search_url, hdr)

        return templates.TemplateResponse(
            request, "search.html",
            {
                "results": {"results": [enriched], "totalCount": 1},
                "kind": full.get("kind", ""),
                "kinds_extra": "",
                "kind_options": _collect_manifest_kinds(),
                "refdata_kinds": _collect_refdata_kinds(),
                "q": record_id,
                "limit": 1,
            },
        )
    except HTTPStatusError as e:
        return templates.TemplateResponse(
            request, "search.html",
            {
                "error": f"Record fetch failed: {e.response.status_code}",
                "error_detail": (e.response.text[:2000] if e.response.text else ""),
                "kind": "",
                "kinds_extra": "",
                "kind_options": _collect_manifest_kinds(),
                "refdata_kinds": _collect_refdata_kinds(),
                "q": record_id,
                "limit": 1,
            },
            status_code=e.response.status_code or 500,
        )
    except Exception as e:
        log.exception("[VIEW] Unexpected error: %s", e)
        return templates.TemplateResponse(
            request, "search.html",
            {
                "error": "Unexpected error",
                "error_detail": "See server logs",
                "kind": "",
                "kinds_extra": "",
                "kind_options": _collect_manifest_kinds(),
                "refdata_kinds": _collect_refdata_kinds(),
                "q": record_id,
                "limit": 1,
            },
            status_code=500,
        )
