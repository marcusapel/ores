"""Shared helpers for seisint demo scripts.

Provides:
- stable_uuid()        deterministic UUID5 from a namespace + name
- wpc_id / md_id       OSDU ID builders
- acl_block / legal_block  default ACL / legal stubs
- bearing_to_offsets / offsets_to_bearing   grid geometry conversion
- abcd_corners         compute A B C D from origin, bearing, width, count
- save_json            write dict → pretty JSON file

Auth functions (parse_dotenv, load_env, mint_from_env) are provided by the
central ``demo/_auth.py`` module and re-exported here for convenience.
"""
from __future__ import annotations

import json
import math
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── Re-export auth helpers from central module ──────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _auth import parse_dotenv, load_env, mint_from_env as get_access_token  # noqa: E402,F401

# ── Namespace for deterministic UUIDs ───────────────────────────────────
NS_SEISINT = uuid.UUID("d1e2f3a4-b5c6-7890-abcd-ef0123456789")


def stable_uuid(name: str) -> str:
    """UUID5 from a fixed namespace - same name always gives same UUID."""
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


# ── .env loader (delegated to demo/_auth.py - re-exported above) ────────
