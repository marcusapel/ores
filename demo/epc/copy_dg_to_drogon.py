#!/usr/bin/env python3
"""
copy_dg_to_drogon.py – Copy missing RESQML objects from maap/drogon_dg
into maap/drogon via REST transactional API.

Reads all objects from drogon_dg, diffs against drogon, and PUTs the
missing/newer ones.  PUT is upsert so existing UUIDs get updated.

Usage:
    cd demo/epc && python3 copy_dg_to_drogon.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import quote

import httpx

# ── Config ────────────────────────────────────────────────────────────────── #
BASE = "https://equinorswedev.energy.azure.com/api/reservoir-ddms/v2"
SRC_DS = "maap/drogon_dg"
DST_DS = "maap/drogon"
SRC_ENC = quote(SRC_DS, safe="")
DST_ENC = quote(DST_DS, safe="")
BATCH_SIZE = 10  # conservative for PUT

TENANT = "3aa4a235-b6e2-48d5-9195-7fcf05b459b0"
CLIENT_ID = "ebd2bfee-ecba-47b7-a33c-017d0131879d"
SCOPE = "7daee810-3f78-40c4-84c2-7a199428de18/.default openid offline_access"
SCRIPT_DIR = Path(__file__).resolve().parent


# ── Auth ──────────────────────────────────────────────────────────────────── #
def mint_token() -> str:
    rt = os.environ.get("SWEDEV_REFRESH_TOKEN", "")
    if not rt:
        secret = SCRIPT_DIR.parent.parent / "k8s" / "secret.yaml"
        for line in secret.read_text().splitlines():
            s = line.strip()
            if s.startswith("INSTANCE_EQNDEV_REFRESH_TOKEN:"):
                rt = s.split(":", 1)[1].strip().strip('"').strip("'")
                break
    if not rt:
        sys.exit("No refresh token (set SWEDEV_REFRESH_TOKEN or k8s/secret.yaml)")
    r = httpx.post(
        f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token",
        data={"grant_type": "refresh_token", "client_id": CLIENT_ID,
              "refresh_token": rt, "scope": SCOPE},
        timeout=30,
    )
    data = r.json()
    if "error" in data:
        sys.exit(f"Auth error: {data['error']}: {data.get('error_description','')[:200]}")
    return data["access_token"]


# ── Helpers ───────────────────────────────────────────────────────────────── #
def list_type_summary(ds_enc: str, hdrs: dict) -> list[dict]:
    """Return [{name, count}, ...] for all types in a dataspace."""
    r = httpx.get(f"{BASE}/dataspaces/{ds_enc}/resources", headers=hdrs, timeout=60)
    r.raise_for_status()
    return r.json()  # list of {name, count}


def list_resources_by_type(ds_enc: str, obj_type: str, hdrs: dict) -> list[dict]:
    """Return [{uri, name, ...}, ...] listing of objects of one type."""
    items = []
    top = 1000
    skip = 0
    while True:
        r = httpx.get(
            f"{BASE}/dataspaces/{ds_enc}/resources/{obj_type}",
            headers=hdrs, params={"$top": str(top), "$skip": str(skip)},
            timeout=120,
        )
        if r.status_code != 200:
            print(f"  WARN: listing {obj_type} skip={skip}: {r.status_code}")
            break
        page = r.json()
        if isinstance(page, list):
            items.extend(page)
            if len(page) < top:
                break
            skip += top
        else:
            break
    return items


def fetch_object(ds_enc: str, obj_type: str, uuid: str, hdrs: dict) -> dict | None:
    """Fetch the full RDDMS JSON for a single object."""
    r = httpx.get(
        f"{BASE}/dataspaces/{ds_enc}/resources/{obj_type}/{uuid}",
        headers=hdrs, timeout=60,
    )
    if r.status_code != 200:
        return None
    data = r.json()
    if isinstance(data, list):
        return data[0] if data else None
    return data


def extract_uuid(uri: str) -> str:
    """Extract UUID from eml:///dataspace('x')/type(uuid)."""
    return uri.split("(")[-1].rstrip(")")


# ── Main ──────────────────────────────────────────────────────────────────── #
def main():
    print("=== Copy drogon_dg → drogon ===")
    print(f"  Source: {SRC_DS}")
    print(f"  Target: {DST_DS}")

    # 1. Authenticate
    print("\n1. Authenticating...")
    token = mint_token()
    hdrs = {
        "Authorization": f"Bearer {token}",
        "data-partition-id": "dev",
        "Content-Type": "application/json",
    }
    print("  OK")

    # 2. Read type summaries
    print("\n2. Reading type inventories...")
    src_types = {t["name"]: t["count"] for t in list_type_summary(SRC_ENC, hdrs)}
    dst_types = {t["name"]: t["count"] for t in list_type_summary(DST_ENC, hdrs)}
    src_total = sum(src_types.values())
    dst_total = sum(dst_types.values())
    print(f"  Source ({SRC_DS}): {src_total} objects, {len(src_types)} types")
    print(f"  Target ({DST_DS}): {dst_total} objects, {len(dst_types)} types")

    # 3. For each type in source, find UUIDs not in target
    print("\n3. Building diff (source UUIDs not in target)...")
    to_copy: list[tuple[str, str]] = []  # (obj_type, uuid)
    to_update: list[tuple[str, str]] = []  # objects in both — will upsert

    for obj_type in sorted(src_types.keys()):
        src_items = list_resources_by_type(SRC_ENC, obj_type, hdrs)
        src_uuids = {extract_uuid(it["uri"]): it for it in src_items}

        dst_items = list_resources_by_type(DST_ENC, obj_type, hdrs)
        dst_uuids = set(extract_uuid(it["uri"]) for it in dst_items)

        new = [u for u in src_uuids if u not in dst_uuids]
        existing = [u for u in src_uuids if u in dst_uuids]

        if new:
            for u in new:
                to_copy.append((obj_type, u))
            print(f"  {obj_type}: {len(new)} NEW (+ {len(existing)} already exist)")
        elif existing:
            # All exist — optionally upsert to ensure latest version
            for u in existing:
                to_update.append((obj_type, u))

    print(f"\n  Total NEW to copy: {len(to_copy)}")
    print(f"  Total existing (will upsert): {len(to_update)}")
    total_to_push = len(to_copy) + len(to_update)
    print(f"  Grand total to PUT: {total_to_push}")

    if total_to_push == 0:
        print("\n  Nothing to do — dataspaces already in sync.")
        return

    # 4. Fetch full objects from source
    print(f"\n4. Fetching {total_to_push} full objects from {SRC_DS}...")
    objects: list[dict] = []
    errors_fetch = 0

    all_items = to_copy + to_update
    for i, (obj_type, uuid) in enumerate(all_items):
        obj = fetch_object(SRC_ENC, obj_type, uuid, hdrs)
        if obj:
            objects.append(obj)
        else:
            errors_fetch += 1
            print(f"  WARN: failed to fetch {obj_type}/{uuid}")

        if (i + 1) % 50 == 0:
            print(f"  Fetched {i + 1}/{total_to_push}...")

    print(f"  Fetched {len(objects)} objects ({errors_fetch} errors)")

    # Show type distribution
    type_counts: dict[str, int] = {}
    for o in objects:
        t = o.get("$type", "?")
        type_counts[t] = type_counts.get(t, 0) + 1
    for t, c in sorted(type_counts.items()):
        print(f"    {t}: {c}")

    # 5. Test PUT a single object
    print(f"\n5. Test PUT...")
    test_obj = objects[0]
    r = httpx.post(f"{BASE}/dataspaces/{DST_ENC}/transactions", headers=hdrs, timeout=30)
    if r.status_code != 201:
        print(f"  FAIL begin tx: {r.status_code} {r.text[:300]}")
        sys.exit(1)
    tx_id = r.text.strip().strip('"')
    r = httpx.put(
        f"{BASE}/dataspaces/{DST_ENC}/resources",
        headers=hdrs, params={"transactionId": tx_id},
        json=[test_obj], timeout=60,
    )
    print(f"  PUT test ({test_obj.get('$type','?')}/{test_obj.get('Uuid','?')}): {r.status_code}")
    if r.status_code >= 400:
        print(f"    {r.text[:500]}")
        httpx.delete(f"{BASE}/dataspaces/{DST_ENC}/transactions/{tx_id}", headers=hdrs, timeout=30)
        sys.exit(1)
    httpx.delete(f"{BASE}/dataspaces/{DST_ENC}/transactions/{tx_id}", headers=hdrs, timeout=30)
    print(f"  Test OK — cancelled test tx")

    # 6. Full PUT in batches
    print(f"\n6. Full import ({len(objects)} objects in batches of {BATCH_SIZE})...")
    r = httpx.post(f"{BASE}/dataspaces/{DST_ENC}/transactions", headers=hdrs, timeout=30)
    if r.status_code != 201:
        print(f"  FAIL begin tx: {r.status_code} {r.text[:300]}")
        sys.exit(1)
    tx_id = r.text.strip().strip('"')
    print(f"  Transaction: {tx_id}")

    success = 0
    errors_put = 0
    for i in range(0, len(objects), BATCH_SIZE):
        batch = objects[i:i + BATCH_SIZE]
        batch_types = set(o.get("$type", "?") for o in batch)
        try:
            r = httpx.put(
                f"{BASE}/dataspaces/{DST_ENC}/resources",
                headers=hdrs, params={"transactionId": tx_id},
                json=batch, timeout=120,
            )
            if r.status_code < 300:
                success += len(batch)
                if (i // BATCH_SIZE) % 5 == 0:
                    print(f"  [{i}:{i+len(batch)}] OK  ({', '.join(sorted(batch_types))})")
            else:
                print(f"  [{i}:{i+len(batch)}] FAIL {r.status_code}: {r.text[:300]}")
                # Retry one-by-one
                for obj in batch:
                    r2 = httpx.put(
                        f"{BASE}/dataspaces/{DST_ENC}/resources",
                        headers=hdrs, params={"transactionId": tx_id},
                        json=[obj], timeout=60,
                    )
                    if r2.status_code < 300:
                        success += 1
                    else:
                        errors_put += 1
                        print(f"    FAIL {obj.get('$type','?')}/{obj.get('Uuid','?')}: {r2.status_code}")
        except Exception as e:
            errors_put += len(batch)
            print(f"  [{i}:{i+len(batch)}] EXCEPTION: {e}")

    print(f"\n  Results: {success} ok, {errors_put} errors")

    # 7. Commit or rollback
    if errors_put == 0 or success > errors_put:
        print(f"\n7. Committing...")
        r = httpx.put(f"{BASE}/dataspaces/{DST_ENC}/transactions/{tx_id}",
                      headers=hdrs, timeout=300)
        if r.status_code < 300:
            print(f"  COMMITTED ({r.status_code})")
        else:
            print(f"  COMMIT FAILED: {r.status_code} {r.text[:500]}")
    else:
        print(f"\n7. Rollback (too many errors)")
        httpx.delete(f"{BASE}/dataspaces/{DST_ENC}/transactions/{tx_id}",
                     headers=hdrs, timeout=30)
        sys.exit(1)

    # 8. Verify
    print(f"\n8. Verify...")
    final_types = list_type_summary(DST_ENC, hdrs)
    total = sum(t["count"] for t in final_types)
    print(f"  {DST_DS} now has {total} objects across {len(final_types)} types:")
    for t in sorted(final_types, key=lambda x: -x["count"]):
        marker = ""
        src_c = src_types.get(t["name"], 0)
        if src_c > 0 and t["count"] < src_c:
            marker = f"  (src has {src_c})"
        print(f"    {t['name']}: {t['count']}{marker}")

    print("\nDone.")


if __name__ == "__main__":
    main()
