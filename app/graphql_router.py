"""
GraphQL deep-search on OSDU Reservoir DDMS (RESQML data model).

Hybrid architecture: uses direct PostgreSQL when co-located with OpenETPServer,
**falls back to the Reservoir DDMS REST API** when PG is unavailable.

This enables:
  • Object browsing by type (Grid2D, IjkGrid, Properties, Horizons, ...)
  • Object graph traversal (targets/sources - any RESQML relationship)
  • Array access: read numerical property data, compute statistics
  • Deep filtering: e.g. "Grid2D with Porosity > 0.2"

Architecture (dual-path):
  ┌────────────┐  GraphQL   ┌──────────────┐  asyncpg   ┌────────────┐
  │  Browser   │ ────────── │  This module │ ─────────── │ PostgreSQL │
  │ (keys.html)│            │  (strawberry)│            │ (openkv)   │
  └────────────┘            └──────────────┘            └────────────┘
                                    │          REST API
                                    └──────────────────── │ RDDMS v2  │
                                                         └────────────┘

When GRAPHQL_PG_CONN_STRING is NOT set (common case), all resolvers
use the existing REST API via app.osdu module, which covers:
  - list_dataspaces, list_types, list_resources
  - get_resource (full JSON with metadata)
  - list_targets / list_sources (object graph)
  - list_arrays / read_array (numerical data)

Dependencies:
  strawberry-graphql[fastapi]        (always required)
  asyncpg                            (optional - for direct PG mode)
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import struct
import urllib.parse
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import strawberry
from strawberry.fastapi import GraphQLRouter as _StrawberryRouter
from strawberry.types import Info
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import osdu
from .common import access_token as _access_token

log = logging.getLogger("rddms-admin.graphql")


# ──────────────────────────────────────────────────────────────────────────────
# Backend abstraction: PG direct vs REST API
# ──────────────────────────────────────────────────────────────────────────────

_PG_CONN_STRING = os.getenv("GRAPHQL_PG_CONN_STRING") or os.getenv("POSTGRESQL_CONN_STRING", "")
_pool = None  # asyncpg.Pool or None


async def _get_pool():
    """Return asyncpg pool (or None if PG not configured/available)."""
    global _pool
    if _pool is not None:
        return _pool
    if not _PG_CONN_STRING:
        return None
    try:
        import asyncpg
        dsn = _PG_CONN_STRING
        if "=" in dsn and "://" not in dsn:
            parts = dict(p.split("=", 1) for p in dsn.split() if "=" in p)
            dsn = "postgresql://{user}:{password}@{host}:{port}/{dbname}".format(
                user=parts.get("user", "postgres"),
                password=parts.get("password", ""),
                host=parts.get("host", "localhost"),
                port=parts.get("port", "5432"),
                dbname=parts.get("dbname", "postkv"),
            )
        _pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10, command_timeout=60)
        log.info("GraphQL PG pool created")
    except Exception as e:
        log.warning("PG pool failed (will use REST fallback): %s", e)
        _pool = None
    return _pool


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def _get_token_from_info(info: Info) -> str:
    """Extract access token from Strawberry context."""
    request: Request = info.context["request"]
    return _access_token(request)


# ──────────────────────────────────────────────────────────────────────────────
# PostgreSQL-native resolvers (direct path - when PG is available)
# ──────────────────────────────────────────────────────────────────────────────

# Array element type → struct format
_ARY_TYPE_FMT = {0: "i", 1: "d", 2: "f", 3: "q", 4: "i", 5: "h"}  # int32, float64, float32, int64, int32, int16


async def _pg_schema_for_dataspace(pool, dataspace: str) -> Optional[str]:
    """Resolve a dataspace path to the PostgreSQL schema name."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT dbfile FROM admin.spaces WHERE path=$1 OR uid=$1", dataspace
        )
        return row["dbfile"] if row else None


async def _pg_list_dataspaces(pool) -> List[Dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT uid, path FROM admin.spaces ORDER BY path")
        return [{"path": r["path"], "uri": f"eml:///dataspace('{r['path']}')"} for r in rows]


async def _pg_list_types(pool, dataspace: str) -> List[Dict[str, Any]]:
    schema = await _pg_schema_for_dataspace(pool, dataspace)
    if not schema:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT t.xml as name, u.ml || '.' || t.xml as full_name, count(r.obj_id) as cnt
            FROM {schema}.typ t
            JOIN {schema}.uri u ON t.uri_id = u.id
            LEFT JOIN {schema}.res r ON r.typ_id = t.id
            GROUP BY t.xml, u.ml
            ORDER BY cnt DESC
        """)
        return [{"name": f"{r['full_name']}", "count": int(r["cnt"])} for r in rows]


async def _pg_list_resources(pool, dataspace: str, type_name: str, limit: int = 100) -> List[Dict[str, Any]]:
    schema = await _pg_schema_for_dataspace(pool, dataspace)
    if not schema:
        return []
    # type_name is like "resqml20.obj_Grid2dRepresentation" → split into ml and xml
    parts = type_name.split(".", 1)
    async with pool.acquire() as conn:
        if len(parts) == 2:
            rows = await conn.fetch(f"""
                SELECT r.obj_id, r.guid, r.name, t.xml as typ_xml, u.ml
                FROM {schema}.res r
                JOIN {schema}.typ t ON r.typ_id = t.id
                JOIN {schema}.uri u ON t.uri_id = u.id
                WHERE u.ml = $1 AND t.xml = $2
                ORDER BY r.obj_id
                LIMIT $3
            """, parts[0], parts[1], limit)
        else:
            rows = await conn.fetch(f"""
                SELECT r.obj_id, r.guid, r.name, t.xml as typ_xml, u.ml
                FROM {schema}.res r
                JOIN {schema}.typ t ON r.typ_id = t.id
                JOIN {schema}.uri u ON t.uri_id = u.id
                WHERE t.xml ILIKE '%' || $1 || '%'
                ORDER BY r.obj_id
                LIMIT $2
            """, type_name, limit)
        return [
            {"uuid": str(r["guid"]), "title": r["name"],
             "type_name": f"{r['ml']}.{r['typ_xml']}", "obj_id": r["obj_id"]}
            for r in rows
        ]


async def _pg_list_relations(pool, dataspace: str, type_name: str, uuid: str, direction: str = "both") -> List[Dict[str, Any]]:
    """Get relationships from the rel table. direction: targets|sources|both."""
    schema = await _pg_schema_for_dataspace(pool, dataspace)
    if not schema:
        return []
    async with pool.acquire() as conn:
        # Find obj_id for the given uuid
        src = await conn.fetchrow(f"SELECT obj_id FROM {schema}.res WHERE guid=$1", uuid)
        if not src:
            return []
        obj_id = src["obj_id"]
        results = []

        if direction in ("both", "targets"):
            rows = await conn.fetch(f"""
                SELECT r2.guid, r2.name, t.xml as typ_xml, u.ml, xpa.th as rel_path
                FROM {schema}.rel rel
                JOIN {schema}.res r2 ON rel.dst_id = r2.obj_id
                JOIN {schema}.typ t ON r2.typ_id = t.id
                JOIN {schema}.uri u ON t.uri_id = u.id
                LEFT JOIN {schema}.xpa xpa ON rel.xpa_id = xpa.id
                WHERE rel.obj_id = $1
            """, obj_id)
            for r in rows:
                results.append({
                    "uuid": str(r["guid"]), "name": r["name"],
                    "type_name": f"{r['ml']}.{r['typ_xml']}",
                    "direction": "target",
                    "content_type": r["rel_path"] or "",
                })

        if direction in ("both", "sources"):
            rows = await conn.fetch(f"""
                SELECT r2.guid, r2.name, t.xml as typ_xml, u.ml, xpa.th as rel_path
                FROM {schema}.rel rel
                JOIN {schema}.res r2 ON rel.obj_id = r2.obj_id
                JOIN {schema}.typ t ON r2.typ_id = t.id
                JOIN {schema}.uri u ON t.uri_id = u.id
                LEFT JOIN {schema}.xpa xpa ON rel.xpa_id = xpa.id
                WHERE rel.dst_id = $1
            """, obj_id)
            for r in rows:
                results.append({
                    "uuid": str(r["guid"]), "name": r["name"],
                    "type_name": f"{r['ml']}.{r['typ_xml']}",
                    "direction": "source",
                    "content_type": r["rel_path"] or "",
                })

        return results


async def _pg_list_arrays(pool, dataspace: str, uuid: str) -> List[Dict[str, Any]]:
    """List arrays for an object from the ary table."""
    schema = await _pg_schema_for_dataspace(pool, dataspace)
    if not schema:
        return []
    async with pool.acquire() as conn:
        src = await conn.fetchrow(f"SELECT obj_id FROM {schema}.res WHERE guid=$1", uuid)
        if not src:
            return []
        rows = await conn.fetch(f"""
            SELECT id, path, type, rank1, dim1, dim2, dim3, dim4, usize
            FROM {schema}.ary WHERE obj_id=$1
        """, src["obj_id"])
        results = []
        for r in rows:
            dims = [int(r[f"dim{i}"]) for i in range(1, 5) if r[f"dim{i}"] is not None and r[f"dim{i}"] > 0]
            total = 1
            for d in dims:
                total *= d
            results.append({
                "ary_id": int(r["id"]),
                "path": r["path"] or "",
                "type": int(r["type"]) if r["type"] is not None else 1,
                "rank": int(r["rank1"]) if r["rank1"] is not None else 0,
                "dimensions": dims,
                "total_elements": total,
                "usize": int(r["usize"]) if r["usize"] is not None else 8,
            })
        return results


async def _pg_read_array(pool, dataspace: str, uuid: str, path: str) -> List[float]:
    """Read array binary data from bin table and decode to floats."""
    schema = await _pg_schema_for_dataspace(pool, dataspace)
    if not schema:
        return []
    async with pool.acquire() as conn:
        src = await conn.fetchrow(f"SELECT obj_id FROM {schema}.res WHERE guid=$1", uuid)
        if not src:
            return []
        ary = await conn.fetchrow(f"""
            SELECT id, type, dim1, dim2, dim3, dim4, usize
            FROM {schema}.ary WHERE obj_id=$1 AND path=$2
        """, src["obj_id"], path)
        if not ary:
            return []
        # Read all binary chunks in order
        bins = await conn.fetch(f"""
            SELECT value FROM {schema}.bin WHERE ary_id=$1 ORDER BY idx
        """, ary["id"])
        raw = b"".join(b["value"] for b in bins)

        # Determine element format
        fmt_char = _ARY_TYPE_FMT.get(ary["type"] if ary["type"] is not None else 1, "d")
        elem_size = struct.calcsize(fmt_char)
        n_elements = len(raw) // elem_size if elem_size else 0
        if n_elements == 0:
            return []
        values = list(struct.unpack_from(f"<{n_elements}{fmt_char}", raw))
        return [float(v) for v in values]


# ──────────────────────────────────────────────────────────────────────────────
# REST-based resolvers (work without direct PG access)
# ──────────────────────────────────────────────────────────────────────────────

async def _rest_list_dataspaces(token: str) -> List[Dict[str, Any]]:
    rows = await osdu.list_dataspaces(token)
    return [
        {"path": r.get("path") or r.get("Path") or r.get("DataspaceId") or "", "uri": r.get("uri", "")}
        for r in rows if (r.get("path") or r.get("Path") or r.get("DataspaceId"))
    ]


async def _rest_list_types(token: str, ds: str) -> List[Dict[str, Any]]:
    enc = urllib.parse.quote(ds, safe="")
    types = await osdu.list_types(token, enc)
    result = []
    for t in types or []:
        if isinstance(t, dict):
            result.append({"name": t.get("name") or "", "count": int(t.get("count") or 0)})
        elif isinstance(t, str):
            result.append({"name": t, "count": 0})
    return result


async def _rest_list_resources(token: str, ds: str, typ: str, limit: int = 100) -> List[Dict[str, Any]]:
    enc = urllib.parse.quote(ds, safe="")
    resources = await osdu.list_resources(token, enc, typ)
    items = []
    for r in (resources or [])[:limit]:
        uid = r.get("UUID") or r.get("Uuid") or r.get("uuid") or ""
        title = r.get("Title") or (r.get("Citation") or {}).get("Title", "") or r.get("name", "")
        items.append({"uuid": str(uid), "title": title, "type_name": typ, "raw": r})
    return items


async def _rest_get_resource(token: str, ds: str, typ: str, uuid: str) -> Dict[str, Any]:
    enc = urllib.parse.quote(ds, safe="")
    return await osdu.get_resource(token, enc, typ, uuid)


async def _rest_list_targets(token: str, ds: str, typ: str, uuid: str) -> List[Dict[str, Any]]:
    enc = urllib.parse.quote(ds, safe="")
    return await osdu.list_targets(token, enc, typ, uuid)


async def _rest_list_sources(token: str, ds: str, typ: str, uuid: str) -> List[Dict[str, Any]]:
    enc = urllib.parse.quote(ds, safe="")
    return await osdu.list_sources(token, enc, typ, uuid)


async def _rest_list_arrays(token: str, ds: str, typ: str, uuid: str) -> List[Dict[str, Any]]:
    enc = urllib.parse.quote(ds, safe="")
    return await osdu.list_arrays(token, enc, typ, uuid)


async def _rest_read_array(token: str, ds: str, typ: str, uuid: str, path: str) -> List[float]:
    enc = urllib.parse.quote(ds, safe="")
    arr_data = await osdu.read_array(token, enc, typ, uuid, path_in_resource=path)
    inner = arr_data.get("data") or arr_data
    if isinstance(inner, dict):
        values = inner.get("data") or inner.get("values") or []
    elif isinstance(inner, list):
        values = inner
    else:
        values = []
    return [float(v) for v in values if v is not None]


# ──────────────────────────────────────────────────────────────────────────────
# Strawberry GraphQL Types
# ──────────────────────────────────────────────────────────────────────────────


@strawberry.enum
class ComparisonOperator(Enum):
    GT = "GT"
    GTE = "GTE"
    LT = "LT"
    LTE = "LTE"
    EQ = "EQ"


@strawberry.type
class ArrayStatistics:
    """Statistics computed from array data."""
    count: int
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    mean: Optional[float] = None
    std_dev: Optional[float] = None
    nan_count: int = 0


@strawberry.type
class CellMatch:
    """Result of a cell-value threshold filter on array data."""
    count: int
    total: int
    fraction: float


@strawberry.type
class ArrayInfo:
    """A numerical array attached to a RESQML object."""
    path: str
    data_type: str = "unknown"
    dimensions: Optional[List[int]] = None
    total_elements: int = 0
    statistics: Optional[ArrayStatistics] = None
    sample_values: Optional[List[float]] = None


@strawberry.type
class PropertyInfo:
    """A RESQML property (Continuous/Discrete) attached to a representation."""
    uuid: str
    title: str
    type_name: str
    kind: str
    uom: Optional[str] = None
    arrays: Optional[List[ArrayInfo]] = None
    statistics: Optional[ArrayStatistics] = None
    matching_cells: Optional[CellMatch] = None


@strawberry.type
class RelationInfo:
    """A relationship edge in the RESQML object graph."""
    uuid: str
    name: str
    type_name: str
    direction: str  # "target" or "source"
    content_type: str = ""


@strawberry.type
class ResqmlObject:
    """A RESQML object from the Reservoir DDMS store."""
    uuid: str
    title: str
    type_name: str
    relations: Optional[List[RelationInfo]] = None
    properties: Optional[List[PropertyInfo]] = None


@strawberry.type
class DataspaceInfo:
    """A Reservoir DDMS dataspace."""
    path: str
    uri: str = ""


@strawberry.type
class TypeSummary:
    """Count of a resource type within a dataspace."""
    name: str
    count: int


@strawberry.type
class DeepSearchResult:
    """Result of a deep search with optional array-level filtering."""
    objects: List[ResqmlObject]
    total_scanned: int
    total_matched: int
    query_description: str
    backend: str  # "REST" or "PostgreSQL"


@strawberry.type
class FederatedHit:
    """A single unified result from federated search (OSDU catalog + local RDDMS + remote RDDMS)."""
    uuid: str
    title: str
    type_name: str = ""
    dataspace: str = ""
    # Source flags
    found_in_catalog: bool = False
    found_in_rddms: bool = False          # True if found in either local or remote RDDMS
    found_in_local_rddms: bool = False     # Local PostgreSQL
    found_in_remote_rddms: bool = False    # Remote OSDU RDDMS (REST)
    # OSDU catalog metadata (if found there)
    osdu_id: Optional[str] = None
    osdu_kind: Optional[str] = None
    data_json: Optional[str] = None
    # RESQML deep data (if found in RDDMS)
    relations: Optional[List[RelationInfo]] = None
    properties: Optional[List[PropertyInfo]] = None


@strawberry.type
class FederatedSearchResult:
    """
    Combined search across OSDU catalog + local RDDMS (PG) + remote RDDMS (REST).
    Searches all sources in parallel, merges by UUID, deduplicates.
    """
    hits: List[FederatedHit]
    total_catalog: int
    total_rddms: int           # Combined local + remote
    total_local_rddms: int = 0
    total_remote_rddms: int = 0
    total_merged: int
    query_description: str
    sources: List[str]  # e.g. ["OSDU catalog", "PostgreSQL", "Remote RDDMS"]


# ──────────────────────────────────────────────────────────────────────────────
# Input types
# ──────────────────────────────────────────────────────────────────────────────


@strawberry.input
class ArrayFilter:
    """Filter on array values (deep search into numerical data)."""
    threshold: float
    operator: ComparisonOperator = ComparisonOperator.GT


@strawberry.input
class PropertyFilter:
    """Filter for properties attached to representations."""
    kind: Optional[str] = None
    title_contains: Optional[str] = None
    array_filter: Optional[ArrayFilter] = None


# ──────────────────────────────────────────────────────────────────────────────
# Computation helpers
# ──────────────────────────────────────────────────────────────────────────────


def _compute_statistics(values: List[float]) -> ArrayStatistics:
    finite = [v for v in values if math.isfinite(v)]
    nan_count = len(values) - len(finite)
    if not finite:
        return ArrayStatistics(count=len(values), nan_count=nan_count)
    min_v = min(finite)
    max_v = max(finite)
    mean = sum(finite) / len(finite)
    variance = sum((v - mean) ** 2 for v in finite) / len(finite) if len(finite) > 1 else 0
    return ArrayStatistics(
        count=len(values), min_value=min_v, max_value=max_v,
        mean=mean, std_dev=variance ** 0.5, nan_count=nan_count,
    )


def _check_threshold(values: List[float], threshold: float, op: ComparisonOperator) -> CellMatch:
    total = len(values)
    if total == 0:
        return CellMatch(count=0, total=0, fraction=0.0)
    ops = {
        ComparisonOperator.GT: lambda v: v > threshold,
        ComparisonOperator.GTE: lambda v: v >= threshold,
        ComparisonOperator.LT: lambda v: v < threshold,
        ComparisonOperator.LTE: lambda v: v <= threshold,
        ComparisonOperator.EQ: lambda v: abs(v - threshold) < 1e-9,
    }
    check = ops[op]
    count = sum(1 for v in values if math.isfinite(v) and check(v))
    return CellMatch(count=count, total=total, fraction=count / total if total else 0.0)


def _extract_property_kind(obj: Dict[str, Any]) -> str:
    """Extract property kind from a RESQML property object JSON."""
    # Try various RESQML property kind paths
    pk = obj.get("PropertyKind") or {}
    kind = pk.get("LocalPropertyKind") or pk.get("Kind") or ""
    if not kind:
        # Nested reference
        kind = (pk.get("LocalPropertyKind") or {}).get("Title", "") if isinstance(pk.get("LocalPropertyKind"), dict) else ""
    if not kind:
        kind = obj.get("StandardPropertyKind") or ""
    if not kind:
        # Try Citation title of the PropertyKind reference
        pk_ref = pk.get("Title") or ""
        if pk_ref:
            kind = pk_ref
    return kind or "Unknown"


def _extract_refs(obj: Dict[str, Any]) -> List[Dict[str, str]]:
    """Extract DataObjectReferences from a RESQML JSON object (all levels)."""
    refs = []
    def _walk(x):
        if isinstance(x, dict):
            ct = x.get("ContentType") or ""
            uid = x.get("UUID") or x.get("Uuid") or ""
            title = x.get("Title") or ""
            if ct and uid:
                refs.append({"content_type": ct, "uuid": str(uid), "title": title})
            for v in x.values():
                _walk(v)
        elif isinstance(x, list):
            for v in x:
                _walk(v)
    _walk(obj)
    return refs


# ──────────────────────────────────────────────────────────────────────────────
# Deep search helper (module-level, called from Query.deep_search)
# ──────────────────────────────────────────────────────────────────────────────


async def _deep_search_pg(
    pool,
    dataspace: str,
    type_name: str,
    title_contains: Optional[str],
    property_filter: Optional[PropertyFilter],
    include_statistics: bool,
    include_sample_values: bool,
    sample_size: int,
    limit: int,
) -> DeepSearchResult:
    """Deep search using direct PostgreSQL access - significantly faster."""
    pg_schema = await _pg_schema_for_dataspace(pool, dataspace)
    if not pg_schema:
        return DeepSearchResult(
            objects=[], total_scanned=0, total_matched=0,
            query_description=f"Dataspace '{dataspace}' not found in PG",
            backend="PostgreSQL",
        )

    async with pool.acquire() as conn:
        # Step 1: List objects of type_name
        parts = type_name.split(".", 1)
        if len(parts) == 2:
            resources = await conn.fetch(f"""
                SELECT r.obj_id, r.guid, r.name, t.xml as typ_xml, u.ml
                FROM {pg_schema}.res r
                JOIN {pg_schema}.typ t ON r.typ_id = t.id
                JOIN {pg_schema}.uri u ON t.uri_id = u.id
                WHERE u.ml = $1 AND t.xml = $2
                ORDER BY r.obj_id
            """, parts[0], parts[1])
        else:
            resources = await conn.fetch(f"""
                SELECT r.obj_id, r.guid, r.name, t.xml as typ_xml, u.ml
                FROM {pg_schema}.res r
                JOIN {pg_schema}.typ t ON r.typ_id = t.id
                JOIN {pg_schema}.uri u ON t.uri_id = u.id
                WHERE t.xml ILIKE '%' || $1 || '%'
                ORDER BY r.obj_id
            """, type_name)

        total_scanned = len(resources)
        matched: List[ResqmlObject] = []

        for res in resources:
            if len(matched) >= limit:
                break

            title = res["name"]
            if title_contains and title_contains.lower() not in title.lower():
                continue

            obj_id = res["obj_id"]
            uuid = str(res["guid"])

            # Step 2: Find property sources that reference this object
            prop_sources = await conn.fetch(f"""
                SELECT r2.obj_id as p_obj_id, r2.guid as p_guid, r2.name as p_name,
                       t2.xml as p_typ_xml, u2.ml as p_ml
                FROM {pg_schema}.rel rel
                JOIN {pg_schema}.res r2 ON rel.obj_id = r2.obj_id
                JOIN {pg_schema}.typ t2 ON r2.typ_id = t2.id
                JOIN {pg_schema}.uri u2 ON t2.uri_id = u2.id
                WHERE rel.dst_id = $1
                AND t2.xml IN ('obj_ContinuousProperty', 'obj_DiscreteProperty', 'obj_CategoricalProperty')
            """, obj_id)

            if property_filter and property_filter.kind and not prop_sources:
                continue

            # Step 3: Process properties
            property_results: List[PropertyInfo] = []
            passes_filter = not (property_filter and property_filter.array_filter)

            for ps in prop_sources:
                p_name = ps["p_name"]
                p_uuid = str(ps["p_guid"])
                p_type = f"{ps['p_ml']}.{ps['p_typ_xml']}"
                p_obj_id = ps["p_obj_id"]

                # Title filter: skip properties whose name doesn't match
                if property_filter and property_filter.title_contains:
                    if property_filter.title_contains.lower() not in p_name.lower():
                        continue

                # Determine kind from XML object content
                kind = "Unknown"
                try:
                    import xml.etree.ElementTree as ET
                    obj_row = await conn.fetchrow(
                        f"SELECT xml FROM {pg_schema}.obj WHERE id=$1", p_obj_id
                    )
                    if obj_row and obj_row["xml"]:
                        xml_str = str(obj_row["xml"])
                        if "PropertyKind" in xml_str or "LocalPropertyKind" in xml_str:
                            try:
                                root = ET.fromstring(xml_str)
                                for elem in root.iter():
                                    if "PropertyKind" in (elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag):
                                        title_elem = elem.find(".//{http://www.energistics.org/energyml/data/commonv2}Title")
                                        if title_elem is not None and title_elem.text:
                                            kind = title_elem.text
                                            break
                            except ET.ParseError:
                                pass
                except Exception:
                    pass

                # Kind filter
                if property_filter and property_filter.kind:
                    if property_filter.kind.lower() not in kind.lower() and \
                       property_filter.kind.lower() not in p_name.lower():
                        continue

                prop_info = PropertyInfo(
                    uuid=p_uuid, title=p_name, type_name=p_type, kind=kind,
                )

                # Step 4: Arrays
                if include_statistics or include_sample_values or (property_filter and property_filter.array_filter):
                    p_arrays = await conn.fetch(f"""
                        SELECT id, path, type, rank1, dim1, dim2, usize
                        FROM {pg_schema}.ary WHERE obj_id=$1
                    """, p_obj_id)

                    array_infos: List[ArrayInfo] = []
                    for pa in p_arrays:
                        pa_path = pa["path"]
                        ai = ArrayInfo(path=pa_path)
                        try:
                            values = await _pg_read_array(pool, dataspace, p_uuid, pa_path)
                        except Exception:
                            values = []
                        if values:
                            ai.total_elements = len(values)
                            if include_statistics:
                                ai.statistics = _compute_statistics(values)
                                prop_info.statistics = ai.statistics
                            if include_sample_values:
                                ai.sample_values = values[:sample_size]
                            if property_filter and property_filter.array_filter:
                                af = property_filter.array_filter
                                match_result = _check_threshold(values, af.threshold, af.operator)
                                prop_info.matching_cells = match_result
                                if match_result.count > 0:
                                    passes_filter = True
                        array_infos.append(ai)
                    prop_info.arrays = array_infos if array_infos else None

                property_results.append(prop_info)

            if property_filter and property_filter.kind and not property_results:
                continue
            if property_filter and property_filter.array_filter and not passes_filter:
                continue

            matched.append(ResqmlObject(
                uuid=uuid, title=title, type_name=type_name,
                properties=property_results if property_results else None,
            ))

    # Build description
    desc_parts = [f"type={type_name}"]
    if title_contains:
        desc_parts.append(f"title~'{title_contains}'")
    if property_filter:
        if property_filter.kind:
            desc_parts.append(f"property.kind='{property_filter.kind}'")
        if property_filter.array_filter:
            af = property_filter.array_filter
            desc_parts.append(f"cellValue {af.operator.value} {af.threshold}")

    return DeepSearchResult(
        objects=matched,
        total_scanned=total_scanned,
        total_matched=len(matched),
        query_description=" AND ".join(desc_parts),
        backend="PostgreSQL",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Query root - all resolvers use REST API fallback when PG unavailable
# ──────────────────────────────────────────────────────────────────────────────


@strawberry.type
class Query:

    @strawberry.field(description="Backend connectivity status")
    async def status(self, info: Info) -> str:
        pool = await _get_pool()
        if pool:
            try:
                async with pool.acquire() as conn:
                    row = await conn.fetchrow("SELECT version() as v")
                    return f"PostgreSQL direct: {row['v']}"
            except Exception as e:
                return f"PostgreSQL error: {e}"
        # REST mode
        try:
            token = _get_token_from_info(info)
            ds = await _rest_list_dataspaces(token)
            return f"REST API (via RDDMS v2) – {len(ds)} dataspaces available"
        except Exception as e:
            return f"REST API error: {e}"

    @strawberry.field(description="List dataspaces")
    async def dataspaces(self, info: Info) -> List[DataspaceInfo]:
        pool = await _get_pool()
        if pool:
            items = await _pg_list_dataspaces(pool)
            return [DataspaceInfo(path=i["path"], uri=i.get("uri", "")) for i in items]
        token = _get_token_from_info(info)
        items = await _rest_list_dataspaces(token)
        return [DataspaceInfo(path=i["path"], uri=i.get("uri", "")) for i in items]

    @strawberry.field(description="List resource types in a dataspace with counts")
    async def resource_types(self, info: Info, dataspace: str) -> List[TypeSummary]:
        pool = await _get_pool()
        if pool:
            types = await _pg_list_types(pool, dataspace)
            return [TypeSummary(name=t["name"], count=t["count"]) for t in types]
        token = _get_token_from_info(info)
        types = await _rest_list_types(token, dataspace)
        return [TypeSummary(name=t["name"], count=t["count"]) for t in types]

    @strawberry.field(description="Browse RESQML objects by type in a dataspace")
    async def resqml_objects(
        self,
        info: Info,
        dataspace: str,
        type_name: str,
        title_contains: Optional[str] = None,
        limit: int = 50,
    ) -> List[ResqmlObject]:
        pool = await _get_pool()
        if pool:
            resources = await _pg_list_resources(pool, dataspace, type_name, limit)
        else:
            token = _get_token_from_info(info)
            resources = await _rest_list_resources(token, dataspace, type_name, limit)
        results = []
        for r in resources:
            title = r["title"]
            if title_contains and title_contains.lower() not in title.lower():
                continue
            results.append(ResqmlObject(
                uuid=r["uuid"], title=title, type_name=r["type_name"],
            ))
        return results

    @strawberry.field(
        description=(
            "Get object graph: targets and sources (relationships) for an object. "
            "This covers all RESQML relationships: SupportingRepresentation, "
            "RepresentedInterpretation, Geologic features, CRS references, etc."
        )
    )
    async def object_relations(
        self,
        info: Info,
        dataspace: str,
        type_name: str,
        uuid: str,
        direction: str = "both",
    ) -> List[RelationInfo]:
        """
        Traverse the RESQML object graph.

        Every DataObjectReference in RESQML creates an edge:
        - targets: objects this object points to
          (e.g. Grid2D → LocalDepth3dCrs, Property → SupportingRepresentation)
        - sources: objects that point to this object
          (e.g. which Properties reference this Grid2D)

        This is how you find ancestors, topology, interpretation hierarchies.
        """
        pool = await _get_pool()
        if pool:
            rels = await _pg_list_relations(pool, dataspace, type_name, uuid, direction)
            return [
                RelationInfo(
                    uuid=r["uuid"], name=r["name"], type_name=r["type_name"],
                    direction=r["direction"], content_type=r["content_type"],
                )
                for r in rels
            ]

        # REST fallback
        token = _get_token_from_info(info)
        results = []

        if direction in ("both", "targets"):
            try:
                targets = await _rest_list_targets(token, dataspace, type_name, uuid)
                for t in targets:
                    ct = t.get("ContentType") or t.get("contentType") or ""
                    uid = t.get("UUID") or t.get("Uuid") or t.get("uuid") or ""
                    name = t.get("Title") or t.get("title") or t.get("name") or ""
                    results.append(RelationInfo(
                        uuid=str(uid), name=name, type_name=ct,
                        direction="target", content_type=ct,
                    ))
            except Exception as e:
                log.debug("targets fetch failed: %s", e)

        if direction in ("both", "sources"):
            try:
                sources = await _rest_list_sources(token, dataspace, type_name, uuid)
                for s in sources:
                    ct = s.get("ContentType") or s.get("contentType") or ""
                    uid = s.get("UUID") or s.get("Uuid") or s.get("uuid") or ""
                    name = s.get("Title") or s.get("title") or s.get("name") or ""
                    results.append(RelationInfo(
                        uuid=str(uid), name=name, type_name=ct,
                        direction="source", content_type=ct,
                    ))
            except Exception as e:
                log.debug("sources fetch failed: %s", e)

        return results

    @strawberry.field(
        description="List arrays attached to an object"
    )
    async def object_arrays(
        self,
        info: Info,
        dataspace: str,
        type_name: str,
        uuid: str,
        include_statistics: bool = False,
        include_sample_values: bool = False,
        sample_size: int = 50,
    ) -> List[ArrayInfo]:
        pool = await _get_pool()
        if pool:
            arrays = await _pg_list_arrays(pool, dataspace, uuid)
            results = []
            for a in arrays:
                ai = ArrayInfo(
                    path=a["path"],
                    data_type=_ARY_TYPE_FMT.get(a["type"], "?"),
                    dimensions=a["dimensions"],
                    total_elements=a["total_elements"],
                )
                if (include_statistics or include_sample_values) and a["path"]:
                    try:
                        values = await _pg_read_array(pool, dataspace, uuid, a["path"])
                        if values:
                            ai.total_elements = len(values)
                            if include_statistics:
                                ai.statistics = _compute_statistics(values)
                            if include_sample_values:
                                ai.sample_values = values[:sample_size]
                    except Exception as e:
                        log.debug("pg read_array %s failed: %s", a["path"], e)
                results.append(ai)
            return results

        # REST fallback
        token = _get_token_from_info(info)
        try:
            arrays = await _rest_list_arrays(token, dataspace, type_name, uuid)
        except Exception as e:
            log.debug("list_arrays failed: %s", e)
            return []

        results = []
        for a in arrays:
            uid_info = a.get("uid") or {}
            path = uid_info.get("pathInResource", "") if isinstance(uid_info, dict) else ""
            dims = a.get("dimensions") or []
            total = a.get("totalCount") or 0

            ai = ArrayInfo(path=path, total_elements=total)

            if (include_statistics or include_sample_values) and path:
                try:
                    values = await _rest_read_array(token, dataspace, type_name, uuid, path)
                    if values:
                        ai.total_elements = len(values)
                        if include_statistics:
                            ai.statistics = _compute_statistics(values)
                        if include_sample_values:
                            ai.sample_values = values[:sample_size]
                except Exception as e:
                    log.debug("read_array %s failed: %s", path, e)

            results.append(ai)
        return results

    @strawberry.field(
        description=(
            "Deep search: find representations with attached properties matching "
            "kind and cell-value criteria. Queries the RESQML graph + array data. "
            "Supports multiple dataspaces via the 'dataspaces' list parameter."
        )
    )
    async def deep_search(
        self,
        info: Info,
        dataspace: Optional[str] = None,
        dataspaces: Optional[List[str]] = None,
        type_name: str = "resqml20.obj_Grid2dRepresentation",
        title_contains: Optional[str] = None,
        property_filter: Optional[PropertyFilter] = None,
        include_statistics: bool = True,
        include_sample_values: bool = False,
        sample_size: int = 50,
        limit: int = 20,
    ) -> DeepSearchResult:
        """
        The flagship query:
          1. List objects of type_name in dataspace(s)
          2. For each, find attached properties via sources (reverse graph)
          3. Filter by property kind
          4. Optionally load array values and apply threshold filter

        Supports querying multiple dataspaces at once via 'dataspaces' param.
        Example: IjkGrid with Porosity > 0.25 across two dataspaces
        """
        # Resolve dataspace list (backwards-compatible: single 'dataspace' still works)
        ds_list: List[str] = []
        if dataspaces:
            ds_list = list(dataspaces)
        elif dataspace:
            ds_list = [dataspace]
        else:
            # Fall back to listing available dataspaces
            pool = await _get_pool()
            if pool:
                all_ds = await _pg_list_dataspaces(pool)
                ds_list = [d["path"] for d in all_ds][:5]  # cap at 5

        if not ds_list:
            return DeepSearchResult(
                objects=[], total_scanned=0, total_matched=0,
                query_description="No dataspace specified and none found",
                backend="PostgreSQL" if await _get_pool() else "REST",
            )

        # Single dataspace: use existing path
        if len(ds_list) == 1:
            pool = await _get_pool()
            if pool:
                return await _deep_search_pg(
                    pool, ds_list[0], type_name, title_contains,
                    property_filter, include_statistics, include_sample_values,
                    sample_size, limit,
                )
            token = _get_token_from_info(info)
            return await self._deep_search_rest(
                token, ds_list[0], type_name, title_contains,
                property_filter, include_statistics, include_sample_values,
                sample_size, limit,
            )

        # Multiple dataspaces: run in parallel and merge
        pool = await _get_pool()
        if pool:
            import asyncio
            tasks = [
                _deep_search_pg(
                    pool, ds, type_name, title_contains,
                    property_filter, include_statistics, include_sample_values,
                    sample_size, limit,
                )
                for ds in ds_list
            ]
            results = await asyncio.gather(*tasks)
            return self._merge_deep_results(results, ds_list, limit)

        # REST multi-dataspace
        token = _get_token_from_info(info)
        import asyncio
        tasks = [
            self._deep_search_rest(
                token, ds, type_name, title_contains,
                property_filter, include_statistics, include_sample_values,
                sample_size, limit,
            )
            for ds in ds_list
        ]
        results = await asyncio.gather(*tasks)
        return self._merge_deep_results(results, ds_list, limit)

    @strawberry.field(
        description=(
            "Federated search: query OSDU catalog AND Reservoir DDMS simultaneously. "
            "Merges results by UUID. Covers: (1) indexed OSDU records with ETP links, "
            "(2) un-indexed RDDMS data (local PG), (3) both together with dedup. "
            "Set search_catalog=false to skip OSDU (e.g. local-only demo). "
            "Set search_rddms=false to skip RDDMS (e.g. catalog-only lookup)."
        )
    )
    async def federated_search(
        self,
        info: Info,
        text: str = "*",
        kind: Optional[str] = None,
        type_name: Optional[str] = None,
        dataspaces: Optional[List[str]] = None,
        search_catalog: bool = True,
        search_rddms: bool = True,
        search_remote_rddms: bool = True,
        include_relations: bool = False,
        include_properties: bool = False,
        include_statistics: bool = False,
        property_filter: Optional[PropertyFilter] = None,
        limit: int = 30,
    ) -> FederatedSearchResult:
        """
        Three-path federated search.

        Path A (OSDU Catalog):
          Hits OSDU Search API with `kind` + `text` query.
          Extracts UUID, dataspace, RESQML type from each record's ResourceURI
          (eml:///dataspace('x/y')/resqml20.obj_Type('uuid')).

        Path B (Local RDDMS / PostgreSQL):
          Queries local PG for objects matching `type_name`
          and optional `text` title filter, within local dataspaces.

        Path C (Remote RDDMS / REST):
          Queries the remote OSDU RDDMS REST API for objects in
          remote dataspaces (those not available in local PG).

        Merge: Results are merged by UUID. If same object found in multiple
               sources, metadata is combined.

        Use cases:
          • "Search everything for 'porosity'" → hits all three
          • "Find OSDU Grid2D records & show their RESQML relations" → catalog + enrich
          • "Browse local un-indexed PG data" → search_catalog=false, search_rddms=true
          • "Compare local vs remote RDDMS" → both, check flags
          • "Check if OSDU records actually exist in RDDMS" → all three, compare flags
        """
        import httpx
        import json as _json
        import re as _re

        token = _get_token_from_info(info)
        hits_by_uuid: Dict[str, FederatedHit] = {}
        total_catalog = 0
        total_rddms = 0
        sources: List[str] = []

        # ── Path A: OSDU Catalog ──────────────────────────────────────────────
        if search_catalog and osdu.OSDU_BASE_URL:
            search_url = f"https://{osdu.OSDU_BASE_URL}/api/search/v2/query"
            hdr = osdu.headers(token)
            osdu_kind = kind or "osdu:wks:work-product-component--*:*"

            # Build query: include type_name in search text so catalog
            # filters by RESQML type (e.g. "IjkGridRepresentation")
            query_text = text if text != "*" else "*"
            if type_name and query_text == "*":
                # Extract the short class name for the catalog text query
                short_type = type_name.rsplit(".", 1)[-1].replace("obj_", "")
                query_text = f"\"{short_type}\""
            elif type_name and query_text != "*":
                short_type = type_name.rsplit(".", 1)[-1].replace("obj_", "")
                query_text = f"{query_text} AND \"{short_type}\""

            payload: Dict[str, Any] = {
                "kind": osdu_kind,
                "query": query_text,
                "limit": min(limit, 100),
                "returnedFields": ["id", "kind", "version", "data"],
                "trackTotalCount": True,
            }
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    r = await client.post(search_url, headers=hdr, json=payload)
                    r.raise_for_status()
                    resp = r.json()
                    total_catalog = int(resp.get("totalCount") or 0)
                    sources.append("OSDU catalog")

                    for hit in resp.get("results", [])[:limit]:
                        rid = hit.get("id", "")
                        rkind = hit.get("kind", "")
                        data = hit.get("data") or {}
                        name = (
                            data.get("Name") or data.get("FacilityName")
                            or data.get("Description") or data.get("ProjectName")
                            or (rid.rsplit(":", 1)[0].rsplit("--", 1)[-1] if rid else "")
                        )
                        uuid = Query._extract_uuid(data, rid)
                        ds = Query._extract_dataspace(data, rid)
                        rtype = Query._extract_resqml_type(rkind, data)

                        # Post-filter: skip catalog hits that don't match the
                        # requested type_name (if one was specified)
                        if type_name and rtype:
                            short_requested = type_name.rsplit(".", 1)[-1].replace("obj_", "").lower()
                            short_actual = rtype.rsplit(".", 1)[-1].replace("obj_", "").lower()
                            if short_requested != short_actual:
                                continue

                        fh = FederatedHit(
                            uuid=uuid or rid,
                            title=name,
                            type_name=rtype or "",
                            dataspace=ds or "",
                            found_in_catalog=True,
                            osdu_id=rid,
                            osdu_kind=rkind,
                            data_json=_json.dumps(data) if data else None,
                        )
                        key = uuid or rid
                        hits_by_uuid[key] = fh

            except Exception as e:
                log.warning("federated_search catalog path failed: %s", e)
                sources.append(f"OSDU catalog (error: {e})")

        # ── Determine RESQML types to search ──────────────────────────────────
        target_types: List[str] = []
        if type_name:
            target_types = [type_name]
        else:
            target_types = [
                "resqml20.obj_IjkGridRepresentation",
                "resqml20.obj_Grid2dRepresentation",
                "resqml20.obj_WellboreFrameRepresentation",
                "resqml20.obj_WellboreFeature",
                "resqml20.obj_HorizonInterpretation",
            ]

        title_filter = text if text != "*" else None
        total_local_rddms = 0
        total_remote_rddms = 0

        # ── Path B: Local RDDMS (PostgreSQL direct) ───────────────────────────
        if search_rddms:
            pool = await _get_pool()
            if pool:
                # Discover which of the requested dataspaces are in local PG
                local_ds_set: set = set()
                all_local = await _pg_list_dataspaces(pool)
                local_ds_set = {d["path"] for d in all_local}

                ds_list = list(dataspaces) if dataspaces else []
                if not ds_list:
                    ds_list = list(local_ds_set)[:10]

                # Only search local dataspaces via PG
                local_dataspaces = [d for d in ds_list if d in local_ds_set]

                if local_dataspaces:
                    sources.append("PostgreSQL")
                    for ds in local_dataspaces:
                        for ttype in target_types:
                            try:
                                resources = await _pg_list_resources(pool, ds, ttype, limit)
                                for r in resources:
                                    uid = r["uuid"]
                                    rtitle = r["title"]
                                    if title_filter and title_filter.lower() not in rtitle.lower():
                                        continue
                                    total_local_rddms += 1
                                    key = uid or f"{ds}::{ttype}::{rtitle}"

                                    if key in hits_by_uuid:
                                        hits_by_uuid[key].found_in_rddms = True
                                        hits_by_uuid[key].found_in_local_rddms = True
                                        if not hits_by_uuid[key].dataspace:
                                            hits_by_uuid[key].dataspace = ds
                                        if not hits_by_uuid[key].type_name:
                                            hits_by_uuid[key].type_name = ttype
                                    else:
                                        hits_by_uuid[key] = FederatedHit(
                                            uuid=uid or key, title=rtitle,
                                            type_name=ttype, dataspace=ds,
                                            found_in_rddms=True,
                                            found_in_local_rddms=True,
                                        )
                            except Exception:
                                pass
                            if len(hits_by_uuid) >= limit:
                                break
                        if len(hits_by_uuid) >= limit:
                            break

        # ── Path C: Remote RDDMS (REST API) ──────────────────────────────────
        if search_remote_rddms and osdu.OSDU_BASE_URL:
            pool = await _get_pool()
            ds_list = list(dataspaces) if dataspaces else []

            # Determine which dataspaces are remote (not in local PG)
            local_ds_set_c: set = set()
            if pool:
                all_local_c = await _pg_list_dataspaces(pool)
                local_ds_set_c = {d["path"] for d in all_local_c}

            remote_dataspaces = [d for d in ds_list if d not in local_ds_set_c]

            # If no specific dataspaces given, try to list from remote RDDMS
            if not ds_list:
                try:
                    remote_rows = await _rest_list_dataspaces(token)
                    remote_dataspaces = [d["path"] for d in remote_rows
                                        if d["path"] not in local_ds_set_c][:10]
                except Exception:
                    remote_dataspaces = []

            if remote_dataspaces:
                sources.append("Remote RDDMS")
                for ds in remote_dataspaces:
                    for ttype in target_types:
                        try:
                            resources = await _rest_list_resources(token, ds, ttype, limit)
                            for r in resources:
                                uid = r["uuid"]
                                rtitle = r["title"]
                                if title_filter and title_filter.lower() not in rtitle.lower():
                                    continue
                                total_remote_rddms += 1
                                key = uid or f"{ds}::{ttype}::{rtitle}"

                                if key in hits_by_uuid:
                                    hits_by_uuid[key].found_in_rddms = True
                                    hits_by_uuid[key].found_in_remote_rddms = True
                                    if not hits_by_uuid[key].dataspace:
                                        hits_by_uuid[key].dataspace = ds
                                    if not hits_by_uuid[key].type_name:
                                        hits_by_uuid[key].type_name = ttype
                                else:
                                    hits_by_uuid[key] = FederatedHit(
                                        uuid=uid or key, title=rtitle,
                                        type_name=ttype, dataspace=ds,
                                        found_in_rddms=True,
                                        found_in_remote_rddms=True,
                                    )
                        except Exception:
                            pass
                        if len(hits_by_uuid) >= limit:
                            break
                    if len(hits_by_uuid) >= limit:
                        break

        total_rddms = total_local_rddms + total_remote_rddms

        # ── Enrichment phase: relations + properties ──────────────────────────
        if include_relations or include_properties or property_filter:
            pool = await _get_pool()
            for fh in list(hits_by_uuid.values())[:limit]:
                if not fh.dataspace or not fh.type_name or not fh.uuid:
                    continue
                try:
                    if pool and include_relations:
                        rels = await _pg_list_relations(pool, fh.dataspace, fh.type_name, fh.uuid, "both")
                        fh.relations = [
                            RelationInfo(
                                uuid=r["uuid"], name=r["name"], type_name=r["type_name"],
                                direction=r["direction"], content_type=r["content_type"],
                            ) for r in rels
                        ]
                    elif not pool and include_relations:
                        try:
                            targets = await _rest_list_targets(token, fh.dataspace, fh.type_name, fh.uuid)
                            sources_r = await _rest_list_sources(token, fh.dataspace, fh.type_name, fh.uuid)
                            rels_list: List[RelationInfo] = []
                            for t in targets:
                                ct = t.get("ContentType") or ""
                                rels_list.append(RelationInfo(
                                    uuid=t.get("UUID") or t.get("uuid") or "",
                                    name=t.get("Title") or t.get("name") or "",
                                    type_name=ct, direction="target", content_type=ct,
                                ))
                            for s in sources_r:
                                ct = s.get("ContentType") or ""
                                rels_list.append(RelationInfo(
                                    uuid=s.get("UUID") or s.get("uuid") or "",
                                    name=s.get("Title") or s.get("name") or "",
                                    type_name=ct, direction="source", content_type=ct,
                                ))
                            fh.relations = rels_list
                        except Exception:
                            pass

                    if pool and (include_properties or property_filter):
                        # Find property sources
                        if not fh.relations:
                            rels = await _pg_list_relations(pool, fh.dataspace, fh.type_name, fh.uuid, "sources")
                            all_rels = rels
                        else:
                            all_rels = [{"uuid": r.uuid, "name": r.name, "type_name": r.type_name, "direction": r.direction}
                                       for r in (fh.relations or [])]
                        prop_rels = [
                            r for r in all_rels
                            if r.get("direction", "") == "source" and "Property" in r.get("type_name", "")
                        ]
                        if prop_rels:
                            props: List[PropertyInfo] = []
                            passes_filter = not property_filter
                            for pr in prop_rels[:20]:
                                pi = PropertyInfo(
                                    uuid=pr["uuid"], title=pr["name"],
                                    type_name=pr["type_name"], kind="",
                                )
                                if include_statistics or property_filter:
                                    try:
                                        arrays = await _pg_list_arrays(pool, fh.dataspace, pr["uuid"])
                                        if arrays:
                                            values = await _pg_read_array(pool, fh.dataspace, pr["uuid"], arrays[0]["path"])
                                            if values:
                                                pi.statistics = _compute_statistics(values)
                                                if property_filter and property_filter.array_filter:
                                                    af = property_filter.array_filter
                                                    match = _check_threshold(values, af.threshold, af.operator)
                                                    pi.matching_cells = match
                                                    if match.count > 0:
                                                        passes_filter = True
                                    except Exception:
                                        pass

                                # Title filter on property
                                if property_filter and property_filter.title_contains:
                                    if property_filter.title_contains.lower() not in pi.title.lower():
                                        continue
                                props.append(pi)

                            if property_filter and not passes_filter:
                                # Remove this hit - doesn't pass filter
                                del hits_by_uuid[fh.uuid]
                                continue
                            fh.properties = props if props else None
                except Exception as e:
                    log.debug("federated enrichment failed for %s: %s", fh.uuid, e)

        # ── Build result ──────────────────────────────────────────────────────
        merged = list(hits_by_uuid.values())[:limit]
        desc_parts = []
        if text != "*":
            desc_parts.append(f"text='{text}'")
        if kind:
            desc_parts.append(f"kind={kind}")
        if type_name:
            desc_parts.append(f"type={type_name}")
        if dataspaces:
            desc_parts.append(f"dataspaces={dataspaces}")
        desc_parts.append(f"sources: {', '.join(sources)}")

        return FederatedSearchResult(
            hits=merged,
            total_catalog=total_catalog,
            total_rddms=total_rddms,
            total_local_rddms=total_local_rddms,
            total_remote_rddms=total_remote_rddms,
            total_merged=len(merged),
            query_description=" | ".join(desc_parts),
            sources=sources,
        )

    @staticmethod
    def _extract_uuid(data: Dict[str, Any], rid: str) -> Optional[str]:
        """Extract a RESQML UUID from OSDU record data or ID."""
        import re as _re
        # From data.ResourceURI: eml:///dataspace('x/y')/resqml20.obj_Type('uuid')
        uri = data.get("ResourceURI") or data.get("DataObjectURI") or ""
        m = _re.search(r"\(([0-9a-f-]{36})\)", uri)
        if m:
            return m.group(1)
        # Check data fields
        for key in ("ResourceID", "UUID", "Uuid", "uuid", "NativeIdentifier"):
            val = data.get(key)
            if val and _re.match(r"^[0-9a-f-]{36}$", str(val)):
                return str(val)
        # Try from record ID (often: ...--TypeName:UUID:version)
        parts = rid.split(":")
        for p in parts:
            if _re.match(r"^[0-9a-f-]{36}$", p):
                return p
        return None

    @staticmethod
    def _extract_dataspace(data: Dict[str, Any], rid: str) -> Optional[str]:
        """Extract dataspace from OSDU record ResourceURI."""
        import re as _re
        # eml:///dataspace('maap/drogon')/resqml20.obj_Grid2dRepresentation(...)
        uri = data.get("ResourceURI") or data.get("DataObjectURI") or ""
        m = _re.search(r"dataspace\(['\"]?([^'\")\s]+)['\"]?\)", uri)
        if m:
            return m.group(1)
        return None

    @staticmethod
    def _extract_resqml_type(kind: str, data: Dict[str, Any]) -> Optional[str]:
        """Infer RESQML type from ResourceURI or OSDU kind."""
        import re as _re
        # From ResourceURI: eml:///dataspace('x')/resqml20.obj_Grid2dRepresentation('uuid')
        uri = data.get("ResourceURI") or data.get("DataObjectURI") or ""
        m = _re.search(r"(resqml\d+\.obj_\w+)", uri)
        if m:
            return m.group(1)
        # From OSDU kind: work-product-component--IjkGridRepresentation:1.0.0
        kind_type = kind.rsplit("--", 1)[-1].split(":")[0] if "--" in kind else ""
        type_map = {
            "IjkGridRepresentation": "resqml20.obj_IjkGridRepresentation",
            "Grid2dRepresentation": "resqml20.obj_Grid2dRepresentation",
            "WellboreFeature": "resqml20.obj_WellboreFeature",
            "WellboreTrajectory": "resqml20.obj_WellboreTrajectoryRepresentation",
            "HorizonInterpretation": "resqml20.obj_HorizonInterpretation",
            "FaultInterpretation": "resqml20.obj_FaultInterpretation",
            "ContinuousProperty": "resqml20.obj_ContinuousProperty",
            "DiscreteProperty": "resqml20.obj_DiscreteProperty",
            "WellboreFrameRepresentation": "resqml20.obj_WellboreFrameRepresentation",
            "GenericRepresentation": "resqml20.obj_Grid2dRepresentation",
        }
        if kind_type in type_map:
            return type_map[kind_type]
        # From data.SchemaFormatTypeID
        ct = data.get("SchemaFormatTypeID") or data.get("ContentType") or ""
        m = _re.search(r"(resqml\d+\.obj_\w+)", ct)
        if m:
            return m.group(1)
        return kind_type if kind_type else None

    @staticmethod
    def _merge_deep_results(results: list, ds_list: List[str], limit: int) -> DeepSearchResult:
        """Merge DeepSearchResult from multiple dataspaces."""
        all_objects: List[ResqmlObject] = []
        total_scanned = 0
        total_matched = 0
        backends = set()
        for r in results:
            total_scanned += r.total_scanned
            total_matched += r.total_matched
            all_objects.extend(r.objects)
            backends.add(r.backend)
        all_objects = all_objects[:limit]
        backend = " + ".join(sorted(backends))
        desc = f"Searched {len(ds_list)} dataspaces: {', '.join(ds_list)}"
        return DeepSearchResult(
            objects=all_objects,
            total_scanned=total_scanned,
            total_matched=total_matched,
            query_description=desc,
            backend=backend,
        )

    async def _deep_search_rest(
        self,
        token: str,
        dataspace: str,
        type_name: str,
        title_contains: Optional[str],
        property_filter: Optional[PropertyFilter],
        include_statistics: bool,
        include_sample_values: bool,
        sample_size: int,
        limit: int,
    ) -> DeepSearchResult:
        """REST-based deep search for a single dataspace."""
        backend = "REST"

        # Step 1: List target objects
        try:
            resources = await _rest_list_resources(token, dataspace, type_name, limit * 3)
        except Exception as e:
            return DeepSearchResult(
                objects=[], total_scanned=0, total_matched=0,
                query_description=f"ERROR listing {type_name}: {e}", backend=backend,
            )

        total_scanned = len(resources)
        matched: List[ResqmlObject] = []

        for r in resources:
            if len(matched) >= limit:
                break

            title = r["title"]
            if title_contains and title_contains.lower() not in title.lower():
                continue

            uuid = r["uuid"]
            if not uuid:
                continue

            # Step 2: find attached properties (sources that point to this object)
            try:
                sources = await _rest_list_sources(token, dataspace, type_name, uuid)
            except Exception:
                sources = []

            # Filter to property types
            prop_sources = [
                s for s in sources
                if any(k in (s.get("ContentType") or s.get("contentType") or "")
                       for k in ("ContinuousProperty", "DiscreteProperty", "CategoricalProperty"))
            ]

            if property_filter and property_filter.kind and not prop_sources:
                continue

            # Step 3: fetch property details and filter by kind
            property_results: List[PropertyInfo] = []
            passes_filter = not (property_filter and property_filter.array_filter)

            for ps in prop_sources:
                p_ct = ps.get("ContentType") or ps.get("contentType") or ""
                p_uuid = ps.get("UUID") or ps.get("Uuid") or ps.get("uuid") or ""
                p_name = ps.get("Title") or ps.get("title") or ps.get("name") or ""
                if not p_uuid:
                    continue

                # Determine property type for API call
                if "ContinuousProperty" in p_ct:
                    p_type = "resqml20.obj_ContinuousProperty"
                elif "DiscreteProperty" in p_ct:
                    p_type = "resqml20.obj_DiscreteProperty"
                elif "CategoricalProperty" in p_ct:
                    p_type = "resqml20.obj_CategoricalProperty"
                else:
                    continue

                # Fetch property object to get kind
                try:
                    p_obj = await _rest_get_resource(token, dataspace, p_type, p_uuid)
                except Exception:
                    continue

                kind = _extract_property_kind(p_obj)
                uom = p_obj.get("UOM") or p_obj.get("Uom") or None

                # Kind filter
                if property_filter and property_filter.kind:
                    if property_filter.kind.lower() not in kind.lower():
                        continue

                # Title filter on property
                if property_filter and property_filter.title_contains:
                    p_title = (p_obj.get("Citation") or {}).get("Title", "") or p_name
                    if property_filter.title_contains.lower() not in p_title.lower():
                        continue

                prop_info = PropertyInfo(
                    uuid=p_uuid,
                    title=(p_obj.get("Citation") or {}).get("Title", "") or p_name,
                    type_name=p_type,
                    kind=kind,
                    uom=uom,
                )

                # Step 4: optionally load array data for statistics/filtering
                if include_statistics or include_sample_values or (property_filter and property_filter.array_filter):
                    try:
                        p_arrays = await _rest_list_arrays(token, dataspace, p_type, p_uuid)
                    except Exception:
                        p_arrays = []

                    array_infos: List[ArrayInfo] = []
                    for pa in p_arrays:
                        pa_uid = pa.get("uid") or {}
                        pa_path = pa_uid.get("pathInResource", "") if isinstance(pa_uid, dict) else ""
                        if not pa_path:
                            continue

                        ai = ArrayInfo(path=pa_path)
                        try:
                            values = await _rest_read_array(token, dataspace, p_type, p_uuid, pa_path)
                        except Exception:
                            values = []

                        if values:
                            ai.total_elements = len(values)
                            if include_statistics:
                                ai.statistics = _compute_statistics(values)
                                prop_info.statistics = ai.statistics
                            if include_sample_values:
                                ai.sample_values = values[:sample_size]

                            # Array threshold filter
                            if property_filter and property_filter.array_filter:
                                af = property_filter.array_filter
                                match = _check_threshold(values, af.threshold, af.operator)
                                prop_info.matching_cells = match
                                if match.count > 0:
                                    passes_filter = True

                        array_infos.append(ai)

                    prop_info.arrays = array_infos if array_infos else None

                property_results.append(prop_info)

            # If we had a property kind filter and no properties matched, skip
            if property_filter and property_filter.kind and not property_results:
                continue
            # If array filter active but nothing passed, skip
            if property_filter and property_filter.array_filter and not passes_filter:
                continue

            matched.append(ResqmlObject(
                uuid=uuid, title=title, type_name=type_name,
                properties=property_results if property_results else None,
            ))

        # Build description
        desc_parts = [f"type={type_name}"]
        if title_contains:
            desc_parts.append(f"title~'{title_contains}'")
        if property_filter:
            if property_filter.kind:
                desc_parts.append(f"property.kind='{property_filter.kind}'")
            if property_filter.array_filter:
                af = property_filter.array_filter
                desc_parts.append(f"cellValue {af.operator.value} {af.threshold}")

        return DeepSearchResult(
            objects=matched,
            total_scanned=total_scanned,
            total_matched=len(matched),
            query_description=" AND ".join(desc_parts),
            backend=backend,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Build Strawberry schema and FastAPI router
# ──────────────────────────────────────────────────────────────────────────────

schema = strawberry.Schema(query=Query)


async def get_context(request: Request) -> dict:
    return {"request": request}


graphql_app = _StrawberryRouter(schema, context_getter=get_context)

router = APIRouter()
router.include_router(graphql_app, prefix="/graphql")


@router.post("/api/graphql/query")
async def graphql_query_api(request: Request):
    """Execute a GraphQL query. Body: {"query": "...", "variables": {...}}"""
    body = await request.json()
    query = body.get("query", "")
    variables = body.get("variables") or {}

    if not query:
        return JSONResponse({"errors": [{"message": "No query provided"}]}, status_code=400)

    result = await schema.execute(
        query,
        variable_values=variables,
        context_value={"request": request},
    )

    response: dict[str, Any] = {}
    if result.data is not None:
        response["data"] = result.data
    if result.errors:
        response["errors"] = [
            {"message": str(e), "path": e.path} for e in result.errors
        ]
    return JSONResponse(response)


@router.get("/api/graphql/info")
async def graphql_info():
    """Return GraphQL backend configuration info (no auth required)."""
    pool = await _get_pool()
    pg_configured = bool(_PG_CONN_STRING)
    pg_connected = pool is not None
    # Mask password in connection string for display
    display_conn = ""
    if _PG_CONN_STRING:
        import re
        display_conn = re.sub(r"password=\S+", "password=***", _PG_CONN_STRING)
        display_conn = re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", display_conn)

    return JSONResponse({
        "pg_configured": pg_configured,
        "pg_connected": pg_connected,
        "pg_connection": display_conn or None,
        "backend": "PostgreSQL" if pg_connected else ("REST API" if not pg_configured else "PG configured but not connected"),
        "hint": "Set GRAPHQL_PG_CONN_STRING env var on the server to enable direct PostgreSQL access. "
                "Example: host=localhost port=5433 dbname=openetp user=tester password=tester",
    })


# ──────────────────────────────────────────────────────────────────────────────
# Reference Data: Property aliases, canonical names, standard kinds, type list
# ──────────────────────────────────────────────────────────────────────────────

# RESQML Standard Property Kinds (Energistics RESQML 2.0.1 spec)
_STANDARD_PROPERTY_KINDS = [
    {"name": "porosity", "aliases": ["poro", "phit", "phi", "nphi"], "description": "Fraction of void space in rock", "uom": "v/v"},
    {"name": "permeability", "aliases": ["perm", "permx", "permy", "permz", "klogh", "kh"], "description": "Ability of rock to transmit fluids", "uom": "mD"},
    {"name": "water saturation", "aliases": ["sw", "swat", "swatinit", "swl", "swcr"], "description": "Fraction of pore space filled with water", "uom": "v/v"},
    {"name": "oil saturation", "aliases": ["so", "soil"], "description": "Fraction of pore space filled with oil", "uom": "v/v"},
    {"name": "gas saturation", "aliases": ["sg", "sgas"], "description": "Fraction of pore space filled with gas", "uom": "v/v"},
    {"name": "net-to-gross", "aliases": ["ntg", "net_fraction", "netfrac"], "description": "Net-to-gross ratio", "uom": "v/v"},
    {"name": "depth", "aliases": ["tvd", "tvdss", "z", "cell_z", "md"], "description": "Vertical depth (TVD or TVDSS)", "uom": "m"},
    {"name": "pressure", "aliases": ["pres", "pressure", "bhp", "pp"], "description": "Formation or fluid pressure", "uom": "bar"},
    {"name": "temperature", "aliases": ["temp", "temperature"], "description": "Formation temperature", "uom": "degC"},
    {"name": "volume", "aliases": ["vol", "bulk", "total_bulk", "pore", "total_pore"], "description": "Cell or pore volume", "uom": "m3"},
    {"name": "velocity", "aliases": ["vp", "vs", "velocity", "velmod"], "description": "Seismic P/S-wave velocity", "uom": "m/s"},
    {"name": "density", "aliases": ["dens", "rhob", "density", "bulk_density"], "description": "Rock or fluid density", "uom": "g/cm3"},
    {"name": "acoustic impedance", "aliases": ["ai", "impedance"], "description": "Product of velocity × density", "uom": "kg/m2/s"},
    {"name": "gamma ray", "aliases": ["gr", "gamma", "gamma_ray", "sgr", "cgr"], "description": "Natural gamma radiation log", "uom": "API"},
    {"name": "shale volume", "aliases": ["vsh", "vphyl", "vclay", "vshale"], "description": "Volume fraction of shale/clay", "uom": "v/v"},
    {"name": "facies", "aliases": ["facies", "lithology", "lith"], "description": "Discrete rock type classification", "uom": "unitless"},
    {"name": "zone", "aliases": ["zone", "region", "fipnum", "fipzon", "eqlnum", "pvtnum", "satnum", "multnum"], "description": "Integer zone/region identifier", "uom": "unitless"},
    {"name": "fault block", "aliases": ["faultblock", "fault_block", "faultblk"], "description": "Fault-bounded compartment index", "uom": "unitless"},
    {"name": "free water level", "aliases": ["fwl", "fwl_wg", "owc", "goc"], "description": "Oil/Water or Gas/Oil contact depth", "uom": "m"},
    {"name": "relative permeability", "aliases": ["krw", "kro", "krg", "krel"], "description": "Relative permeability curves", "uom": "fraction"},
    {"name": "capillary pressure", "aliases": ["pc", "pcow", "pcgo"], "description": "Capillary pressure", "uom": "bar"},
]

# RESQML 2.0 object types (most common in OSDU/RDDMS)
_RESQML_TYPES = [
    {"name": "resqml20.obj_IjkGridRepresentation", "short": "IjkGrid", "category": "Grid", "description": "3D geocellular grid (corner-point or parametric)"},
    {"name": "resqml20.obj_Grid2dRepresentation", "short": "Grid2D", "category": "Surface", "description": "2D regular grid (depth/time surface maps)"},
    {"name": "resqml20.obj_PolylineSetRepresentation", "short": "PolylineSet", "category": "Surface", "description": "Fault traces, contour lines, polygon boundaries"},
    {"name": "resqml20.obj_PointSetRepresentation", "short": "PointSet", "category": "Surface", "description": "Scattered point cloud (e.g. well picks, seismic picks)"},
    {"name": "resqml20.obj_WellboreFeature", "short": "WellboreFeature", "category": "Well", "description": "Well identity (top-level wellbore)"},
    {"name": "resqml20.obj_WellboreInterpretation", "short": "WellboreInterp", "category": "Well", "description": "Geological interpretation of a wellbore"},
    {"name": "resqml20.obj_WellboreTrajectoryRepresentation", "short": "Trajectory", "category": "Well", "description": "Well path in 3D space (MD, inclination, azimuth)"},
    {"name": "resqml20.obj_DeviationSurveyRepresentation", "short": "DeviationSurvey", "category": "Well", "description": "Measured deviation survey data"},
    {"name": "resqml20.obj_WellboreFrameRepresentation", "short": "WellFrame", "category": "Well", "description": "Sampling frame for well logs (MD stations)"},
    {"name": "resqml20.obj_WellboreMarkerFrameRepresentation", "short": "WellMarkers", "category": "Well", "description": "Formation tops / horizon picks along wellbore"},
    {"name": "resqml20.obj_MdDatum", "short": "MdDatum", "category": "Well", "description": "Measured depth reference point (kelly bushing, etc.)"},
    {"name": "resqml20.obj_ContinuousProperty", "short": "ContinuousProp", "category": "Property", "description": "Floating-point values (porosity, perm, sat, etc.)"},
    {"name": "resqml20.obj_DiscreteProperty", "short": "DiscreteProp", "category": "Property", "description": "Integer values (facies, zone, region, etc.)"},
    {"name": "resqml20.obj_HorizonInterpretation", "short": "HorizonInterp", "category": "Stratigraphy", "description": "Geological interpretation of a horizon boundary"},
    {"name": "resqml20.obj_FaultInterpretation", "short": "FaultInterp", "category": "Stratigraphy", "description": "Geological interpretation of a fault"},
    {"name": "resqml20.obj_GeneticBoundaryFeature", "short": "GeneticBoundary", "category": "Stratigraphy", "description": "Horizon or unconformity as a geological feature"},
    {"name": "resqml20.obj_TectonicBoundaryFeature", "short": "TectonicBoundary", "category": "Stratigraphy", "description": "Fault as a geological feature"},
    {"name": "resqml20.obj_StratigraphicColumn", "short": "StratColumn", "category": "Stratigraphy", "description": "Ordered set of stratigraphic units"},
    {"name": "resqml20.obj_StratigraphicColumnRankInterpretation", "short": "StratRank", "category": "Stratigraphy", "description": "Ranked stratigraphic units (formations, groups)"},
    {"name": "resqml20.obj_StratigraphicUnitFeature", "short": "StratUnit", "category": "Stratigraphy", "description": "Named geological time unit (formation)"},
    {"name": "resqml20.obj_StratigraphicUnitInterpretation", "short": "StratUnitInterp", "category": "Stratigraphy", "description": "Interpretation of a stratigraphic unit"},
    {"name": "resqml20.obj_OrganizationFeature", "short": "OrgFeature", "category": "Organization", "description": "Structural/stratigraphic organization"},
    {"name": "resqml20.obj_GridConnectionSetRepresentation", "short": "GridConnSet", "category": "Grid", "description": "Non-neighbour connections between grid cells (faults)"},
    {"name": "resqml20.obj_LocalDepth3dCrs", "short": "DepthCRS", "category": "CRS", "description": "Local coordinate reference system (depth)"},
    {"name": "resqml20.obj_LocalTime3dCrs", "short": "TimeCRS", "category": "CRS", "description": "Local coordinate reference system (time)"},
    {"name": "resqml20.obj_PropertyKind", "short": "PropertyKind", "category": "Property", "description": "Custom property kind definition"},
    {"name": "resqml20.obj_Activity", "short": "Activity", "category": "Provenance", "description": "Workflow activity that created/modified objects"},
    {"name": "resqml20.obj_ActivityTemplate", "short": "ActivityTemplate", "category": "Provenance", "description": "Template for activity parameterization"},
    {"name": "eml20.obj_EpcExternalPartReference", "short": "EpcExtPart", "category": "Container", "description": "Reference to external HDF5 data file"},
]

# Comparison operators
_OPERATORS = [
    {"value": "GT", "label": "> (greater than)", "symbol": ">"},
    {"value": "GTE", "label": "≥ (greater or equal)", "symbol": "≥"},
    {"value": "LT", "label": "< (less than)", "symbol": "<"},
    {"value": "LTE", "label": "≤ (less or equal)", "symbol": "≤"},
    {"value": "EQ", "label": "= (equal)", "symbol": "="},
]

# Build alias → canonical lookup
_ALIAS_TO_CANONICAL: Dict[str, str] = {}
for _pk in _STANDARD_PROPERTY_KINDS:
    for _a in _pk["aliases"]:
        _ALIAS_TO_CANONICAL[_a.lower()] = _pk["name"]
    _ALIAS_TO_CANONICAL[_pk["name"].lower()] = _pk["name"]


@router.get("/api/graphql/reference")
async def graphql_reference():
    """
    Return reference data for the query builder:
    - Standard RESQML property kinds with aliases
    - RESQML object types with categories
    - Comparison operators
    - Alias → canonical name lookup
    """
    return JSONResponse({
        "propertyKinds": _STANDARD_PROPERTY_KINDS,
        "resqmlTypes": _RESQML_TYPES,
        "operators": _OPERATORS,
        "aliasMap": _ALIAS_TO_CANONICAL,
    })


@router.get("/api/graphql/resolve-alias")
async def graphql_resolve_alias(term: str = ""):
    """
    Resolve a property term to canonical name(s).
    Supports loose matching: 'poro' → porosity, 'sw' → water saturation
    """
    term_lower = term.lower().strip()
    if not term_lower:
        return JSONResponse({"matches": [], "mode": "empty"})

    # Exact alias match
    if term_lower in _ALIAS_TO_CANONICAL:
        canonical = _ALIAS_TO_CANONICAL[term_lower]
        pk = next((p for p in _STANDARD_PROPERTY_KINDS if p["name"] == canonical), None)
        return JSONResponse({"matches": [pk] if pk else [], "mode": "exact"})

    # Substring match across aliases and names
    matches = []
    for pk in _STANDARD_PROPERTY_KINDS:
        if term_lower in pk["name"].lower():
            matches.append(pk)
        elif any(term_lower in a for a in pk["aliases"]):
            matches.append(pk)
    return JSONResponse({"matches": matches, "mode": "fuzzy"})

