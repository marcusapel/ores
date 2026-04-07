#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
list_rddms_surfaces.py — List Grid2dRepresentation objects in an RDDMS dataspace,
classify them by domain (depth/time), and show grid geometry.

This is the discovery step before generating OSDU StructureMap records.

Usage:
  python list_rddms_surfaces.py maap/drogon
  python list_rddms_surfaces.py maap/drogon --env-file ../../.env
  python list_rddms_surfaces.py maap/drogon --json
  python list_rddms_surfaces.py maap/drogon --all   # show all types, not just Grid2d
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent


# ── Auth ─────────────────────────────────────────────────────────────
def load_env_file(path: str) -> None:
    if not os.path.isfile(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def get_token(tenant: str, client_id: str, refresh_token: str, scope: str) -> str:
    r = requests.post(
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
            "scope": scope,
        },
    )
    r.raise_for_status()
    return r.json()["access_token"]


def env_params() -> Dict[str, str]:
    return {
        "host": os.getenv("OSDU_BASE_URL", os.getenv("OSDU_HOST", "")),
        "partition": os.getenv("DATA_PARTITION_ID", os.getenv("OSDU_PARTITION", "")),
        "tenant": os.getenv("AZURE_TENANT_ID", ""),
        "client_id": os.getenv("AZURE_CLIENT_ID", ""),
        "refresh_token": os.getenv("REFRESH_TOKEN", os.getenv("refresh_token", "")),
        "scope": os.getenv("AZURE_SCOPE", ""),
    }


# ── RDDMS REST API ──────────────────────────────────────────────────
def rddms_headers(token: str, partition: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "data-partition-id": partition,
    }


def list_resource_types(base: str, dataspace: str, headers: dict) -> List[dict]:
    enc = quote(dataspace, safe="")
    r = requests.get(f"{base}/dataspaces/{enc}/resources", headers=headers)
    r.raise_for_status()
    return r.json()


def list_resources(base: str, dataspace: str, resource_type: str, headers: dict) -> List[dict]:
    enc = quote(dataspace, safe="")
    r = requests.get(f"{base}/dataspaces/{enc}/resources/{resource_type}", headers=headers)
    r.raise_for_status()
    return r.json()


def get_object(base: str, dataspace: str, resource_type: str, uuid: str, headers: dict) -> Optional[dict]:
    """Get a single RESQML object by UUID (JSON representation)."""
    enc = quote(dataspace, safe="")
    r = requests.get(
        f"{base}/dataspaces/{enc}/resources/{resource_type}/{uuid}",
        headers=headers,
    )
    if r.status_code == 200:
        return r.json()
    return None


def extract_uuid(uri: str) -> Optional[str]:
    import re
    m = re.search(r"\(([0-9a-f-]{36})\)", uri)
    return m.group(1) if m else None


# ── Surface analysis ─────────────────────────────────────────────────
def classify_grid2d(obj: dict) -> Dict[str, Any]:
    """Extract key properties from a Grid2dRepresentation object."""
    info: Dict[str, Any] = {
        "uuid": obj.get("Uuid", obj.get("uuid", "")),
        "title": "",
        "surface_role": "",
        "fastest_axis": 0,
        "slowest_axis": 0,
        "domain": "unknown",
        "has_interpretation": False,
        "interpretation_uuid": "",
        "interpretation_type": "",
        "interpretation_title": "",
        "grid_type": "unknown",
        "supporting_rep_uuid": "",
    }

    # Citation
    citation = obj.get("Citation", {})
    info["title"] = citation.get("Title", obj.get("title", ""))

    # Surface role
    info["surface_role"] = obj.get("SurfaceRole", obj.get("surfaceRole", ""))

    # Axis counts
    info["fastest_axis"] = obj.get("FastestAxisCount", obj.get("fastestAxisCount", 0))
    info["slowest_axis"] = obj.get("SlowestAxisCount", obj.get("slowestAxisCount", 0))

    # Represented interpretation
    rep_obj = obj.get("RepresentedObject", obj.get("representedObject", {}))
    if rep_obj:
        info["has_interpretation"] = True
        info["interpretation_uuid"] = rep_obj.get("Uuid", rep_obj.get("uuid", ""))
        info["interpretation_type"] = rep_obj.get("QualifiedType", rep_obj.get("qualifiedType", ""))
        info["interpretation_title"] = rep_obj.get("Title", rep_obj.get("title", ""))

    # Geometry / grid type
    geom = obj.get("Geometry", obj.get("geometry", {}))
    if geom:
        points = geom.get("Points", geom.get("points", {}))
        if points:
            support = points.get("SupportingGeometry", points.get("supportingGeometry", {}))
            stype = support.get("$type", support.get("type", ""))
            if "LatticeArray" in stype and "FromRepresentation" not in stype:
                info["grid_type"] = "inline_lattice"
            elif "FromRepresentation" in stype:
                info["grid_type"] = "external_grid"
                sup_rep = support.get("SupportingRepresentation", support.get("supportingRepresentation", {}))
                info["supporting_rep_uuid"] = sup_rep.get("Uuid", sup_rep.get("uuid", ""))

        # CRS domain detection
        local_crs = geom.get("LocalCrs", geom.get("localCrs", {}))
        if local_crs:
            crs_title = local_crs.get("Title", local_crs.get("title", ""))
            if "time" in crs_title.lower() or "twt" in crs_title.lower():
                info["domain"] = "time"
            elif "depth" in crs_title.lower():
                info["domain"] = "depth"

    return info


def print_summary(surfaces: List[Dict[str, Any]], all_types: List[dict]) -> None:
    """Print a formatted summary of discovered surfaces."""
    print(f"\n{'='*80}")
    print(f"  RDDMS Dataspace Summary")
    print(f"{'='*80}")

    print(f"\n  Resource types:")
    for t in all_types:
        print(f"    {t['name']:45s}  {t['count']:4d} objects")
    total = sum(t["count"] for t in all_types)
    print(f"    {'─'*45}  {'─'*4}")
    print(f"    {'TOTAL':45s}  {total:4d}")

    if not surfaces:
        print("\n  No Grid2dRepresentation objects found.")
        return

    depth = [s for s in surfaces if s["domain"] == "depth"]
    time_ = [s for s in surfaces if s["domain"] == "time"]
    unknown = [s for s in surfaces if s["domain"] == "unknown"]

    print(f"\n  Grid2dRepresentation objects: {len(surfaces)}")
    print(f"    Depth domain:   {len(depth)}")
    print(f"    Time domain:    {len(time_)}")
    print(f"    Unknown domain: {len(unknown)}")

    for label, group in [("DEPTH", depth), ("TIME", time_), ("UNKNOWN", unknown)]:
        if not group:
            continue
        print(f"\n  ── {label} surfaces ──")
        for s in group:
            grid_tag = f"[{s['grid_type']}]"
            interp_tag = f" → {s['interpretation_title']}" if s["has_interpretation"] else ""
            size = f"{s['fastest_axis']}×{s['slowest_axis']}" if s["fastest_axis"] else "?"
            print(f"    {s['uuid'][:8]}..  {s['title'][:45]:45s}  {size:10s}  {grid_tag:18s}{interp_tag}")

    # StructureMap candidates
    candidates = depth if depth else unknown
    print(f"\n  ── StructureMap:1.0.0 candidates ──")
    if candidates:
        for s in candidates:
            print(f"    ✓ {s['title']}  ({s['uuid']})")
    else:
        print(f"    (none found — need depth-domain Grid2dRepresentations)")

    print()


# ── CLI ──────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="List Grid2dRepresentation surfaces in an RDDMS dataspace."
    )
    ap.add_argument("dataspace", help="Dataspace path, e.g. maap/drogon")
    ap.add_argument("--env-file", default=str(REPO_ROOT / ".env"),
                    help="Path to .env file")
    ap.add_argument("--json", action="store_true",
                    help="Output raw JSON instead of summary")
    ap.add_argument("--all", action="store_true",
                    help="Show all resource types, not just Grid2dRepresentation")
    ap.add_argument("--save", metavar="FILE",
                    help="Save surface list to JSON file")
    args = ap.parse_args()

    load_env_file(args.env_file)
    env = env_params()

    missing = [k for k, v in env.items() if not v]
    if missing:
        print(f"ERROR: Missing env: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    print(f"Dataspace:  {args.dataspace}")
    print(f"Host:       {env['host']}")
    print(f"Partition:  {env['partition']}")

    token = get_token(env["tenant"], env["client_id"], env["refresh_token"], env["scope"])
    print("Token acquired.\n")

    base = f"https://{env['host']}/api/reservoir-ddms/v2"
    h = rddms_headers(token, env["partition"])

    # List all resource types
    all_types = list_resource_types(base, args.dataspace, h)

    # Find Grid2dRepresentation objects
    grid2d_type = None
    for t in all_types:
        name = t["name"].lower()
        if "grid2d" in name:
            grid2d_type = t["name"]
            break

    surfaces: List[Dict[str, Any]] = []
    if grid2d_type:
        objs = list_resources(base, args.dataspace, grid2d_type, h)
        for obj_ref in objs:
            uuid = extract_uuid(obj_ref.get("uri", ""))
            if not uuid:
                continue
            # Try to get full object for detailed analysis
            full_obj = get_object(base, args.dataspace, grid2d_type, uuid, h)
            if full_obj:
                info = classify_grid2d(full_obj)
                surfaces.append(info)
            else:
                # Fallback: just use the listing info
                surfaces.append({
                    "uuid": uuid,
                    "title": obj_ref.get("name", ""),
                    "domain": "unknown",
                    "grid_type": "unknown",
                    "has_interpretation": False,
                    "fastest_axis": 0,
                    "slowest_axis": 0,
                })

    if args.json:
        output = {"dataspace": args.dataspace, "resource_types": all_types, "surfaces": surfaces}
        print(json.dumps(output, indent=2))
    else:
        print_summary(surfaces, all_types)

    if args.save:
        output = {"dataspace": args.dataspace, "resource_types": all_types, "surfaces": surfaces}
        Path(args.save).write_text(json.dumps(output, indent=2) + "\n")
        print(f"  Saved to {args.save}")


if __name__ == "__main__":
    main()
