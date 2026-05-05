#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_horizon_controlpoints.py - Discover PointSetRepresentation objects
in a Reservoir DDMS dataspace and emit HorizonControlPoints:1.0.0 records.

HorizonControlPoints represents sparse interpreter seed picks used to guide
automated horizon tracking.  It inherits AbstractRepresentation (providing
DDMSDatasets[], InterpretationID, Role, Type) and adds:
  - HorizonControlPoints (AbstractColumnBasedTable) for inline pick data
  - WellboreMarkerSetIDs for well-tie linkage
  - DomainTypeID, HorizontalCRSID, VerticalDatum

This script discovers PointSetRepresentations that are linked to a
HorizonInterpretation (via RepresentedInterpretation.ContentType) and
emits conformant HorizonControlPoints:1.0.0 catalog records.

Schema dependency:
  - HorizonControlPoints.1.0.0.json  (already in schemas/)
  - Inherits AbstractRepresentation.1.0.0 (DDMSDatasets[], ancestry)
  - RepresentationRole = Pick
  - RepresentationType = PointSet

Usage:
  python gen_horizon_controlpoints.py                        # discover + generate
  python gen_horizon_controlpoints.py --dry-run              # list only
  python gen_horizon_controlpoints.py --dataspace dev/picks  # alternate dataspace
  python gen_horizon_controlpoints.py --inline-picks         # fetch XYZ arrays and embed inline
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from seisint._shared import (
    stable_uuid,
    wpc_id,
    md_id,
    acl_block,
    legal_block,
    save_json,
)
from _auth import load_env, mint_from_env, load_instance, get_token  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
RECORDS_DIR = SCRIPT_DIR / "records"

# ── Config ──────────────────────────────────────────────────────────────
DATASPACE = "maap/drogon"
INSTANCE = "eqndev"
POINTSET_TYPE = "resqml20.obj_PointSetRepresentation"

# Substrings in ContentType that indicate a horizon interpretation link.
# The RDDMS returns ContentType like:
#   "application/x-resqml+xml;version=2.0;type=obj_HorizonInterpretation"
HORIZON_CONTENT_MARKERS = ("HorizonInterpretation",)


def _rddms_url(host: str, path: str) -> str:
    return f"{host}/api/reservoir-ddms/v2{path}"


def _headers(token: str, partition: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "data-partition-id": partition,
        "Accept": "application/json",
    }


# ── RDDMS Discovery ────────────────────────────────────────────────────

def list_point_sets(host: str, token: str, partition: str, ds_path: str) -> list[dict[str, Any]]:
    """List all PointSetRepresentation objects in a dataspace."""
    enc = urllib.parse.quote(ds_path, safe="")
    url = _rddms_url(host, f"/dataspaces/{enc}/resources/{POINTSET_TYPE}")
    r = httpx.get(url, headers=_headers(token, partition), timeout=60)
    if r.status_code == 404:
        return []  # no objects of this type
    r.raise_for_status()
    return r.json() or []


def get_pointset_object(host: str, token: str, partition: str, ds_path: str, uuid: str) -> dict[str, Any]:
    """Fetch a single PointSetRepresentation object."""
    enc = urllib.parse.quote(ds_path, safe="")
    url = _rddms_url(host, f"/dataspaces/{enc}/resources/{POINTSET_TYPE}/{uuid}")
    r = httpx.get(url, headers=_headers(token, partition), params={"$format": "json"}, timeout=60)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, list):
        return data[0] if data else {}
    return data or {}


def get_pointset_arrays(host: str, token: str, partition: str, ds_path: str, uuid: str) -> list[float]:
    """Fetch the XYZ point array from a PointSetRepresentation.

    Returns a flat list of [x0, y0, z0, x1, y1, z1, ...] coordinates.
    """
    enc = urllib.parse.quote(ds_path, safe="")
    base_url = _rddms_url(host, f"/dataspaces/{enc}/resources/{POINTSET_TYPE}/{uuid}")

    # List arrays
    r = httpx.get(f"{base_url}/arrays", headers=_headers(token, partition), timeout=60)
    r.raise_for_status()
    arr_list = r.json() or []

    if not arr_list:
        return []

    # Find the points array (usually the first/only one)
    arr_path = ""
    for a in arr_list:
        uid = a.get("uid") or {}
        pir = uid.get("pathInResource", "")
        if "point" in pir.lower() or "coordinate" in pir.lower():
            arr_path = pir
            break
    if not arr_path and arr_list:
        arr_path = (arr_list[0].get("uid") or {}).get("pathInResource", "")

    if not arr_path:
        return []

    arr_enc = urllib.parse.quote(arr_path, safe="")
    r = httpx.get(f"{base_url}/arrays/{arr_enc}", headers=_headers(token, partition), timeout=60)
    r.raise_for_status()
    body = r.json() or {}
    inner = body.get("data") or body
    if isinstance(inner, dict):
        return inner.get("data") or inner.get("values") or []
    if isinstance(inner, list):
        return inner
    return []


# ── Classification ──────────────────────────────────────────────────────

def classify_pointset(obj: dict[str, Any]) -> dict[str, Any]:
    """Classify a PointSetRepresentation — is it horizon control points?

    Returns metadata dict with classification.
    """
    uuid_val = obj.get("Uuid") or obj.get("UUID") or obj.get("uuid") or ""
    title = (obj.get("Citation") or {}).get("Title", uuid_val)

    # Check interpretation link
    interp_ref = obj.get("RepresentedInterpretation") or {}
    interp_ct = interp_ref.get("ContentType", "")
    interp_uuid = interp_ref.get("UUID") or interp_ref.get("Uuid") or ""
    interp_title = interp_ref.get("Title") or ""

    is_horizon = any(marker in interp_ct for marker in HORIZON_CONTENT_MARKERS)

    # Count points (NodePatch or Point3d array)
    patches = obj.get("NodePatch") or obj.get("NodePatchGeometry") or []
    if isinstance(patches, list):
        # Each patch has a Count
        n_points = sum(int(p.get("Count", 0)) for p in patches)
    else:
        n_points = 0

    # Resolve CRS type for domain classification
    geom = {}
    if patches and isinstance(patches, list):
        geom = patches[0].get("Geometry") or {}
    crs_ref = geom.get("LocalCrs") or {}
    crs_ct = crs_ref.get("ContentType", "")
    domain = "time" if "LocalTime" in crs_ct else "depth"

    return {
        "uuid": uuid_val,
        "title": title,
        "is_horizon_picks": is_horizon,
        "domain": domain,
        "interpretation_uuid": interp_uuid,
        "interpretation_title": interp_title,
        "interpretation_type": interp_ct,
        "n_points": n_points,
    }


# ── Record Builder ──────────────────────────────────────────────────────

def _ddms_uri(ds_path: str, uuid_val: str) -> str:
    """EML URI for DDMSDatasets[]."""
    return (
        f"eml:///dataspace('{ds_path}')/"
        f"resqml20.obj_PointSetRepresentation('{uuid_val}')"
    )


def make_horizon_controlpoints(
    prefix: str,
    info: dict[str, Any],
    ds_path: str,
    *,
    inline_xyz: list[float] | None = None,
) -> dict[str, Any]:
    """Build a HorizonControlPoints:1.0.0 record.

    Args:
        prefix: OSDU namespace prefix.
        info: Classification output from classify_pointset().
        ds_path: RDDMS dataspace path.
        inline_xyz: Optional flat [x0,y0,z0,...] array for inline ColumnValues.

    Returns:
        HorizonControlPoints WPC record dict.
    """
    uuid_val = info["uuid"]
    title = info["title"]
    interp_uuid = info["interpretation_uuid"]
    domain = info["domain"]

    # Stable OSDU UUID for this control points record
    osdu_uuid = stable_uuid(f"hcp:{uuid_val}")

    # Build HorizonInterpretation reference
    horizon_interp_id = ""
    feature_id = ""
    if interp_uuid:
        osdu_interp_uuid = stable_uuid(f"resqml-interp:{interp_uuid}")
        horizon_interp_id = wpc_id(prefix, "HorizonInterpretation", osdu_interp_uuid)
        osdu_feat_uuid = stable_uuid(f"resqml-feature:{interp_uuid}")
        feature_id = md_id(prefix, "LocalBoundaryFeature", osdu_feat_uuid)

    data: dict[str, Any] = {
        "Name": f"{title} - Control Points",
        "Description": (
            f"Horizon seed picks from PointSetRepresentation {uuid_val} "
            f"in dataspace {ds_path}. {info['n_points']} control point(s)."
        ),
        "RepresentationRole": f"{prefix}:reference-data--RepresentationRole:Pick:",
        "RepresentationType": f"{prefix}:reference-data--RepresentationType:PointSet:",
        "DomainTypeID": f"{prefix}:reference-data--DomainType:{'Depth' if domain == 'depth' else 'Time'}:",
        "DDMSDatasets": [_ddms_uri(ds_path, uuid_val)],
    }

    if horizon_interp_id:
        data["InterpretationID"] = horizon_interp_id
        data["InterpretationName"] = info.get("interpretation_title") or title

    # Ancestry
    parents = []
    if horizon_interp_id:
        parents.append(horizon_interp_id)
    if feature_id:
        parents.append(feature_id)
    if parents:
        data["ancestry"] = {"parents": parents}

    # Inline picks (optional: embed XYZ as AbstractColumnBasedTable if available)
    if inline_xyz and len(inline_xyz) >= 3:
        n_pts = len(inline_xyz) // 3
        xs = [inline_xyz[i * 3] for i in range(n_pts)]
        ys = [inline_xyz[i * 3 + 1] for i in range(n_pts)]
        zs = [inline_xyz[i * 3 + 2] for i in range(n_pts)]

        data["HorizonControlPoints"] = {
            "Columns": [
                {
                    "Name": "Easting",
                    "Kind": "double",
                    "UnitOfMeasure": "m",
                    "Values": xs,
                },
                {
                    "Name": "Northing",
                    "Kind": "double",
                    "UnitOfMeasure": "m",
                    "Values": ys,
                },
                {
                    "Name": "Depth" if domain == "depth" else "TWT",
                    "Kind": "double",
                    "UnitOfMeasure": "m" if domain == "depth" else "ms",
                    "Values": zs,
                },
            ],
        }

    return {
        "id": wpc_id(prefix, "HorizonControlPoints", osdu_uuid),
        "kind": "osdu:wks:work-product-component--HorizonControlPoints:1.0.0",
        "acl": acl_block(),
        "legal": legal_block(),
        "data": data,
    }


# ── Main Pipeline ───────────────────────────────────────────────────────

def discover_and_generate(
    prefix: str = "dev",
    ds_path: str = DATASPACE,
    dry_run: bool = False,
    inline_picks: bool = False,
    instance: str = INSTANCE,
) -> dict[str, Any]:
    """Discover PointSetRepresentations, classify, and emit HorizonControlPoints.

    Returns a summary dict.
    """
    inst = load_instance(instance)
    token = get_token(instance, verbose=True)
    host = inst["host"]
    partition = inst["partition"]

    print(f"Discovering PointSetRepresentations in dataspace '{ds_path}'...")
    raw_list = list_point_sets(host, token, partition, ds_path)
    print(f"  Found {len(raw_list)} PointSetRepresentation object(s)")

    if not raw_list:
        print("  No point sets to process.")
        return {"found": 0, "horizon": 0, "other": 0, "records": []}

    # Fetch full objects and classify
    classified = []
    for entry in raw_list:
        uid = entry.get("Uuid") or entry.get("UUID") or entry.get("uuid") or ""
        if not uid:
            uri = entry.get("uri", "")
            if "(" in uri:
                uid = uri.split("(")[-1].rstrip(")'\"")
        if not uid:
            continue

        try:
            obj = get_pointset_object(host, token, partition, ds_path, uid)
            info = classify_pointset(obj)
            classified.append(info)
        except Exception as e:
            name = entry.get("name", uid[:12])
            print(f"  WARN: Failed to fetch/classify {name} ({uid[:8]}...): {e}")
            classified.append({"uuid": uid, "title": entry.get("name", uid), "is_horizon_picks": False, "error": str(e)})

    horizon_picks = [c for c in classified if c.get("is_horizon_picks")]
    other = [c for c in classified if not c.get("is_horizon_picks") and "error" not in c]
    errors = [c for c in classified if "error" in c]

    print(f"  Classification: {len(horizon_picks)} horizon picks, {len(other)} other, {len(errors)} error")

    if dry_run:
        print("\n  DRY RUN — no files written")
        for c in classified:
            kind = "HORIZON" if c.get("is_horizon_picks") else "other"
            print(f"    [{kind}] {c['title']} ({c['uuid'][:8]}...) — {c.get('n_points', '?')} pts, domain={c.get('domain', '?')}")
        return {"found": len(classified), "horizon": len(horizon_picks), "other": len(other), "records": []}

    # Generate records (only for horizon-linked point sets)
    records: list[dict[str, Any]] = []
    for info in horizon_picks:
        # Optionally fetch inline XYZ data
        xyz: list[float] | None = None
        if inline_picks:
            try:
                xyz = get_pointset_arrays(host, token, partition, ds_path, info["uuid"])
                if xyz:
                    print(f"    Fetched {len(xyz) // 3} inline points for {info['title']}")
            except Exception as e:
                print(f"    WARN: Could not fetch arrays for {info['uuid']}: {e}")

        records.append(make_horizon_controlpoints(prefix, info, ds_path, inline_xyz=xyz))

    # Save manifest
    manifest = {
        "kind": "osdu:wks:Manifest:1.0.0",
        "ReferenceData": [],
        "MasterData": [],
        "Data": {
            "WorkProductComponents": records,
        },
    }

    outpath = SCRIPT_DIR / "manifest_horizon_controlpoints.json"
    save_json(manifest, outpath)
    print(f"\n  Wrote {len(records)} HorizonControlPoints records → {outpath.name}")

    # Also save individual record files
    RECORDS_DIR.mkdir(exist_ok=True)
    existing_count = len(list(RECORDS_DIR.glob("*.json")))
    for i, rec in enumerate(records, start=existing_count):
        fname = f"{i:03d}_{rec['id'].replace(':', '_').replace('/', '_')}.json"
        save_json(rec, RECORDS_DIR / fname)

    return {"found": len(classified), "horizon": len(horizon_picks), "other": len(other), "records": records}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate HorizonControlPoints records from RDDMS")
    parser.add_argument("--prefix", default="dev", help="OSDU namespace prefix (default: dev)")
    parser.add_argument("--dataspace", default=DATASPACE, help="RDDMS dataspace name")
    parser.add_argument("--dry-run", action="store_true", help="List objects only, no file output")
    parser.add_argument("--inline-picks", action="store_true", help="Fetch XYZ arrays and embed as ColumnValues")
    args = parser.parse_args()
    discover_and_generate(prefix=args.prefix, ds_path=args.dataspace, dry_run=args.dry_run, inline_picks=args.inline_picks)
