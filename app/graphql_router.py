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
            dims = [int(r[f"dim{i}"]) for i in range(1, 5) if r[f"dim{i}"] and r[f"dim{i}"] > 0]
            total = 1
            for d in dims:
                total *= d
            results.append({
                "ary_id": int(r["id"]),
                "path": r["path"],
                "type": int(r["type"]),
                "rank": int(r["rank1"]),
                "dimensions": dims,
                "total_elements": total,
                "usize": int(r["usize"]),
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
        fmt_char = _ARY_TYPE_FMT.get(ary["type"], "d")
        elem_size = struct.calcsize(fmt_char)
        n_elements = len(raw) // elem_size
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
            "Works via REST API (no direct PG needed)."
        )
    )
    async def deep_search(
        self,
        info: Info,
        dataspace: str,
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
          1. List objects of type_name in dataspace
          2. For each, find attached properties via sources (reverse graph)
          3. Filter by property kind
          4. Optionally load array values and apply threshold filter

        Example: Grid2D with Porosity > 0.2
        """
        pool = await _get_pool()
        if pool:
            return await _deep_search_pg(
                pool, dataspace, type_name, title_contains,
                property_filter, include_statistics, include_sample_values,
                sample_size, limit,
            )

        token = _get_token_from_info(info)
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
