#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ingest_records_seisint.py — Ingest seisint records one-by-one via
the OSDU Storage API (PUT /api/storage/v2/records).

Records must first be split into individual files via
manifest2records_seisint.py.  Files are ingested in sorted order
(sequence prefix ensures dependency ordering).

Prerequisites:
  1. Run gen_volantis_interp.py           → manifest_volantis_interp.json
  2. Run manifest2records_seisint.py      → records/*.json
  3. Run register_m27_schemas.py          (M27 schemas — one-time, safe to re-run)
  5. Run THIS script                      → records ingested to OSDU

Usage:
  python ingest_records_seisint.py
  python ingest_records_seisint.py --env-file ../../.env
  python ingest_records_seisint.py --dry-run
  python ingest_records_seisint.py --start 5         # resume from record 5
  python ingest_records_seisint.py --delay 5         # 5s between PUTs
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import httpx

SCRIPT_DIR = Path(__file__).resolve().parent
RECORDS_DIR = SCRIPT_DIR / "records"
REPO_ROOT = SCRIPT_DIR.parent.parent

from _shared import load_env  # noqa: E402


# ── Auth ──────────────────────────────────────────────────────────────
def get_access_token(env: Dict[str, str]) -> str:
    url = f"https://login.microsoftonline.com/{env['tenant']}/oauth2/v2.0/token"
    form = {
        "grant_type":    "refresh_token",
        "client_id":     env["client_id"],
        "refresh_token": env["refresh_token"],
        "scope":         env["scope"],
    }
    r = httpx.post(url, data=form, timeout=30)
    if not r.is_success:
        raise RuntimeError(f"Auth failed ({r.status_code}): {r.text[:600]}")
    data = r.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"No access_token in response: {list(data.keys())}")
    print(f"  token acquired (expires_in={data.get('expires_in', '?')}s)")
    return token


# ── Record helpers ────────────────────────────────────────────────────
def load_records(records_dir: Path) -> List[Dict[str, Any]]:
    files = sorted(records_dir.glob("*.json"))
    records: List[Dict[str, Any]] = []
    for f in files:
        rec = json.loads(f.read_text(encoding="utf-8"))
        records.append(rec)
        kind = rec.get("kind", "?")
        name = rec.get("data", {}).get("Name", "")
        print(f"  loaded {f.name:50s}  {kind}  {name}")
    return records


# ── Storage API ───────────────────────────────────────────────────────
MAX_RETRIES = 4
RETRY_BACKOFF = [3, 6, 10, 15]


def put_one_record(env: Dict[str, str], record: Dict[str, Any],
                   client: httpx.Client) -> Dict[str, Any]:
    """PUT a single record, retrying on 404 (parent not yet indexed)."""
    url = f"{env['host']}/api/storage/v2/records"
    for attempt in range(MAX_RETRIES + 1):
        r = client.put(url, json=[record], timeout=60)
        if r.is_success:
            return r.json()
        if r.status_code == 404 and attempt < MAX_RETRIES:
            wait = RETRY_BACKOFF[attempt]
            print(f"        -> 404 parent not indexed yet — retry in {wait}s")
            time.sleep(wait)
            continue
        raise RuntimeError(f"PUT failed ({r.status_code}): {r.text[:1000]}")
    raise RuntimeError("Unreachable")


def put_records_batch(env: Dict[str, str], records: List[Dict[str, Any]],
                      client: httpx.Client) -> Dict[str, Any]:
    """PUT all records in a single call (up to 500 per OSDU limit)."""
    url = f"{env['host']}/api/storage/v2/records"
    r = client.put(url, json=records, timeout=120)
    if r.is_success:
        return r.json()
    raise RuntimeError(f"Batch PUT failed ({r.status_code}): {r.text[:1000]}")


# ── Main ──────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Ingest seisint records via Storage API"
    )
    ap.add_argument("--env-file", action="append", default=[],
                    help=".env file(s) with auth credentials (repeatable)")
    ap.add_argument("--records-dir", default=str(RECORDS_DIR))
    ap.add_argument("--dry-run", action="store_true",
                    help="Load and validate without sending")
    ap.add_argument("--start", type=int, default=0,
                    help="Skip records before this index (0-based)")
    ap.add_argument("--delay", type=float, default=3,
                    help="Seconds between PUTs (for indexing, default 3)")
    ap.add_argument("--batch", action="store_true",
                    help="Try single batch PUT first, fall back to sequential")
    args = ap.parse_args()

    env_files = args.env_file or [str(REPO_ROOT / ".env")]

    print("Loading env ...")
    env = load_env(env_files)
    print(f"  host={env['host']}  partition={env['partition']}")

    records_dir = Path(args.records_dir)
    if not records_dir.exists() or not list(records_dir.glob("*.json")):
        print(f"\nNo records in {records_dir}/")
        print("Run these first:")
        print("  python gen_volantis_interp.py")
        print("  python manifest2records_seisint.py")
        raise SystemExit(1)

    print(f"\nLoading records from {records_dir}:")
    records = load_records(records_dir)
    print(f"\n{len(records)} records loaded")

    if args.dry_run:
        print("\n[dry-run] Would PUT these records — exiting.")
        for i, r in enumerate(records):
            rid = r.get("id", "?")
            kind = r.get("kind", "?")
            print(f"  [{i:02d}] {kind}  {rid}")
        return

    print("\nAuthenticating ...")
    token = get_access_token(env)

    headers = {
        "Authorization":     f"Bearer {token}",
        "data-partition-id": env["partition"],
        "Content-Type":      "application/json",
    }
    created: List[str] = []
    skipped: List[str] = []
    failed:  List[str] = []

    active = records[args.start:]
    print(f"\nIngesting {len(active)} records ...")

    with httpx.Client(headers=headers) as client:
        if args.batch and args.start == 0:
            print(f"  Attempting single batch PUT ({len(active)} records) ...")
            try:
                resp = put_records_batch(env, active, client)
                created.extend(resp.get("recordIds", []))
                skipped.extend(resp.get("skippedRecordIds", []))
                print(f"  Batch OK — created={len(resp.get('recordIds', []))}  "
                      f"skipped={len(resp.get('skippedRecordIds', []))}")
                # If batch succeeds, skip sequential
                active = []
            except RuntimeError as e:
                print(f"  Batch PUT failed ({e}); falling back to sequential ...")

        for i, rec in enumerate(active):
            rid = rec.get("id", "?")
            short = rid.split("--")[-1][:50] if "--" in rid else rid[:50]
            try:
                r = put_one_record(env, rec, client)
                ids = r.get("recordIds", [])
                sk  = r.get("skippedRecordIds", [])
                created.extend(ids)
                skipped.extend(sk)
                tag = "OK  " if ids else "SKIP"
                print(f"  [{i+1:02d}/{len(active)}] {tag} {short}")
            except RuntimeError as e:
                failed.append(f"{rid}: {e}")
                print(f"  [{i+1:02d}/{len(active)}] FAIL {short}: {e}")
            if i < len(active) - 1:
                time.sleep(args.delay)

    print(f"\n--- Summary ---")
    print(f"  created/updated : {len(created)}")
    print(f"  skipped         : {len(skipped)}")
    print(f"  failed          : {len(failed)}")
    if created:
        print("\nCreated:")
        for rid in created:
            print(f"   {rid}")
    if failed:
        print("\nFailed:")
        for msg in failed:
            print(f"   {msg}")


if __name__ == "__main__":
    main()
