"""Shared helpers for seisint demo scripts.

Provides:
- stable_uuid()        deterministic UUID5 from a namespace + name
- wpc_id / md_id       OSDU ID builders
- acl_block / legal_block  default ACL / legal stubs
- bearing_to_offsets / offsets_to_bearing   grid geometry conversion
- abcd_corners         compute A B C D from origin, bearing, width, count
- save_json            write dict → pretty JSON file
"""
from __future__ import annotations

import json
import math
import uuid
from pathlib import Path
from typing import Any, Dict, Tuple

# ── Namespace for deterministic UUIDs ───────────────────────────────────
NS_SEISINT = uuid.UUID("d1e2f3a4-b5c6-7890-abcd-ef0123456789")


def stable_uuid(name: str) -> str:
    """UUID5 from a fixed namespace — same name always gives same UUID."""
    return str(uuid.uuid5(NS_SEISINT, name))


# ── ID builders ─────────────────────────────────────────────────────────
def wpc_id(prefix: str, entity: str, uid: str) -> str:
    """Build work-product-component ID.

    >>> wpc_id("dev", "StructureMap", "aabb...")
    'dev:work-product-component--StructureMap:aabb...:1'
    """
    return f"{prefix}:work-product-component--{entity}:{uid}:1"


def md_id(prefix: str, entity: str, uid: str) -> str:
    """Build master-data ID."""
    return f"{prefix}:master-data--{entity}:{uid}:1"


def ds_id(prefix: str, entity: str, uid: str) -> str:
    """Build dataset ID."""
    return f"{prefix}:dataset--{entity}:{uid}:1"


# ── Default ACL / Legal ────────────────────────────────────────────────
DEFAULT_OWNERS  = ["data.default.owners@dev.dataservices.energy"]
DEFAULT_VIEWERS = ["data.office.global.viewers@dev.dataservices.energy"]
DEFAULT_LEGAL   = ["dev-equinor-private-default"]
DEFAULT_COUNTRY = ["NO"]


def acl_block() -> Dict[str, Any]:
    return {"owners": DEFAULT_OWNERS[:], "viewers": DEFAULT_VIEWERS[:]}


def legal_block() -> Dict[str, Any]:
    return {"legaltags": DEFAULT_LEGAL[:], "otherRelevantDataCountries": DEFAULT_COUNTRY[:]}


# ── Grid geometry helpers ───────────────────────────────────────────────
def bearing_to_offsets(bearing_deg: float, bin_width: float) -> Tuple[float, float]:
    """Convert compass bearing (°CW from north) + width → (dX, dY) offset.

    Returns (dEasting, dNorthing) per node step.
    """
    rad = math.radians(bearing_deg)
    dx = bin_width * math.sin(rad)
    dy = bin_width * math.cos(rad)
    return (round(dx, 6), round(dy, 6))


def offsets_to_bearing(dx: float, dy: float) -> Tuple[float, float]:
    """Convert (dEasting, dNorthing) → (bearing_deg, width)."""
    width = math.sqrt(dx * dx + dy * dy)
    bearing = math.degrees(math.atan2(dx, dy)) % 360
    return (round(bearing, 6), round(width, 6))


def abcd_corners(
    origin_e: float, origin_n: float,
    bearing_i: float, width_i: float, count_i: int,
    bearing_j: float, width_j: float, count_j: int,
) -> Dict[str, Dict[str, float]]:
    """Compute ABCD corner coordinates from grid parameters.

    A = origin (i=0, j=0)
    B = end of I axis (i=max, j=0)
    C = far corner (i=max, j=max)
    D = end of J axis (i=0, j=max)
    """
    di = bearing_to_offsets(bearing_i, width_i)
    dj = bearing_to_offsets(bearing_j, width_j)
    ni, nj = count_i - 1, count_j - 1

    a = (origin_e, origin_n)
    b = (origin_e + ni * di[0], origin_n + ni * di[1])
    c = (origin_e + ni * di[0] + nj * dj[0], origin_n + ni * di[1] + nj * dj[1])
    d = (origin_e + nj * dj[0], origin_n + nj * dj[1])

    def pt(xy: Tuple[float, float]) -> Dict[str, float]:
        return {"Easting": round(xy[0], 2), "Northing": round(xy[1], 2)}

    return {"A": pt(a), "B": pt(b), "C": pt(c), "D": pt(d)}


# ── JSON writer ─────────────────────────────────────────────────────────
def save_json(data: Any, path: str | Path) -> None:
    p = Path(path)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  Written → {p}")


# ── .env loader (shared with drogon pattern) ────────────────────────────
from typing import List, Optional  # noqa: E402 (already imported Tuple above)

def parse_dotenv(path: Path) -> Dict[str, str]:
    """Parse a KEY=VALUE .env file into a dict, stripping quotes."""
    vals: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
            v = v[1:-1]
        vals[k] = v
    return vals


def first_env(env: Dict[str, str], keys: list) -> Optional[str]:
    """Return first non-empty value for any of *keys* in *env*."""
    for k in keys:
        v = (env.get(k) or "").strip()
        if v:
            return v
    return None


def load_env(paths: list) -> Dict[str, str]:
    """Merge one or more .env files and return normalised auth/host dict.

    Keys returned: refresh_token, tenant, client_id, scope, host, partition.
    """
    merged: Dict[str, str] = {}
    for p in paths:
        fp = Path(p).expanduser().resolve()
        if not fp.exists():
            raise SystemExit(f"env file not found: {p}")
        merged.update(parse_dotenv(fp))

    env: Dict[str, str] = {}
    env["refresh_token"] = first_env(merged, ["refresh_token", "REFRESH_TOKEN"]) or ""
    env["tenant"]        = first_env(merged, ["OSDU_TENANT_ID", "AZURE_TENANT_ID"]) or ""
    env["client_id"]     = first_env(merged, ["OSDU_CLIENT_ID", "AZURE_CLIENT_ID"]) or ""
    env["scope"]         = first_env(merged, ["OSDU_SCOPE", "AZURE_SCOPE"]) or ""
    host                 = first_env(merged, ["OSDU_HOST", "OSDU_BASE_URL"]) or ""
    if host and not host.startswith("http"):
        host = "https://" + host.lstrip("/")
    env["host"]          = host
    env["partition"]     = first_env(merged, ["OSDU_PARTITION", "DATA_PARTITION_ID"]) or ""

    missing = [k for k in ("refresh_token", "tenant", "client_id", "scope", "host", "partition") if not env[k]]
    if missing:
        raise SystemExit(f"Missing keys in .env: {', '.join(missing)}")
    return env
