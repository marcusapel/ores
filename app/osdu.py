from __future__ import annotations

import asyncio
import io
import math
import os
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
import urllib.parse
import httpx

from .cache import cached_call

log = logging.getLogger("rddms-admin.osdu")

# ── Global concurrency limiter ───────────────────────────────────────────────
# Limits how many simultaneous HTTP requests the app sends to external APIs.
# Prevents saturating the OSDU backend during fan-out operations like search
# enrichment.  Default 20; override via OSDU_MAX_CONCURRENT env var.
_MAX_CONCURRENT = int(os.getenv("OSDU_MAX_CONCURRENT", "20"))
API_SEMAPHORE = asyncio.Semaphore(_MAX_CONCURRENT)

# ----------------------------------------------------------------------
# Environment & defaults
#
# NOTE: These module-level globals are overwritten by instances.py
#       _apply_instance() at startup and on every instance switch.
#       Initial values are populated from env as a safe fallback.
# ----------------------------------------------------------------------

# Base DNS name of your ADME/OSDU instance (no scheme).
OSDU_BASE_URL: str = os.getenv("OSDU_BASE_URL", "")

# Required header for all ADME/OSDU calls.
DATA_PARTITION_ID: str = os.getenv("DATA_PARTITION_ID", "").strip()

def _partition_suffix() -> str:
    """E.g. 'dev.dataservices.energy'.  Returns empty string when unset."""
    return f"{DATA_PARTITION_ID}.dataservices.energy" if DATA_PARTITION_ID else ""

# Sensible defaults for the "Create Dataspace" form (can be overridden in env)
DEFAULT_LEGAL_TAG: str = os.getenv(
    "DEFAULT_LEGAL_TAG",
    f"{DATA_PARTITION_ID}-equinor-private-default" if DATA_PARTITION_ID else "dev-equinor-private-default",
)

_default_owners = os.getenv("DEFAULT_OWNERS", f"data.default.owners@{_partition_suffix()}" if _partition_suffix() else "")
DEFAULT_OWNERS: list[str] = [x.strip() for x in _default_owners.split(",") if x.strip()]

_default_viewers = os.getenv("DEFAULT_VIEWERS", f"data.default.viewers@{_partition_suffix()}" if _partition_suffix() else "")
DEFAULT_VIEWERS: list[str] = [x.strip() for x in _default_viewers.split(",") if x.strip()]

_default_countries = os.getenv("DEFAULT_COUNTRIES", "NO")
DEFAULT_COUNTRIES: list[str] = [x.strip() for x in _default_countries.split(",") if x.strip()]

# ----------------------------------------------------------------------
# HTTP helpers
# ----------------------------------------------------------------------

# Module-level shared client (created lazily, reused across calls)
_shared_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def _http(timeout: float = 60) -> AsyncIterator[httpx.AsyncClient]:
    """Yield a shared :class:`httpx.AsyncClient`.

    Re-uses a module-level client so TCP connections are pooled across
    calls instead of opening a fresh connection per request.
    The *timeout* is applied per-request via the client, not at creation
    time, so callers that need longer deadlines get them.
    """
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(timeout=timeout)
    else:
        # Update timeout for this call if different from the client default
        _shared_client.timeout = httpx.Timeout(timeout)
    yield _shared_client


async def close_shared_client() -> None:
    """Close the module-level HTTP client. Called on app shutdown (#9)."""
    global _shared_client
    if _shared_client is not None and not _shared_client.is_closed:
        await _shared_client.aclose()
        _shared_client = None
        log.info("Shared httpx client closed")


def _rddms_url(path: str = "") -> str:
    """Build a Reservoir-DDMS v2 URL.  *path* is appended after the base."""
    return f"https://{OSDU_BASE_URL}/api/reservoir-ddms/v2{path}"


def headers(access_token: str) -> dict[str, str]:
    if not DATA_PARTITION_ID:
        log.warning("DATA_PARTITION_ID env var is not set; calls may fail")
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "data-partition-id": DATA_PARTITION_ID,
    }

# ----------------------------------------------------------------------
# Dataspaces
# ----------------------------------------------------------------------

async def list_dataspaces(access_token: str) -> list[dict[str, Any]]:
    """GET /api/reservoir-ddms/v2/dataspaces  (cached 600 s)"""
    async def _fetch(at: str) -> list[dict[str, Any]]:
        async with _http() as client:
            r = await client.get(_rddms_url("/dataspaces"), headers=headers(at))
            r.raise_for_status()
            return r.json() or []
    return await cached_call("list_dataspaces", 600, _fetch, access_token)

async def create_dataspace(
    access_token: str,
    path: str,
    *,
    legal_tag: str,
    owners: list[str],
    viewers: list[str],
    countries: list[str],
    extra_custom: dict[str, Any] | None = None,
) -> Any:
    """POST /api/reservoir-ddms/v2/dataspaces"""
    url = _rddms_url("/dataspaces")

    custom: dict[str, Any] = {
        "legaltags": [legal_tag],
        "otherRelevantDataCountries": countries,
        "viewers": viewers,
        "owners": owners,
    }
    if extra_custom:
        # Do not let extra keys override reserved compliance ACL fields
        for k in ("legaltags", "otherRelevantDataCountries", "viewers", "owners"):
            extra_custom.pop(k, None)
        custom.update(extra_custom)

    payload = [
        {
            "DataspaceId": path,
            "Path": path,
            "CustomData": custom,
        }
    ]

    hdr = headers(access_token)
    async with _http() as client:
        r = await client.post(url, headers=hdr, json=payload)

    try:
        r.raise_for_status()
    except httpx.HTTPStatusError:
        corr = r.headers.get("x-correlation-id") or r.headers.get("x-request-id")
        log.error(
            "Dataspace create failed (%s) corr=%s\nURL=%s\nPayload=%s\nBody=%s",
            r.status_code, corr, url, json.dumps(payload, indent=2), r.text,
        )
        raise
    return r.json()

# ----------------------------------------------------------------------
# Types & resources
# ----------------------------------------------------------------------

async def list_types(access_token: str, ds_enc: str) -> list[dict[str, Any]]:
    """GET /dataspaces/{dataspaceId}/resources -> list of {'name','count'}"""
    async with _http() as client:
        r = await client.get(_rddms_url(f"/dataspaces/{ds_enc}/resources"), headers=headers(access_token))
        r.raise_for_status()
        return r.json() or []

async def list_resources(access_token: str, ds_enc: str, typ: str) -> list[dict[str, Any]]:
    """GET /dataspaces/{dataspaceId}/resources/{dataObjectType}"""
    async with _http() as client:
        r = await client.get(_rddms_url(f"/dataspaces/{ds_enc}/resources/{typ}"), headers=headers(access_token))
        r.raise_for_status()
        return r.json() or []

async def get_resource(
    access_token: str,
    ds_enc: str,
    typ: str,
    uuid: str,
    *,
    as_json: bool = True,
) -> dict[str, Any]:
    """GET /dataspaces/{dataspaceId}/resources/{dataObjectType}/{guid}

    By default requests ``$format=json`` so the RDDMS returns JSON
    instead of XML.
    """
    params: dict[str, str] = {}
    if as_json:
        params["$format"] = "json"
    async with _http() as client:
        r = await client.get(
            _rddms_url(f"/dataspaces/{ds_enc}/resources/{typ}/{uuid}"),
            headers=headers(access_token), params=params,
        )
        r.raise_for_status()
        return r.json() or {}

async def list_arrays(access_token: str, ds_enc: str, typ: str, uuid: str) -> list[dict[str, Any]]:
    """GET arrays metadata list for an object."""
    async with _http() as client:
        r = await client.get(
            _rddms_url(f"/dataspaces/{ds_enc}/resources/{typ}/{uuid}/arrays"),
            headers=headers(access_token),
        )
        r.raise_for_status()
        return r.json() or []

async def read_array(
    access_token: str,
    ds_enc: str,
    typ: str,
    uuid: str,
    *,
    path_in_resource: str,
) -> dict[str, Any]:
    """GET content of an array."""
    async with _http() as client:
        r = await client.get(
            _rddms_url(f"/dataspaces/{ds_enc}/resources/{typ}/{uuid}/arrays/{path_in_resource}"),
            headers=headers(access_token),
        )
        r.raise_for_status()
        return r.json() or {}

# ----------------------------------------------------------------------
# Helpers for UI features
# ----------------------------------------------------------------------

def extract_refs(obj: dict[str, Any]) -> list[dict[str, str]]:
    """Very lightweight scan for DataObjectReference-like dicts."""
    edges: list[dict[str, str]] = []

    def _walk(x: Any):
        if isinstance(x, dict):
            ct = x.get("ContentType")
            uid = x.get("UUID") or x.get("Uuid")
            if ct and uid:
                edges.append({"contentType": ct, "uuid": str(uid)})
            for v in x.values():
                _walk(v)
        elif isinstance(x, list):
            for v in x:
                _walk(v)

    _walk(obj)
    return edges


async def lock_dataspace(access_token: str, path: str) -> None:
    """POST /api/reservoir-ddms/v2/dataspaces/{dataspaceId}/lock"""
    enc = urllib.parse.quote(path, safe="")
    hdr = headers(access_token)
    async with _http() as client:
        r = await client.post(_rddms_url(f"/dataspaces/{enc}/lock"), headers=hdr)
    try:
        r.raise_for_status()
    except httpx.HTTPStatusError:
        log.error("Dataspace lock failed (%s) path=%s body=%s", r.status_code, path, r.text)
        raise

async def unlock_dataspace(access_token: str, path: str) -> None:
    """DELETE /api/reservoir-ddms/v2/dataspaces/{dataspaceId}/lock"""
    enc = urllib.parse.quote(path, safe="")
    hdr = headers(access_token)
    async with _http() as client:
        r = await client.delete(_rddms_url(f"/dataspaces/{enc}/lock"), headers=hdr)
    try:
        r.raise_for_status()
    except httpx.HTTPStatusError:
        log.error("Dataspace unlock failed (%s) path=%s body=%s", r.status_code, path, r.text)
        raise

async def delete_dataspace(access_token: str, path: str) -> None:
    """DELETE /api/reservoir-ddms/v2/dataspaces/{dataspaceId}"""
    enc = urllib.parse.quote(path, safe="")
    hdr = headers(access_token)
    async with _http() as client:
        r = await client.delete(_rddms_url(f"/dataspaces/{enc}"), headers=hdr)
    try:
        r.raise_for_status()
    except httpx.HTTPStatusError:
        log.error("Dataspace delete failed (%s) path=%s body=%s", r.status_code, path, r.text)
        raise


def _dataspace_uri(path: str) -> str:
    """Canonical EML dataspace URI."""
    return f"eml:///dataspace('{path}')"


def _eml_uri_from_parts(path: str, typ: str, uuid: str) -> str:
    """Canonical EML URI fallback if object lacks 'uri'."""
    return f"eml:///dataspace('{path}')/{typ}('{uuid}')"


async def build_manifest_for_uris(
    access_token: str,
    uris: list[str],
    *,
    legal_tag: str | None = None,
    owners: list[str] | None = None,
    viewers: list[str] | None = None,
    countries: list[str] | None = None,
    create_missing_refs: bool = True,
) -> dict:
    """POST /api/reservoir-ddms/v2/manifests/build for arbitrary URIs.

    Pass a single ``eml:///dataspace('...')`` URI to build a whole-dataspace
    manifest, or multiple object URIs for a targeted build.
    """
    hdr = headers(access_token)
    legal_tag = legal_tag or DEFAULT_LEGAL_TAG
    owners = owners or DEFAULT_OWNERS
    viewers = viewers or DEFAULT_VIEWERS
    countries = countries or DEFAULT_COUNTRIES
    body = {
        "uris": list(uris),
        "acl": {"owners": owners, "viewers": viewers},
        "legal": {"legaltags": [legal_tag], "otherRelevantDataCountries": countries},
        "createMissingReferences": bool(create_missing_refs),
    }
    async with _http(timeout=120) as client:
        r = await client.post(_rddms_url("/manifests/build"), headers=hdr, json=body)
    try:
        r.raise_for_status()
    except httpx.HTTPStatusError:
        log.error("Build manifest failed (%s) uris=%s body=%s", r.status_code, uris[:3], r.text[:2000])
        raise
    return r.json() or {}


async def build_manifest(
    access_token: str,
    path: str,
    *,
    legal_tag: str | None = None,
    owners: list[str] | None = None,
    viewers: list[str] | None = None,
    countries: list[str] | None = None,
    create_missing_refs: bool = True,
) -> dict:
    """Convenience wrapper: build manifest for an entire dataspace."""
    return await build_manifest_for_uris(
        access_token,
        [_dataspace_uri(path)],
        legal_tag=legal_tag,
        owners=owners,
        viewers=viewers,
        countries=countries,
        create_missing_refs=create_missing_refs,
    )


async def list_all_resources(access_token: str, ds_enc: str) -> list[dict]:
    """GET /dataspaces/{dataspaceId}/resources/all"""
    async with _http(timeout=90) as client:
        r = await client.get(_rddms_url(f"/dataspaces/{ds_enc}/resources/all"), headers=headers(access_token))
        r.raise_for_status()
        return r.json() or []

async def list_sources(access_token: str, ds_enc: str, typ: str, uuid: str) -> list[dict]:
    """GET /dataspaces/{dataspaceId}/resources/{type}/{uuid}/sources"""
    async with _http(timeout=90) as client:
        r = await client.get(
            _rddms_url(f"/dataspaces/{ds_enc}/resources/{typ}/{uuid}/sources"),
            headers=headers(access_token),
        )
        r.raise_for_status()
        return r.json() or []

async def list_targets(access_token: str, ds_enc: str, typ: str, uuid: str) -> list[dict]:
    """GET /dataspaces/{dataspaceId}/resources/{type}/{uuid}/targets"""
    async with _http(timeout=90) as client:
        r = await client.get(
            _rddms_url(f"/dataspaces/{ds_enc}/resources/{typ}/{uuid}/targets"),
            headers=headers(access_token),
        )
        r.raise_for_status()
        return r.json() or []

# ── Transactions ──────────────────────────────────────────────────────

async def begin_transaction(access_token: str, ds_path: str) -> str:
    """POST /dataspaces/{dataspaceId}/transactions → transaction ID."""
    enc = urllib.parse.quote(ds_path, safe="")
    hdr = headers(access_token)
    async with _http() as client:
        r = await client.post(_rddms_url(f"/dataspaces/{enc}/transactions"), headers=hdr)
    try:
        r.raise_for_status()
    except httpx.HTTPStatusError:
        log.error("Begin transaction failed (%s) ds=%s body=%s", r.status_code, ds_path, r.text[:2000])
        raise
    return r.text.strip().strip('"')


async def commit_transaction(access_token: str, ds_path: str, tx_id: str) -> None:
    """PUT /dataspaces/{dataspaceId}/transactions/{transactionId} → commit."""
    enc = urllib.parse.quote(ds_path, safe="")
    hdr = headers(access_token)
    async with _http(timeout=120) as client:
        r = await client.put(_rddms_url(f"/dataspaces/{enc}/transactions/{tx_id}"), headers=hdr)
    try:
        r.raise_for_status()
    except httpx.HTTPStatusError:
        log.error("Commit transaction failed (%s) ds=%s tx=%s body=%s",
                  r.status_code, ds_path, tx_id, r.text[:2000])
        raise


async def cancel_transaction(access_token: str, ds_path: str, tx_id: str) -> None:
    """DELETE /dataspaces/{dataspaceId}/transactions/{transactionId} → rollback."""
    enc = urllib.parse.quote(ds_path, safe="")
    hdr = headers(access_token)
    async with _http() as client:
        r = await client.delete(_rddms_url(f"/dataspaces/{enc}/transactions/{tx_id}"), headers=hdr)
    try:
        r.raise_for_status()
    except httpx.HTTPStatusError:
        log.warning("Cancel transaction failed (%s) ds=%s tx=%s", r.status_code, ds_path, tx_id)


# ── Write operations (within a transaction) ──────────────────────────

async def put_resources(
    access_token: str,
    ds_path: str,
    objects: list[dict],
    tx_id: str,
) -> dict:
    """PUT RESQML objects into a Reservoir DDMS v2 dataspace (transactional)."""
    enc = urllib.parse.quote(ds_path, safe="")
    hdr = headers(access_token)
    async with _http(timeout=120) as client:
        r = await client.put(
            _rddms_url(f"/dataspaces/{enc}/resources"),
            headers=hdr, json=objects, params={"transactionId": tx_id},
        )
    try:
        r.raise_for_status()
    except httpx.HTTPStatusError:
        log.error("PUT resources failed (%s) ds=%s tx=%s body=%s",
                  r.status_code, ds_path, tx_id, r.text[:2000])
        raise
    try:
        return r.json() or {}
    except Exception:
        return {"status": r.status_code, "text": r.text[:500]}


# ======================================================================
# Grid2dRepresentation - full surface fetch + CRS-aware PNG rendering
# ======================================================================

def _normalize_obj(raw: Any, uuid: str) -> dict[str, Any]:
    """Pick the right dict when the RDDMS returns a list.

    Warns when the exact UUID isn't found and a fallback is used.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        for it in raw:
            if isinstance(it, dict):
                uid = it.get("Uuid") or it.get("UUID") or it.get("uuid")
                if uid and str(uid).lower() == uuid.lower():
                    return it
        # Exact match failed - fall back to first dict (with warning)
        for it in raw:
            if isinstance(it, dict):
                log.warning("_normalize_obj: UUID %s not found, using first dict", uuid)
                return it
    return {}


async def fetch_grid2d_surface(
    access_token: str,
    ds: str,
    uuid: str,
) -> dict[str, Any]:
    """
    Fetch a Grid2dRepresentation object, its z-values array, and its
    referenced LocalDepth3dCrs - everything needed to render a map.

    Returns a dict with:
      grid     – the Grid2d RESQML object (dict)
      zvalues  – flat list[float] of depth values
      dims     – [n_slow, n_fast]
      crs      – the LocalDepth3dCrs object (dict) or None
      geometry – parsed lattice geometry dict (origin, offsets, spacing)
    """
    enc = urllib.parse.quote(ds, safe="")
    typ = "resqml20.obj_Grid2dRepresentation"
    hdr = headers(access_token)
    base_obj = _rddms_url(f"/dataspaces/{enc}/resources/{typ}/{uuid}")

    async with _http(timeout=120) as client:
        # 1. Fetch the Grid2d metadata
        r1 = await client.get(base_obj, headers=hdr)
        r1.raise_for_status()
        grid = _normalize_obj(r1.json(), uuid)

        # 2. Parse lattice geometry from Grid2dPatch
        patch = grid.get("Grid2dPatch") or {}
        n_fast = int(patch.get("FastestAxisCount", 0))
        n_slow = int(patch.get("SlowestAxisCount", 0))
        geom = patch.get("Geometry") or {}
        points = geom.get("Points") or {}

        # Handle Point3dZValueArray (z separate from x-y lattice)
        supporting = points.get("SupportingGeometry") or {}
        origin_d = supporting.get("Origin") or points.get("Origin") or {}
        offsets = supporting.get("Offset") or points.get("Offset") or []

        geometry = _parse_lattice(origin_d, offsets, n_slow, n_fast)

        # 3. Resolve CRS (may be inline via _data or need separate fetch)
        crs_ref = geom.get("LocalCrs") or {}
        crs = crs_ref.get("_data")  # RDDMS often inlines it
        if not crs:
            crs_uuid = crs_ref.get("UUID") or crs_ref.get("Uuid")
            if crs_uuid:
                ct = crs_ref.get("ContentType", "")
                crs_typ = "resqml20.obj_LocalTime3dCrs" if "LocalTime3dCrs" in ct else "resqml20.obj_LocalDepth3dCrs"
                try:
                    r_crs = await client.get(
                        _rddms_url(f"/dataspaces/{enc}/resources/{crs_typ}/{crs_uuid}"),
                        headers=hdr,
                    )
                    r_crs.raise_for_status()
                    crs = _normalize_obj(r_crs.json(), crs_uuid)
                except Exception as e:
                    log.warning("fetch_grid2d_surface: CRS fetch failed: %s", e)
                    crs = None

        # 4. Discover array path and fetch z-values
        r_al = await client.get(f"{base_obj}/arrays", headers=hdr)
        r_al.raise_for_status()
        arr_list = r_al.json() or []

        arr_path = ""
        for a in arr_list:
            uid = a.get("uid") or {}
            pir = uid.get("pathInResource", "")
            if "points_patch" in pir or "zvalues" in pir:
                arr_path = pir
                break
        if not arr_path and arr_list:
            arr_path = (arr_list[0].get("uid") or {}).get("pathInResource", "")

        zvalues: list[float] = []
        if arr_path:
            arr_enc = urllib.parse.quote(arr_path, safe="")
            r_arr = await client.get(f"{base_obj}/arrays/{arr_enc}", headers=hdr)
            r_arr.raise_for_status()
            arr_body = r_arr.json() or {}
            inner = arr_body.get("data") or arr_body
            if isinstance(inner, dict):
                zvalues = inner.get("data") or inner.get("values") or []
            elif isinstance(inner, list):
                zvalues = inner

    return {
        "grid": grid,
        "zvalues": zvalues,
        "dims": [n_slow, n_fast],
        "crs": crs,
        "geometry": geometry,
    }


def _parse_lattice(
    origin_d: dict[str, Any],
    offsets: list[dict[str, Any]],
    n_slow: int,
    n_fast: int,
) -> dict[str, Any]:
    """
    Parse a RESQML Point3dLatticeArray into a geometry dict.

    Returns:
      origin   – (x, y, z)
      u_vec    – (dx, dy) unit direction for slow axis
      v_vec    – (dx, dy) unit direction for fast axis
      u_space  – spacing along slow axis (metres)
      v_space  – spacing along fast axis (metres)
      n_slow, n_fast
    """
    ox = float(origin_d.get("Coordinate1", 0))
    oy = float(origin_d.get("Coordinate2", 0))
    oz = float(origin_d.get("Coordinate3", 0))

    def _offset_parts(off: dict[str, Any]) -> tuple[float, float, float]:
        o = off.get("Offset") or {}
        return (
            float(o.get("Coordinate1", 0)),
            float(o.get("Coordinate2", 0)),
            float(o.get("Coordinate3", 0)),
        )

    def _spacing(off: dict[str, Any]) -> float:
        s = off.get("Spacing") or {}
        return float(s.get("Value", 1.0))

    u_dx, u_dy, _ = (0.0, 0.0, 0.0)
    v_dx, v_dy, _ = (0.0, 0.0, 0.0)
    u_space = 1.0
    v_space = 1.0

    if len(offsets) >= 1:
        u_dx, u_dy, _ = _offset_parts(offsets[0])
        u_space = _spacing(offsets[0])
    if len(offsets) >= 2:
        v_dx, v_dy, _ = _offset_parts(offsets[1])
        v_space = _spacing(offsets[1])

    return {
        "origin": (ox, oy, oz),
        "u_vec": (u_dx, u_dy),
        "v_vec": (v_dx, v_dy),
        "u_space": u_space,
        "v_space": v_space,
        "n_slow": n_slow,
        "n_fast": n_fast,
    }


def _apply_crs_rotation(
    geometry: dict[str, Any],
    crs: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Apply the CRS ArealRotation + XOffset/YOffset to the geometry.

    RESQML LocalDepth3dCrs defines:
      - XOffset, YOffset   - translation of local origin w.r.t. projected CRS
      - ArealRotation      - counter-clockwise angle (degrees) from projected
                             CRS north to local CRS Y-axis

    The grid's origin and offset vectors are in local CRS coordinates.
    To map to projected coordinates:
      P_proj = R(θ) · P_local + (XOffset, YOffset)

    If ArealRotation is 0 and offsets are 0 (common case for RMS exports
    where rotation is baked into the offset vectors), this is a no-op.
    """
    if not crs:
        return geometry

    x_off = float(crs.get("XOffset", 0) or 0)
    y_off = float(crs.get("YOffset", 0) or 0)

    # ArealRotation
    rot_obj = crs.get("ArealRotation") or {}
    angle_deg = float(rot_obj.get("_", 0) or rot_obj.get("Value", 0) or 0)
    uom = (rot_obj.get("Uom") or "dega").lower()
    if "rad" in uom:
        angle_rad = angle_deg  # already radians
    else:
        angle_rad = math.radians(angle_deg)

    if abs(angle_rad) < 1e-12 and abs(x_off) < 1e-6 and abs(y_off) < 1e-6:
        return geometry  # nothing to do

    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)

    ox, oy, oz = geometry["origin"]
    # Rotate origin
    new_ox = cos_a * ox - sin_a * oy + x_off
    new_oy = sin_a * ox + cos_a * oy + y_off

    # Rotate offset vectors
    ux, uy = geometry["u_vec"]
    new_ux = cos_a * ux - sin_a * uy
    new_uy = sin_a * ux + cos_a * uy

    vx, vy = geometry["v_vec"]
    new_vx = cos_a * vx - sin_a * vy
    new_vy = sin_a * vx + cos_a * vy

    return {
        **geometry,
        "origin": (new_ox, new_oy, oz),
        "u_vec": (new_ux, new_uy),
        "v_vec": (new_vx, new_vy),
    }


def build_xy_mesh(
    geometry: dict[str, Any],
    crs: dict[str, Any] | None = None,
) -> tuple:
    """
    Build 2-D X and Y coordinate arrays (n_slow × n_fast) in projected CRS,
    correctly handling RESQML offset-vector rotation.

    Returns (X, Y) ndarrays suitable for matplotlib pcolormesh.
    """
    import numpy as np

    geo = _apply_crs_rotation(geometry, crs)

    ox, oy, _ = geo["origin"]
    ux, uy = geo["u_vec"]
    vx, vy = geo["v_vec"]
    u_sp = geo["u_space"]
    v_sp = geo["v_space"]
    n_slow = geo["n_slow"]
    n_fast = geo["n_fast"]

    # Row indices (slow axis) and col indices (fast axis)
    i = np.arange(n_slow, dtype=np.float64)
    j = np.arange(n_fast, dtype=np.float64)
    II, JJ = np.meshgrid(i, j, indexing="ij")  # shape (n_slow, n_fast)

    X = ox + II * (ux * u_sp) + JJ * (vx * v_sp)
    Y = oy + II * (uy * u_sp) + JJ * (vy * v_sp)
    return X, Y


def render_grid2d_png(
    zvalues: list[float],
    dims: list[int],
    geometry: dict[str, Any],
    crs: dict[str, Any] | None = None,
    *,
    title: str = "",
    cmap: str = "viridis_r",
    figsize: tuple[int, int] = (10, 8),
    dpi: int = 120,
    nan_sentinel: float = 1e30,
    unit: str = "m",
    show_crs_info: bool = True,
    max_render_dim: int = 500,
) -> bytes:
    """
    Render a Grid2dRepresentation depth surface as a PNG image.

    Handles:
      - RESQML offset-vector rotation (any angle)
      - CRS ArealRotation + XOffset/YOffset
      - Colour bar with depth range
      - UTM coordinate axes with grid lines
      - NaN masking
      - Auto-downsampling for grids larger than max_render_dim per axis

    Returns PNG bytes.
    """
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import Normalize
    from matplotlib.ticker import FuncFormatter

    n_slow, n_fast = dims[0], dims[1]
    total = n_slow * n_fast
    if len(zvalues) < total:
        # pad with NaN
        zvalues = list(zvalues) + [float("nan")] * (total - len(zvalues))

    Z = np.array(zvalues[:total], dtype=np.float64).reshape(n_slow, n_fast)
    # Mask sentinel and very large values
    Z[np.abs(Z) > nan_sentinel] = np.nan

    X, Y = build_xy_mesh(geometry, crs)

    # Downsample large grids to keep rendering fast
    step_i = max(1, n_slow // max_render_dim)
    step_j = max(1, n_fast // max_render_dim)
    if step_i > 1 or step_j > 1:
        log.info("render_grid2d_png: downsampling %dx%d → %dx%d (step %d×%d)",
                 n_slow, n_fast,
                 n_slow // step_i, n_fast // step_j,
                 step_i, step_j)
        Z = Z[::step_i, ::step_j]
        X = X[::step_i, ::step_j]
        Y = Y[::step_i, ::step_j]

    # Determine if Z should be negated (ZIncreasingDownward means positive Z is deeper)
    z_down = True
    if crs and crs.get("ZIncreasingDownward") is False:
        z_down = False
    # For color mapping: shallower = lighter, deeper = darker  (viridis_r default)
    Z_plot = Z.copy()

    fig, ax = plt.subplots(1, 1, figsize=figsize, dpi=dpi)

    # Use pcolormesh for irregular/rotated grids
    valid = np.isfinite(Z_plot)
    if valid.any():
        vmin = float(np.nanmin(Z_plot))
        vmax = float(np.nanmax(Z_plot))
    else:
        vmin, vmax = 0, 1

    pcm = ax.pcolormesh(X, Y, Z_plot, cmap=cmap, shading="auto",
                        norm=Normalize(vmin=vmin, vmax=vmax))

    # Color bar
    cbar = fig.colorbar(pcm, ax=ax, shrink=0.85, pad=0.02)
    depth_label = f"Depth ({unit})"
    if z_down:
        depth_label += " - increasing downward"
    cbar.set_label(depth_label, fontsize=10)

    # Axis labels
    ax.set_xlabel("Easting (m)", fontsize=10)
    ax.set_ylabel("Northing (m)", fontsize=10)
    ax.set_aspect("equal")
    ax.grid(True, alpha=0.3, linewidth=0.5)
    ax.tick_params(labelsize=8)

    # Format ticks as km if range > 5000
    x_range = X.max() - X.min()
    y_range = Y.max() - Y.min()

    def _fmt_km(val, _):
        return f"{val / 1000:.1f}"

    if x_range > 5000 or y_range > 5000:
        ax.xaxis.set_major_formatter(FuncFormatter(_fmt_km))
        ax.yaxis.set_major_formatter(FuncFormatter(_fmt_km))
        ax.set_xlabel("Easting (km)", fontsize=10)
        ax.set_ylabel("Northing (km)", fontsize=10)

    # Title
    if title:
        ax.set_title(title, fontsize=12, fontweight="bold")

    # CRS annotation
    if show_crs_info and crs:
        crs_title = (crs.get("Citation") or {}).get("Title", "")
        rot_obj = crs.get("ArealRotation") or {}
        rot_val = rot_obj.get("_", 0) or rot_obj.get("Value", 0) or 0
        # Check for WKT in ExtraMetadata
        wkt_short = ""
        for em in (crs.get("ExtraMetadata") or []):
            if isinstance(em, dict) and "Wkt" in (em.get("Name") or ""):
                wkt = em.get("Value", "")
                # Extract projection name
                import re as _re
                m = _re.search(r'PROJCS\["([^"]+)"', wkt)
                if m:
                    wkt_short = m.group(1)
                break
        info_parts = []
        if crs_title:
            info_parts.append(crs_title)
        if wkt_short:
            info_parts.append(wkt_short)
        if abs(float(rot_val)) > 0.001:
            info_parts.append(f"rot={rot_val}°")
        if info_parts:
            ax.annotate(
                " | ".join(info_parts),
                xy=(0.01, 0.01), xycoords="axes fraction",
                fontsize=7, color="gray", alpha=0.8,
            )

    # Compute rotation angle from offset vectors for annotation
    ux, uy = geometry.get("u_vec", (1, 0))
    angle = math.degrees(math.atan2(ux, uy))  # angle from north
    if abs(angle) > 0.1:
        ax.annotate(
            f"Grid rotation: {angle:.1f}° from N",
            xy=(0.99, 0.01), xycoords="axes fraction",
            fontsize=7, color="gray", alpha=0.8, ha="right",
        )

    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=dpi)
    plt.close(fig)
    buf.seek(0)
    return buf.read()
