#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
peek_rddms_grid2d.py — Fetch a Grid2dRepresentation from the Reservoir DDMS
and display its structure: grid geometry, CRS, Z-value statistics.

This script demonstrates the data that lives *behind* DDMSDatasets[] on an
OSDU StructureMap or SeismicHorizon catalog record.  The OSDU record carries
only searchable metadata (name, interpretation link, grid parameters).  The
actual depth/time Z-value arrays live here in the RDDMS.

Usage:
  python peek_rddms_grid2d.py                          # all demo surfaces
  python peek_rddms_grid2d.py --uuid f857c36c-...      # specific UUID
  python peek_rddms_grid2d.py --list                   # list Grid2dReps in dataspace

Requires: httpx, ../../.env with OSDU credentials.
"""

import argparse
import json
import statistics
import sys
import urllib.parse
from pathlib import Path

import httpx

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent))
from _auth import load_env, mint_from_env  # noqa: E402

# ── RDDMS Configuration ────────────────────────────────────────────────
DATASPACE = "maap/drogon"
RDDMS_TYPE = "resqml20.obj_Grid2dRepresentation"

# Same UUIDs referenced by the demo StructureMap and SeismicHorizon records
DEMO_SURFACES = {
    "TopVolantis (Depth)":  "f857c36c-3939-4ff3-9125-a11cf2af105c",
    "BaseVolantis (Depth)": "0c6ab8e7-c793-4ab5-a88c-ccf457d9266d",
    "TopTherys (Depth)":    "0ce9278d-979c-450a-a3db-08ea96517463",
    "TopVolantis (TWT)":    "9deb9074-c4eb-44ff-990a-229bb545d442",
    "BaseVolantis (TWT)":   "efcf91f9-6e56-4bed-9e23-f0e9350a0b91",
}


# ── Auth ────────────────────────────────────────────────────────────────
def get_token(env: dict) -> str:
    return mint_from_env(env)


# ── RDDMS helpers ──────────────────────────────────────────────────────
def rddms_url(host: str, ds: str, path: str = "") -> str:
    enc = urllib.parse.quote(ds, safe="")
    return f"{host}/api/reservoir-ddms/v2/dataspaces/{enc}/resources{path}"


def fetch_grid2d_object(host: str, headers: dict, ds: str, uuid: str) -> dict:
    """Fetch the Grid2dRepresentation metadata object."""
    url = rddms_url(host, ds, f"/{RDDMS_TYPE}/{uuid}")
    r = httpx.get(url, headers=headers, timeout=60, verify=False)
    r.raise_for_status()
    raw = r.json()
    # RDDMS may return a list; pick the right object
    if isinstance(raw, list):
        for obj in raw:
            if isinstance(obj, dict):
                uid = obj.get("Uuid") or obj.get("UUID") or ""
                if str(uid).lower() == uuid.lower():
                    return obj
        return raw[0] if raw else {}
    return raw


def fetch_array_paths(host: str, headers: dict, ds: str, uuid: str) -> list:
    """List available array paths for a Grid2dRepresentation."""
    url = rddms_url(host, ds, f"/{RDDMS_TYPE}/{uuid}/arrays")
    r = httpx.get(url, headers=headers, timeout=60, verify=False)
    r.raise_for_status()
    return r.json() or []


def fetch_zvalues(host: str, headers: dict, ds: str, uuid: str, arr_path: str) -> list:
    """Fetch the Z-value array from the RDDMS."""
    enc_path = urllib.parse.quote(arr_path, safe="")
    url = rddms_url(host, ds, f"/{RDDMS_TYPE}/{uuid}/arrays/{enc_path}")
    r = httpx.get(url, headers=headers, timeout=120, verify=False)
    r.raise_for_status()
    body = r.json() or {}
    inner = body.get("data") or body
    if isinstance(inner, dict):
        return inner.get("data") or inner.get("values") or []
    elif isinstance(inner, list):
        return inner
    return []


def fetch_crs(host: str, headers: dict, ds: str, crs_ref: dict) -> dict | None:
    """Fetch the CRS object referenced by the Grid2dRepresentation."""
    crs_uuid = crs_ref.get("UUID") or crs_ref.get("Uuid")
    if not crs_uuid:
        return crs_ref.get("_data")
    ct = crs_ref.get("ContentType", "")
    crs_type = "resqml20.obj_LocalTime3dCrs" if "LocalTime3dCrs" in ct else "resqml20.obj_LocalDepth3dCrs"
    url = rddms_url(host, ds, f"/{crs_type}/{crs_uuid}")
    try:
        r = httpx.get(url, headers=headers, timeout=30, verify=False)
        r.raise_for_status()
        raw = r.json()
        if isinstance(raw, list):
            return raw[0] if raw else None
        return raw
    except Exception as e:
        print(f"  ⚠ CRS fetch failed: {e}")
        return None


def list_grid2d_in_dataspace(host: str, headers: dict, ds: str, top: int = 20):
    """List Grid2dRepresentation objects in a dataspace."""
    url = rddms_url(host, ds, f"/{RDDMS_TYPE}")
    r = httpx.get(url, headers=headers, timeout=60, verify=False, params={"$top": top})
    r.raise_for_status()
    items = r.json() or []
    if isinstance(items, dict):
        items = items.get("value") or items.get("items") or [items]
    return items


# ── Display ─────────────────────────────────────────────────────────────
def display_grid2d(name: str, grid: dict, crs: dict | None, zvalues: list, arrays_meta: list):
    """Pretty-print a Grid2dRepresentation summary."""
    print(f"\n{'='*72}")
    print(f"  {name}")
    print(f"{'='*72}")

    # Citation
    cit = grid.get("Citation") or {}
    print(f"\n  Title:       {cit.get('Title', '?')}")
    print(f"  UUID:        {grid.get('Uuid', '?')}")
    print(f"  SchemaVersion: {grid.get('SchemaVersion', '?')}")

    # Grid geometry from Grid2dPatch
    patch = grid.get("Grid2dPatch") or {}
    n_fast = patch.get("FastestAxisCount", "?")
    n_slow = patch.get("SlowestAxisCount", "?")
    print(f"\n  Grid dimensions:  {n_slow} (slow/J) × {n_fast} (fast/I)")

    geom = patch.get("Geometry") or {}
    points = geom.get("Points") or {}
    supporting = points.get("SupportingGeometry") or {}
    origin = supporting.get("Origin") or points.get("Origin") or {}
    offsets = supporting.get("Offset") or points.get("Offset") or []

    print(f"  Origin:      ({origin.get('Coordinate1', '?')}, {origin.get('Coordinate2', '?')}, {origin.get('Coordinate3', '?')})")
    for i, off in enumerate(offsets):
        dirn = off.get("Direction") or off.get("Offset") or {}
        spacing = off.get("Spacing") or {}
        count = spacing.get("Count", "?")
        value = spacing.get("Value", "?")
        print(f"  Offset[{i}]:   direction=({dirn.get('Coordinate1','?')}, {dirn.get('Coordinate2','?')}, {dirn.get('Coordinate3','?')})  spacing={value}  count={count}")

    # Referenced interpretation
    rep_obj = grid.get("RepresentedInterpretation") or grid.get("RepresentedObject") or {}
    if rep_obj:
        print(f"\n  RepresentedInterpretation:")
        print(f"    Title:     {rep_obj.get('Title', '?')}")
        print(f"    UUID:      {rep_obj.get('UUID') or rep_obj.get('Uuid', '?')}")
        print(f"    Type:      {rep_obj.get('QualifiedType') or rep_obj.get('ContentType', '?')}")

    # CRS
    if crs:
        print(f"\n  CRS (LocalCrs):")
        crs_cit = crs.get("Citation") or {}
        print(f"    Title:     {crs_cit.get('Title', '?')}")
        # Domain detection: RESQML 2.2 uses VerticalAxis.IsTime; 2.0 uses the type name
        crs_type = crs.get("$type", "")
        vert = crs.get("VerticalAxis") or {}
        is_time = vert.get("IsTime")
        if is_time is not None:
            domain = "Time (TWT)" if is_time else "Depth"
            print(f"    Domain:    {domain}  (VerticalAxis.IsTime = {is_time})")
        elif "LocalTime3dCrs" in crs_type:
            print(f"    Domain:    Time (TWT)  (type = {crs_type})")
        elif "LocalDepth3dCrs" in crs_type:
            print(f"    Domain:    Depth  (type = {crs_type})")
        vert_uom = crs.get("VerticalUom") or vert.get("Uom")
        if vert_uom:
            print(f"    Vert UOM:  {vert_uom}")
        proj_uom = crs.get("ProjectedUom")
        if proj_uom:
            print(f"    Proj UOM:  {proj_uom}")
        x_off = crs.get("XOffset")
        y_off = crs.get("YOffset")
        if x_off is not None:
            print(f"    XY offset: ({x_off}, {y_off})  ← add to local coords for projected XY")
        proj_crs = crs.get("ProjectedCrs") or {}
        epsg = proj_crs.get("EpsgCode") or proj_crs.get("LocalAuthorityCrs") or proj_crs.get("Unknown") or "?"
        print(f"    ProjCRS:   {epsg}")
        z_down = crs.get("ZIncreasingDownward")
        if z_down is not None:
            print(f"    Z down:    {z_down}")
    else:
        crs_ref_inner = geom.get("LocalCrs") or {}
        print(f"\n  CRS ref:     {crs_ref_inner.get('ContentType', '?')}  UUID={crs_ref_inner.get('UUID') or crs_ref_inner.get('Uuid','?')}")

    # Arrays metadata
    if arrays_meta:
        print(f"\n  Array paths ({len(arrays_meta)}):")
        for a in arrays_meta:
            uid = a.get("uid") or {}
            print(f"    {uid.get('pathInResource', '?')}")

    # Z-value statistics
    if zvalues:
        nums = [v for v in zvalues if isinstance(v, (int, float)) and v == v]  # exclude NaN
        print(f"\n  Z-values:    {len(zvalues)} total ({len(nums)} valid)")
        if nums:
            print(f"    min:       {min(nums):.2f}")
            print(f"    max:       {max(nums):.2f}")
            print(f"    mean:      {statistics.mean(nums):.2f}")
            print(f"    stdev:     {statistics.stdev(nums):.2f}" if len(nums) > 1 else "")
            print(f"    first 5:   {nums[:5]}")
    else:
        print(f"\n  Z-values:    (not fetched or empty)")

    print()


# ── Main ────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Peek at RDDMS Grid2dRepresentation objects")
    parser.add_argument("--env-file", default=str(REPO_ROOT / ".env"), help=".env file path")
    parser.add_argument("--dataspace", default=DATASPACE, help="RDDMS dataspace")
    parser.add_argument("--uuid", help="Specific Grid2dRep UUID to fetch")
    parser.add_argument("--list", action="store_true", help="List Grid2dReps in the dataspace")
    parser.add_argument("--no-zvalues", action="store_true", help="Skip fetching Z-value arrays")
    parser.add_argument("--json", action="store_true", help="Output full JSON instead of summary")
    args = parser.parse_args()

    print("Loading env ...")
    env = load_env([args.env_file])
    host = env["host"]
    print(f"  host={host}  partition={env['partition']}")

    print("\nAuthenticating ...")
    token = get_token(env)
    print("  OK")
    hdrs = {
        "Authorization": f"Bearer {token}",
        "data-partition-id": env["partition"],
        "Accept": "application/json",
    }

    ds = args.dataspace

    # --list mode
    if args.list:
        print(f"\nListing Grid2dRepresentations in dataspace '{ds}'...")
        items = list_grid2d_in_dataspace(host, hdrs, ds, top=100)
        print(f"  Found {len(items)} objects:\n")
        for obj in items:
            if isinstance(obj, dict):
                cit = obj.get("Citation") or {}
                uid = obj.get("Uuid") or obj.get("UUID", "?")
                title = cit.get("Title", "?")
                # Detect domain from CRS ref if present
                patch = obj.get("Grid2dPatch") or {}
                n_f = patch.get("FastestAxisCount", "?")
                n_s = patch.get("SlowestAxisCount", "?")
                print(f"  {uid}  {n_s}×{n_f}  {title}")
            else:
                print(f"  {obj}")
        return

    # Single UUID or all demo surfaces
    if args.uuid:
        surfaces = {"(specified)": args.uuid}
    else:
        surfaces = DEMO_SURFACES

    for name, uuid in surfaces.items():
        try:
            print(f"\nFetching {name} → {uuid} ...")
            grid = fetch_grid2d_object(host, hdrs, ds, uuid)

            if args.json:
                print(json.dumps(grid, indent=2))
                continue

            # Fetch CRS
            patch = grid.get("Grid2dPatch") or {}
            geom = patch.get("Geometry") or {}
            crs_ref = geom.get("LocalCrs") or {}
            crs = crs_ref.get("_data")
            if not crs:
                crs = fetch_crs(host, hdrs, ds, crs_ref)

            # Fetch arrays metadata
            arrays_meta = fetch_array_paths(host, hdrs, ds, uuid)

            # Fetch Z-values
            zvalues = []
            if not args.no_zvalues and arrays_meta:
                # Find the z-values array path
                arr_path = ""
                for a in arrays_meta:
                    uid = a.get("uid") or {}
                    pir = uid.get("pathInResource", "")
                    if "points_patch" in pir or "zvalues" in pir or "ZValues" in pir:
                        arr_path = pir
                        break
                if not arr_path and arrays_meta:
                    arr_path = (arrays_meta[0].get("uid") or {}).get("pathInResource", "")
                if arr_path:
                    print(f"  Fetching Z-values from: {arr_path}")
                    zvalues = fetch_zvalues(host, hdrs, ds, uuid, arr_path)

            display_grid2d(name, grid, crs, zvalues, arrays_meta)

        except httpx.HTTPStatusError as e:
            print(f"  HTTP {e.response.status_code}: {e.response.text[:300]}")
        except Exception as e:
            print(f"  Error: {e}")


if __name__ == "__main__":
    main()
