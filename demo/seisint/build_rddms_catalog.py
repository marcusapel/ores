#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_rddms_catalog.py - Fetch GenericRepresentation:1.2.0 records from
the RDDMS manifests/build API for RESQML objects in a dataspace.

The RDDMS manifests/build endpoint (POST /api/reservoir-ddms/v2/manifests/build)
is the authoritative source for GenericRepresentation WPCs.  It produces records
with real SpatialArea, IndexableElementCount, and DDMSDatasets URIs derived from
the actual RESQML content.

This script:
  1. Discovers objects in the dataspace (Grid2d, PolylineSet, PointSet) - or
     uses hardcoded UUIDs for reproducibility.
  2. Calls manifests/build for discovered objects.
  3. Saves the full manifest as manifest_rddms_catalog.json
  4. Splits out individual record files into records/

These GenericRepresentation records complement the StructureMap + SeismicHorizon
records from gen_volantis_interp.py.  Both kinds point to the same RDDMS objects
via DDMSDatasets[], but serve different purposes:

  GenericRepresentation  - universal RDDMS catalog layer (produced by RDDMS API)
  StructureMap           - specialised depth map with grid geometry + typed refs
  SeismicHorizon         - specialised TWT pick with seismic grid ref + domain type

Usage:
  python build_rddms_catalog.py                    # hardcoded 5 Grid2d UUIDs (default)
  python build_rddms_catalog.py --discover         # discover ALL objects dynamically
  python build_rddms_catalog.py --discover --types Grid2d,PolylineSet,PointSet
  python build_rddms_catalog.py --dry-run          # show what would be fetched
  python build_rddms_catalog.py --ingest           # fetch, save, and ingest to OSDU
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from seisint._shared import save_json
from _auth import load_env, mint_from_env  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent

# ── Demo Grid2dRepresentation UUIDs (same as gen_volantis_interp.py) ────
# Used in default (non-discover) mode for reproducibility.
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

# ── RESQML types eligible for dynamic discovery ────────────────────────
RDDMS_TYPES = {
    "Grid2d":      "resqml20.obj_Grid2dRepresentation",
    "PolylineSet": "resqml20.obj_PolylineSetRepresentation",
    "PointSet":    "resqml20.obj_PointSetRepresentation",
    "IjkGrid":     "resqml20.obj_IjkGridRepresentation",
    "TriangulatedSet": "resqml20.obj_TriangulatedSetRepresentation",
}


def get_token(env: dict) -> str:
    return mint_from_env(env)


def discover_objects(
    host: str,
    token: str,
    partition: str,
    ds_path: str,
    types: list[str] | None = None,
) -> dict[str, list[str]]:
    """Discover RESQML objects in the dataspace by type.

    Args:
        host: RDDMS host base URL.
        token: Bearer token.
        partition: Data partition ID.
        ds_path: Dataspace path (e.g. 'maap/drogon').
        types: List of short type names to discover (e.g. ['Grid2d', 'PolylineSet']).
                Defaults to all known types.

    Returns:
        Dict mapping short type name → list of UUIDs found.
    """
    import urllib.parse

    if types is None:
        types = list(RDDMS_TYPES.keys())

    headers = {
        "Authorization": f"Bearer {token}",
        "data-partition-id": partition,
        "Accept": "application/json",
    }

    enc = urllib.parse.quote(ds_path, safe="")
    discovered: dict[str, list[str]] = {}

    for short_name in types:
        resqml_type = RDDMS_TYPES.get(short_name)
        if not resqml_type:
            print(f"  WARN: Unknown type '{short_name}' - skipping")
            continue

        url = f"{host}/api/reservoir-ddms/v2/dataspaces/{enc}/resources/{resqml_type}"
        try:
            r = httpx.get(url, headers=headers, timeout=60)
            if r.status_code == 404:
                discovered[short_name] = []
                continue
            r.raise_for_status()
            items = r.json() or []
        except Exception as e:
            print(f"  WARN: Discovery failed for {short_name}: {e}")
            discovered[short_name] = []
            continue

        uuids = []
        for item in items:
            uid = item.get("Uuid") or item.get("UUID") or item.get("uuid") or ""
            if not uid:
                uri = item.get("uri", "")
                if "(" in uri:
                    uid = uri.split("(")[-1].rstrip(")'\"")
            if uid:
                uuids.append(uid)

        discovered[short_name] = uuids

    return discovered


def build_manifest(host: str, token: str, partition: str, uuids: list[str], *, ds_path: str = DATASPACE, uris: list[str] | None = None) -> dict:
    """Call RDDMS manifests/build for RESQML objects.

    Args:
        host: RDDMS host base URL.
        token: Bearer token.
        partition: Data partition ID.
        uuids: List of UUIDs (used only if uris is None - assumes Grid2d type).
        ds_path: Dataspace path for URI construction.
        uris: Pre-built EML URIs. If provided, uuids is ignored.
    """
    url = f"{host}/api/reservoir-ddms/v2/manifests/build"
    headers = {
        "Authorization": f"Bearer {token}",
        "data-partition-id": partition,
        "Content-Type": "application/json",
    }
    if uris is None:
        # Legacy mode: assume all are Grid2dRepresentation
        uris = [
            f"eml:///dataspace('{ds_path}')/resqml20.obj_Grid2dRepresentation({u})"
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
    parser.add_argument("--discover", action="store_true",
                        help="Dynamically discover objects instead of using hardcoded UUIDs")
    parser.add_argument("--types", default="Grid2d,PolylineSet,PointSet",
                        help="Comma-separated RESQML types to discover (default: Grid2d,PolylineSet,PointSet)")
    parser.add_argument("--dataspace", default=DATASPACE, help="RDDMS dataspace path")
    args = parser.parse_args()

    print("Loading env ...")
    env = load_env(args.env_file)
    host = env.get("host", "")
    partition = env.get("partition", "dev")
    print(f"  host={host}  partition={partition}")

    print("\nAuthenticating ...")
    token = get_token(env)

    # ── Determine which UUIDs to process ──
    eml_uris: list[str] | None = None  # None = legacy Grid2d-only mode

    if args.discover:
        type_list = [t.strip() for t in args.types.split(",")]
        print(f"\nDiscovering objects in dataspace '{args.dataspace}' ...")
        print(f"  Types: {', '.join(type_list)}")
        discovered = discover_objects(host, token, partition, args.dataspace, type_list)

        all_uuids: list[str] = []
        all_uris_display: list[str] = []
        eml_uris = []
        for short_name, uuids in discovered.items():
            resqml_type = RDDMS_TYPES.get(short_name, short_name)
            print(f"\n  {short_name} ({resqml_type}): {len(uuids)} object(s)")
            for u in uuids:
                all_uuids.append(u)
                all_uris_display.append(f"    [{short_name}] {u}")
                eml_uris.append(
                    f"eml:///dataspace('{args.dataspace}')/{resqml_type}({u})"
                )

        if not all_uuids:
            print("\n  No objects found. Exiting.")
            return
    else:
        all_uuids = list(SURFACES.values())
        all_uris_display = [f"  {name}: {uuid}" for name, uuid in SURFACES.items()]
        print(f"\nSurfaces ({len(all_uuids)}) - hardcoded mode:")
        for line in all_uris_display:
            print(line)

    print(f"\n  Total: {len(all_uuids)} object(s) to process")

    if args.dry_run:
        print("\n[dry-run] Would call POST /api/reservoir-ddms/v2/manifests/build")
        print(f"  dataspace: {args.dataspace}")
        print(f"  uris: {len(all_uuids)} objects")
        for line in all_uris_display:
            print(line)
        return

    print(f"\nCalling manifests/build for {len(all_uuids)} objects ...")
    manifest = build_manifest(host, token, partition, all_uuids, ds_path=args.dataspace, uris=eml_uris)

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
