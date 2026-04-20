"""
app/common.py — Shared helpers used across app modules.

Consolidates duplicated utilities (access_token extraction,
reservoir search) into a single importable location.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

import httpx
from fastapi import HTTPException, Request

from . import osdu
from .cache import cached_call

log = logging.getLogger("rddms-admin.common")


def access_token(request: Request) -> str:
    """Extract the access token set by the auth middleware.

    Raises 401 if no token is available — use only in routes
    that require authentication (not in public paths).
    """
    at = getattr(request.state, "access_token", None)
    if not at:
        raise HTTPException(401, "Authentication required")
    return at


async def search_reservoirs(
    at: str,
    query: str = "*",
    limit: int = 50,
) -> List[Dict[str, str]]:
    """Search for Reservoir master-data records, de-dupe, and fetch names.

    Uses parallel storage-record fetches to resolve human-readable names.
    Returns ``[{id, name, version}]`` sorted by name.  Cached 90 s.
    """
    async def _do_search(at_: str, query_: str, limit_: int) -> List[Dict[str, str]]:
        search_url = f"https://{osdu.OSDU_BASE_URL}/api/search/v2/query"
        storage_url = f"https://{osdu.OSDU_BASE_URL}/api/storage/v2/records"
        hdr = osdu.headers(at_)

        payload = {
            "kind": "osdu:wks:master-data--Reservoir:2.0.0",
            "query": query_,
            "limit": min(int(limit_), 100),
            "returnedFields": ["id", "kind", "version"],
            "trackTotalCount": True,
        }

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(search_url, headers=hdr, json=payload)
                r.raise_for_status()
                results = r.json().get("results", [])

                # De-dupe by base ID (strip version suffix), keep highest version
                best: Dict[str, Dict[str, Any]] = {}
                for rec in results:
                    rid = rec.get("id", "")
                    if not rid:
                        continue
                    parts = rid.rsplit(":", 1)
                    base = parts[0] if len(parts) == 2 and parts[1].isdigit() else rid
                    ver = int(rec.get("version") or 0)
                    existing = best.get(base)
                    if existing is None or ver > int(existing.get("version") or 0):
                        best[base] = {**rec, "version": str(ver)}

                # Parallel fetch names
                async def _fetch_name(rec: Dict[str, Any]) -> Dict[str, str]:
                    rid = rec.get("id", "")
                    name = rid
                    try:
                        rf = await client.get(f"{storage_url}/{rid}", headers=hdr)
                        if rf.status_code == 200:
                            d = (rf.json() or {}).get("data", {}) or {}
                            name = d.get("Name") or d.get("Description") or rid
                    except Exception:
                        pass
                    return {"id": rid, "name": name, "version": rec.get("version", "")}

                out = list(await asyncio.gather(*[_fetch_name(rec) for rec in best.values()]))
                return sorted(out, key=lambda x: x.get("name", ""))

        except Exception as e:
            log.warning("Reservoir search failed: %s", e)
            return []

    cache_key = f"search_reservoirs:{query}:{limit}"
    return await cached_call(cache_key, 90, _do_search, at, query, limit)
