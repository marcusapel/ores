#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_fault_polylines.py - Discover PolylineSetRepresentation objects in a
Reservoir DDMS dataspace and emit GenericRepresentation:1.2.0 records with
fault-specific Role and Type constraints.

This follows the Oslo'26 workshop interim decision:
  - PolylineSetRepresentation tied to FaultInterpretation → GenericRepresentation
    with Role=FaultStick, Type=PolylineSetRepresentation
  - Non-fault polylines → GenericRepresentation with Role=Outline/Contour etc.

Once a SeismicFault WKS schema is formally published and mapped, these records
can be upgraded to the specialised type.  Until then, GenericRepresentation
with constrained Role/Type is the agreed interoperability pattern.

Schema dependency:
  - GenericRepresentation:1.2.0  (inherits AbstractRepresentation.1.0.0)
  - AbstractRepresentation.InterpretationID allows FaultInterpretation

Usage:
  python gen_fault_polylines.py                        # list + generate
  python gen_fault_polylines.py --dry-run              # list only, no file output
  python gen_fault_polylines.py --dataspace dev/faults # alternate dataspace
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
POLYLINE_TYPE = "resqml20.obj_PolylineSetRepresentation"

# Substrings in ContentType that indicate a fault interpretation link.
# The RDDMS returns ContentType like:
#   "application/x-resqml+xml;version=2.0;type=obj_FaultInterpretation"
FAULT_CONTENT_MARKERS = ("FaultInterpretation",)


def _rddms_url(host: str, path: str) -> str:
    return f"{host}/api/reservoir-ddms/v2{path}"


def _headers(token: str, partition: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "data-partition-id": partition,
        "Accept": "application/json",
    }


# ── RDDMS Discovery ────────────────────────────────────────────────────

def list_polyline_sets(host: str, token: str, partition: str, ds_path: str) -> list[dict[str, Any]]:
    """List all PolylineSetRepresentation objects in a dataspace."""
    enc = urllib.parse.quote(ds_path, safe="")
    url = _rddms_url(host, f"/dataspaces/{enc}/resources/{POLYLINE_TYPE}")
    r = httpx.get(url, headers=_headers(token, partition), timeout=60)
    if r.status_code == 404:
        return []  # no objects of this type
    r.raise_for_status()
    return r.json() or []


def get_polyline_object(host: str, token: str, partition: str, ds_path: str, uuid: str) -> dict[str, Any]:
    """Fetch a single PolylineSetRepresentation object."""
    enc = urllib.parse.quote(ds_path, safe="")
    url = _rddms_url(host, f"/dataspaces/{enc}/resources/{POLYLINE_TYPE}/{uuid}")
    r = httpx.get(url, headers=_headers(token, partition), params={"$format": "json"}, timeout=60)
    r.raise_for_status()
    data = r.json()
    # RDDMS may return a list with one item
    if isinstance(data, list):
        return data[0] if data else {}
    return data or {}


# ── Classification ──────────────────────────────────────────────────────

def classify_polyline(obj: dict[str, Any]) -> dict[str, Any]:
    """Classify a PolylineSetRepresentation as fault/non-fault.

    Checks RepresentedInterpretation → ContentType for fault link.
    Returns metadata dict with classification.
    """
    uuid_val = obj.get("Uuid") or obj.get("UUID") or obj.get("uuid") or ""
    title = (obj.get("Citation") or {}).get("Title", uuid_val)

    # Check interpretation link
    interp_ref = obj.get("RepresentedInterpretation") or {}
    interp_ct = interp_ref.get("ContentType", "")
    interp_uuid = interp_ref.get("UUID") or interp_ref.get("Uuid") or ""
    interp_title = interp_ref.get("Title") or ""

    is_fault = any(marker in interp_ct for marker in FAULT_CONTENT_MARKERS)

    # Count polylines (LinePatch array)
    patches = obj.get("LinePatch") or obj.get("NodePatch") or []
    if isinstance(patches, list):
        n_polylines = len(patches)
    else:
        n_polylines = 0

    return {
        "uuid": uuid_val,
        "title": title,
        "is_fault": is_fault,
        "interpretation_uuid": interp_uuid,
        "interpretation_title": interp_title,
        "interpretation_type": interp_ct,
        "n_polylines": n_polylines,
    }


# ── Record Builders ─────────────────────────────────────────────────────

def _ddms_uri(ds_path: str, uuid_val: str) -> str:
    """Build EML URI for DDMSDatasets[]."""
    return (
        f"eml:///dataspace('{ds_path}')/"
        f"resqml20.obj_PolylineSetRepresentation('{uuid_val}')"
    )


def make_fault_generic_representation(
    prefix: str,
    info: dict[str, Any],
    ds_path: str,
) -> dict[str, Any]:
    """GenericRepresentation:1.2.0 with fault-specific constraint.

    Role = FaultStick (for fault sticks / picks on seismic sections)
    Type = PolylineSetRepresentation
    InterpretationID → reference to the FaultInterpretation WPC
    """
    uuid_val = info["uuid"]
    title = info["title"]
    interp_uuid = info["interpretation_uuid"]

    # Build FaultInterpretation reference (stable UUID from RESQML UUID)
    fault_interp_id = ""
    fault_feature_id = ""
    if interp_uuid:
        osdu_interp_uuid = stable_uuid(f"resqml-fault-interp:{interp_uuid}")
        fault_interp_id = wpc_id(prefix, "FaultInterpretation", osdu_interp_uuid)
        osdu_feat_uuid = stable_uuid(f"resqml-fault-feature:{interp_uuid}")
        fault_feature_id = md_id(prefix, "LocalBoundaryFeature", osdu_feat_uuid)

    data: dict[str, Any] = {
        "Name": f"{title} - PolylineSetRepresentation (Fault)",
        "Description": (
            f"RDDMS catalog entry for fault polylines: {title} "
            f"(PolylineSetRepresentation {uuid_val}) in dataspace {ds_path}"
        ),
        "ExistenceKind": f"{prefix}:reference-data--ExistenceKind:Prototype:",
        "Role": f"{prefix}:reference-data--RepresentationRole:FaultStick:",
        "Type": f"{prefix}:reference-data--RepresentationType:PolylineSetRepresentation:",
        "DDMSDatasets": [_ddms_uri(ds_path, uuid_val)],
    }

    if fault_interp_id:
        data["InterpretationID"] = fault_interp_id
        data["InterpretationName"] = info.get("interpretation_title") or title
        data["ancestry"] = {"parents": [fault_interp_id]}
        if fault_feature_id:
            data["ancestry"]["parents"].append(fault_feature_id)

    return {
        "id": f"{prefix}:work-product-component--GenericRepresentation:{uuid_val}:1",
        "kind": "osdu:wks:work-product-component--GenericRepresentation:1.2.0",
        "acl": acl_block(),
        "legal": legal_block(),
        "data": data,
    }


def make_nonfault_generic_representation(
    prefix: str,
    info: dict[str, Any],
    ds_path: str,
) -> dict[str, Any]:
    """GenericRepresentation:1.2.0 for non-fault polylines.

    Role = Outline (contours, field boundaries, etc.)
    Type = PolylineSetRepresentation
    """
    uuid_val = info["uuid"]
    title = info["title"]

    data: dict[str, Any] = {
        "Name": f"{title} - PolylineSetRepresentation",
        "Description": (
            f"RDDMS catalog entry for polyline set: {title} "
            f"(PolylineSetRepresentation {uuid_val}) in dataspace {ds_path}"
        ),
        "ExistenceKind": f"{prefix}:reference-data--ExistenceKind:Prototype:",
        "Role": f"{prefix}:reference-data--RepresentationRole:Outline:",
        "Type": f"{prefix}:reference-data--RepresentationType:PolylineSetRepresentation:",
        "DDMSDatasets": [_ddms_uri(ds_path, uuid_val)],
    }

    return {
        "id": f"{prefix}:work-product-component--GenericRepresentation:{uuid_val}:1",
        "kind": "osdu:wks:work-product-component--GenericRepresentation:1.2.0",
        "acl": acl_block(),
        "legal": legal_block(),
        "data": data,
    }


# ── Main Pipeline ───────────────────────────────────────────────────────

def discover_and_generate(
    prefix: str = "dev",
    ds_path: str = DATASPACE,
    dry_run: bool = False,
    instance: str = INSTANCE,
) -> dict[str, Any]:
    """Discover PolylineSetRepresentations, classify, and emit records.

    Returns a summary dict.
    """
    inst = load_instance(instance)
    token = get_token(instance, verbose=True)
    host = inst["host"]
    partition = inst["partition"]

    print(f"Discovering PolylineSetRepresentations in dataspace '{ds_path}'...")
    raw_list = list_polyline_sets(host, token, partition, ds_path)
    print(f"  Found {len(raw_list)} PolylineSetRepresentation object(s)")

    if not raw_list:
        print("  No polylines to process.")
        return {"found": 0, "faults": 0, "nonfault": 0, "records": []}

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
            obj = get_polyline_object(host, token, partition, ds_path, uid)
            info = classify_polyline(obj)
            classified.append(info)
        except Exception as e:
            name = entry.get("name", uid[:12])
            print(f"  WARN: Failed to fetch/classify {name} ({uid[:8]}...): {e}")
            classified.append({"uuid": uid, "title": entry.get("name", uid), "is_fault": False, "error": str(e)})

    faults = [c for c in classified if c.get("is_fault")]
    nonfault = [c for c in classified if not c.get("is_fault") and "error" not in c]
    errors = [c for c in classified if "error" in c]

    print(f"  Classification: {len(faults)} fault, {len(nonfault)} non-fault, {len(errors)} error")

    if dry_run:
        print("\n  DRY RUN — no files written")
        for c in classified:
            role = "FAULT" if c.get("is_fault") else "non-fault"
            print(f"    [{role}] {c['title']} ({c['uuid'][:8]}...) — {c.get('n_polylines', '?')} polylines")
        return {"found": len(classified), "faults": len(faults), "nonfault": len(nonfault), "records": []}

    # Generate records
    records: list[dict[str, Any]] = []
    for info in faults:
        records.append(make_fault_generic_representation(prefix, info, ds_path))
    for info in nonfault:
        records.append(make_nonfault_generic_representation(prefix, info, ds_path))

    # Save manifest
    manifest = {
        "kind": "osdu:wks:Manifest:1.0.0",
        "ReferenceData": [],
        "MasterData": [],
        "Data": {
            "WorkProductComponents": records,
        },
    }

    outpath = SCRIPT_DIR / "manifest_fault_polylines.json"
    save_json(manifest, outpath)
    print(f"\n  Wrote {len(records)} records → {outpath.name}")

    # Also save individual record files
    RECORDS_DIR.mkdir(exist_ok=True)
    existing_count = len(list(RECORDS_DIR.glob("*.json")))
    for i, rec in enumerate(records, start=existing_count):
        fname = f"{i:03d}_{rec['id'].replace(':', '_').replace('/', '_')}.json"
        save_json(rec, RECORDS_DIR / fname)

    return {"found": len(classified), "faults": len(faults), "nonfault": len(nonfault), "records": records}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate fault polyline catalog records")
    parser.add_argument("--prefix", default="dev", help="OSDU namespace prefix (default: dev)")
    parser.add_argument("--dataspace", default=DATASPACE, help="RDDMS dataspace name")
    parser.add_argument("--dry-run", action="store_true", help="List objects only, no file output")
    args = parser.parse_args()
    discover_and_generate(prefix=args.prefix, ds_path=args.dataspace, dry_run=args.dry_run)
