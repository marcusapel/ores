#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_rddms_catalog.py — Fetch GenericRepresentation:1.2.0 records from
the RDDMS manifests/build API for our demo surfaces.

The RDDMS manifests/build endpoint (POST /api/reservoir-ddms/v2/manifests/build)
is the authoritative source for GenericRepresentation WPCs.  It produces records
with real SpatialArea, IndexableElementCount, and DDMSDatasets URIs derived from
the actual RESQML content.

This script:
  1. Calls manifests/build for our 5 demo Grid2dRepresentation objects
  2. Saves the full manifest as manifest_rddms_catalog.json
  3. Splits out individual record files into records/

These GenericRepresentation records complement the StructureMap + SeismicHorizon
records from gen_volantis_interp.py.  Both kinds point to the same RDDMS objects
via DDMSDatasets[], but serve different purposes:

  GenericRepresentation  — universal RDDMS catalog layer (produced by RDDMS API)
  StructureMap           — specialised depth map with grid geometry + typed refs
  SeismicHorizon         — specialised TWT pick with seismic grid ref + domain type

Usage:
  python build_rddms_catalog.py                    # fetch & save
  python build_rddms_catalog.py --dry-run           # show what would be fetched
  python build_rddms_catalog.py --ingest            # fetch, save, and ingest to OSDU
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from seisint._shared import load_env, save_json

SCRIPT_DIR = Path(__file__).resolve().parent

# ── Demo Grid2dRepresentation UUIDs (same as gen_volantis_interp.py) ────
SURFACES = {
    # Depth surfaces (linked by StructureMap)
    "TopVolantis (Depth)":  "f857c36c-3939-4ff3-9125-a11cf2af105c",
    "BaseVolantis (Depth)": "0c6ab8e7-c793-4ab5-a88c-ccf457d9266d",
    "TopTherys (Depth)":    "0ce9278d-979c-450a-a3db-08ea96517463",
    # TWT surfaces (linked by SeismicHorizon)
    "TopVolantis (TWT)":    "9deb9074-c4eb-44ff-990a-229bb545d442",
    "BaseVolantis (TWT)":   "efcf91f9-6e56-4bed-9e23-f0e9350a0b91",
}

DATASPACE = "maap/drogon"


def get_token(env: dict) -> str:
    url = f"https://login.microsoftonline.com/{env['tenant']}/oauth2/v2.0/token"
    r = httpx.post(url, data={
        "grant_type": "refresh_token",
        "client_id": env["client_id"],
        "refresh_token": env["refresh_token"],
        "scope": env["scope"],
    }, timeout=30)
    r.raise_for_status()
    data = r.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"No access_token: {list(data.keys())}")
    print(f"  token acquired (expires_in={data.get('expires_in', '?')}s)")
    return token


def build_manifest(host: str, token: str, partition: str, uuids: list[str]) -> dict:
    """Call RDDMS manifests/build for specific Grid2dRepresentation UUIDs."""
    url = f"{host}/api/reservoir-ddms/v2/manifests/build"
    headers = {
        "Authorization": f"Bearer {token}",
        "data-partition-id": partition,
        "Content-Type": "application/json",
    }
    uris = [
        f"eml:///dataspace('{DATASPACE}')/resqml20.obj_Grid2dRepresentation({u})"
        for u in uuids
    ]
    body = {
        "uris": uris,
        "acl": {
            "owners": [f"data.default.owners@{partition}.dataservices.energy"],
            "viewers": [f"data.default.viewers@{partition}.dataservices.energy"],
        },
        "legal": {
            "legaltags": [f"{partition}-equinor-private-default"],
            "otherRelevantDataCountries": ["NO"],
        },
        "createMissingReferences": True,
    }
    r = httpx.post(url, headers=headers, json=body, timeout=120)
    if r.status_code >= 400:
        raise RuntimeError(f"manifests/build failed ({r.status_code}): {r.text[:500]}")
    return r.json() or {}


def ingest_records(host: str, token: str, partition: str, records: list[dict]) -> dict:
    """PUT records to OSDU Storage API."""
    url = f"{host}/api/storage/v2/records"
    headers = {
        "Authorization": f"Bearer {token}",
        "data-partition-id": partition,
        "Content-Type": "application/json",
    }
    r = httpx.put(url, headers=headers, json=records, timeout=120)
    if r.status_code >= 400:
        raise RuntimeError(f"Storage PUT failed ({r.status_code}): {r.text[:500]}")
    return r.json() or {}


def main():
    parser = argparse.ArgumentParser(description="Fetch GenericRepresentation records from RDDMS manifests/build")
    parser.add_argument("--env-file", nargs="+", default=["../../.env"], help=".env file(s)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be fetched without calling the API")
    parser.add_argument("--ingest", action="store_true", help="After fetching, ingest records to OSDU Storage")
    args = parser.parse_args()

    print("Loading env ...")
    env = load_env(args.env_file)
    host = env.get("host", "")
    partition = env.get("partition", "dev")
    print(f"  host={host}  partition={partition}")

    uuids = list(SURFACES.values())
    uris_display = [f"  {name}: {uuid}" for name, uuid in SURFACES.items()]
    print(f"\nSurfaces ({len(uuids)}):")
    for line in uris_display:
        print(line)

    if args.dry_run:
        print("\n[dry-run] Would call POST /api/reservoir-ddms/v2/manifests/build")
        print(f"  dataspace: {DATASPACE}")
        print(f"  uris: {len(uuids)} Grid2dRepresentation objects")
        return

    print("\nAuthenticating ...")
    token = get_token(env)

    print(f"\nCalling manifests/build for {len(uuids)} surfaces ...")
    manifest = build_manifest(host, token, partition, uuids)

    # Save full manifest
    outpath = SCRIPT_DIR / "manifest_rddms_catalog.json"
    save_json(manifest, outpath)
    print(f"  Written → {outpath}")

    # Extract and count
    inner = manifest.get("Data", manifest)
    all_records = []
    for section in ("WorkProductComponents", "Datasets", "ReferenceData", "MasterData"):
        items = inner.get(section, [])
        if items:
            all_records.extend(items)

    kinds = {}
    for r in all_records:
        k = r.get("kind", "?").split("--")[-1]
        kinds[k] = kinds.get(k, 0) + 1
    print(f"\n  Total: {len(all_records)} records")
    for k, c in sorted(kinds.items()):
        print(f"    {c}× {k}")

    # Show GenericRepresentations
    grep_records = [r for r in all_records if "GenericRepresentation" in r.get("kind", "")]
    if grep_records:
        print(f"\n  GenericRepresentation records ({len(grep_records)}):")
        for r in grep_records:
            d = r.get("data", {})
            name = d.get("Name", "?")
            ddms = d.get("DDMSDatasets", ["?"])[0]
            nodes = next((x["Count"] for x in d.get("IndexableElementCount", []) if "Nodes" in x.get("IndexableElementID", "")), "?")
            print(f"    {name:30s}  nodes={nodes:>8}  DDMSDatasets=...{ddms.split('(')[-1][:15]}")

    # Save individual record files (prefixed with rddms_ to distinguish from gen_volantis records)
    records_dir = SCRIPT_DIR / "records"
    records_dir.mkdir(exist_ok=True)
    for i, r in enumerate(all_records):
        kind_short = r.get("kind", "unknown").split("--")[-1].replace(":", "_")
        uuid_part = r["id"].split(":")[-2][:12] if ":" in r["id"] else "unknown"
        name = r.get("data", {}).get("Name", "").replace(" ", "_").replace("/", "_")[:20]
        fname = f"rddms_{i:03d}_{kind_short}_{name}.json"
        save_json(r, records_dir / fname)

    print(f"\n  {len(all_records)} record files written to {records_dir}/rddms_*.json")

    # Ingest if requested
    if args.ingest:
        print(f"\nIngesting {len(all_records)} records to OSDU Storage ...")
        result = ingest_records(host, token, partition, all_records)
        count = result.get("recordCount", len(result.get("recordIds", [])))
        print(f"  Ingested: {count} records")
        ids = result.get("recordIds", [])
        for rid in ids[:10]:
            print(f"    {rid}")
        if len(ids) > 10:
            print(f"    ... and {len(ids) - 10} more")

    print("\nDone.")


if __name__ == "__main__":
    main()
