#!/usr/bin/env python3
"""
manifest_ingest.py – Build OSDU manifest from local RDDMS and ingest
into the remote OSDU catalog.

Pipeline:
  1. Build OSDU manifest via local RDDMS REST API  (POST /manifests/build)
  2. Patch manifest records with ACLs, legal tags, data-partition-id
  3. Push manifest to remote OSDU catalog (Workflow or Storage API)

Prerequisites:
  - Local ETP server + RDDMS REST API containers running
    (see docker-compose.yaml or start manually)
  - Data already loaded into a local dataspace via ingest.sh
  - Remote auth: SWEDEV_REFRESH_TOKEN env or k8s/secret.yaml

Usage:
    python -m demo.epc.manifest_ingest                         # defaults
    python -m demo.epc.manifest_ingest --dataspace maap/drogon
    python -m demo.epc.manifest_ingest --save-only             # save manifest, don't push
    python -m demo.epc.manifest_ingest --dry-run               # show what would be pushed
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

try:
    import httpx
except ImportError:
    sys.exit("pip install httpx")

# ── Paths & imports ───────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
DEMO_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(DEMO_DIR))

from _auth import get_token, load_instance, _mint  # noqa: E402

# ── Defaults ──────────────────────────────────────────────────────────────
LOCAL_RDDMS = "http://localhost:3000/api/reservoir-ddms/v2"
INSTANCE = "swedev"
DATASPACE = "maap/drogon"

# OSDU ACL / Legal
LEGAL_TAG = "dev-equinor-private-default"
OWNERS = ["data.default.owners@dev.dataservices.energy"]
VIEWERS = ["data.default.viewers@dev.dataservices.energy"]
COUNTRIES = ["NO"]


# ═════════════════════════════════════════════════════════════════════════
# 1.  Build manifest from local RDDMS
# ═════════════════════════════════════════════════════════════════════════

def build_manifest(dataspace: str, local_base: str = LOCAL_RDDMS,
                   type_patterns: list[str] | None = None) -> dict:
    """Call POST /manifests/build on the local RDDMS REST API."""
    url = f"{local_base}/manifests/build"
    body: dict = {
        "uris": [f"eml:///dataspace('{dataspace}')"],
        "createMissingReferences": True,
    }
    if type_patterns:
        body["typePatterns"] = type_patterns

    print(f"  POST {url}")
    print(f"  uris: {body['uris']}")
    r = httpx.post(url, json=body,
                   headers={"Authorization": "Bearer local",
                            "Content-Type": "application/json"},
                   timeout=120)
    if r.status_code >= 300:
        print(f"  FAIL {r.status_code}: {r.text[:500]}")
        sys.exit(1)

    manifest = r.json()
    data = manifest.get("Data", {})
    counts = {k: len(v) for k, v in data.items()}
    print(f"  OK  kind={manifest.get('kind')}  {counts}")
    return manifest


# ═════════════════════════════════════════════════════════════════════════
# 2.  Patch manifest with ACLs / legal tags / DatasetIDs
# ═════════════════════════════════════════════════════════════════════════

def _patch_record(rec: dict, ds_id: str | None) -> dict:
    """Patch a single manifest record with ACL, legal, and DatasetID."""
    rec["acl"] = {"owners": OWNERS, "viewers": VIEWERS}
    rec["legal"] = {
        "legaltags": [LEGAL_TAG],
        "otherRelevantDataCountries": COUNTRIES,
        "status": "compliant",
    }
    # Link WPC → ETPDataspace via DatasetIDs  (fixes §1.6 in todo.txt)
    if ds_id and rec.get("kind", "").startswith("osdu:wks:work-product-component"):
        data = rec.setdefault("data", {})
        ddms = data.get("DDMSDatasets", [])
        if ddms:
            # DDMSDatasets already present from manifest builder — ensure
            # DatasetIDs links to the ETPDataspace record too
            if "DatasetIDs" not in data:
                data["DatasetIDs"] = [ds_id]
    return rec


def patch_manifest(manifest: dict) -> dict:
    """Patch all records inside the manifest with ACLs and DatasetIDs."""
    data = manifest.get("Data", {})

    # Find the ETPDataspace record ID (if any)
    ds_id = None
    for ds in data.get("Datasets", []):
        if "ETPDataspace" in ds.get("kind", ""):
            ds_id = ds.get("id")
            break

    patched = 0
    for section in data.values():
        if not isinstance(section, list):
            continue
        for rec in section:
            _patch_record(rec, ds_id)
            patched += 1

    print(f"  Patched {patched} records (ACL + legal)")
    if ds_id:
        print(f"  DatasetIDs → {ds_id}")
    return manifest


# ═════════════════════════════════════════════════════════════════════════
# 3.  Push manifest to remote OSDU catalog
# ═════════════════════════════════════════════════════════════════════════

def _remote_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "data-partition-id": "dev",
        "Content-Type": "application/json",
    }


def push_via_workflow(token: str, base_osdu: str,
                      manifest: dict) -> bool:
    """Push manifest via Workflow API (Osdu_ingest)."""
    url = f"{base_osdu}/api/workflow/v1/workflow/Osdu_ingest/workflowRun"
    hdrs = _remote_headers(token)
    body = {
        "executionContext": {
            "manifest": manifest,
            "Payload": {
                "data-partition-id": "dev",
                "AppKey": "ores-manifest-ingest",
            },
        },
    }

    print(f"  POST {url}")
    r = httpx.post(url, headers=hdrs, json=body, timeout=60)
    if r.status_code not in (200, 201, 202):
        print(f"  FAIL {r.status_code}: {r.text[:300]}")
        return False

    run_id = r.json().get("runId", "?")
    print(f"  Workflow run: {run_id}")
    status = _poll_workflow(token, base_osdu, run_id)
    print(f"  Final status: {status}")
    return status in ("completed", "succeeded", "finished")


def push_via_storage(token: str, base_osdu: str,
                     manifest: dict) -> bool:
    """Fallback: push records directly via Storage API PUT /records."""
    url = f"{base_osdu}/api/storage/v2/records"
    hdrs = _remote_headers(token)

    # Collect all records from manifest sections
    records: list[dict] = []
    for section in manifest.get("Data", {}).values():
        if isinstance(section, list):
            records.extend(section)

    BATCH = 100
    ok = 0
    fail = 0
    for i in range(0, len(records), BATCH):
        batch = records[i:i + BATCH]
        print(f"  PUT batch {i // BATCH + 1} ({len(batch)} records)...")
        r = httpx.put(url, headers=hdrs, json=batch, timeout=120)
        if r.is_success:
            cnt = r.json().get("recordCount", len(batch))
            ok += cnt
            print(f"    {cnt} stored")
        else:
            fail += len(batch)
            print(f"    FAIL {r.status_code}: {r.text[:200]}")

    print(f"  Results: {ok} stored, {fail} failed")
    return fail == 0


def _poll_workflow(token: str, base_osdu: str, run_id: str,
                   timeout: int = 300, interval: int = 5) -> str:
    """Poll workflow run until terminal state or timeout."""
    url = f"{base_osdu}/api/workflow/v1/workflow/Osdu_ingest/workflowRun/{run_id}"
    hdrs = _remote_headers(token)
    deadline = time.time() + timeout

    while time.time() < deadline:
        time.sleep(interval)
        try:
            r = httpx.get(url, headers=hdrs, timeout=30)
            if not r.is_success:
                continue
            status = r.json().get("status", "unknown").lower()
            if status in ("completed", "succeeded", "failed", "error",
                          "cancelled", "finished"):
                return status
            print(f"    poll: {status}...")
        except Exception:
            continue
    return "timeout"


# ═════════════════════════════════════════════════════════════════════════
# Auth helper
# ═════════════════════════════════════════════════════════════════════════

def get_remote_token() -> str:
    """Get a fresh access token for the remote OSDU instance."""
    import os
    fresh_rt = os.environ.get("SWEDEV_REFRESH_TOKEN", "")
    if fresh_rt:
        print(f"  Using SWEDEV_REFRESH_TOKEN ({len(fresh_rt)} chars)")
        inst = dict(load_instance(INSTANCE))
        inst["refresh_token"] = fresh_rt
        return _mint(inst, verbose=True)
    return get_token(INSTANCE, verbose=True)


# ═════════════════════════════════════════════════════════════════════════
# Main
# ═════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(
        description="Build OSDU manifest from local RDDMS and push to remote catalog")
    ap.add_argument("--dataspace", default=DATASPACE,
                    help=f"Local ETP dataspace name (default: {DATASPACE})")
    ap.add_argument("--local-rddms", default=LOCAL_RDDMS,
                    help=f"Local RDDMS REST API base URL (default: {LOCAL_RDDMS})")
    ap.add_argument("--type-patterns", nargs="*",
                    help="Restrict manifest to matching Energistics types (e.g. resqml20.obj_*Representation)")
    ap.add_argument("--save-only", action="store_true",
                    help="Save manifest JSON to file, don't push to remote")
    ap.add_argument("--dry-run", action="store_true",
                    help="Build and patch manifest, show summary, don't push")
    ap.add_argument("--storage", action="store_true",
                    help="Use Storage API instead of Workflow API for indexing")
    ap.add_argument("-o", "--output",
                    help="Output filename for saved manifest (default: manifest_<dataspace>.json)")
    args = ap.parse_args()

    ds = args.dataspace
    ds_safe = ds.replace("/", "_")

    print(f"{'=' * 60}")
    print(f"manifest_ingest — {ds}")
    print(f"{'=' * 60}")

    # ── Step 1: Build manifest locally ──
    print(f"\n=== 1. Build manifest from local RDDMS ===")
    manifest = build_manifest(ds, args.local_rddms, args.type_patterns)

    # ── Step 2: Patch ACLs ──
    print(f"\n=== 2. Patch ACLs & DatasetIDs ===")
    manifest = patch_manifest(manifest)

    # ── Summary ──
    data = manifest.get("Data", {})
    total = sum(len(v) for v in data.values() if isinstance(v, list))
    print(f"\n  Manifest summary:")
    for k, v in data.items():
        if isinstance(v, list):
            print(f"    {k}: {len(v)}")
    print(f"    Total: {total} records")

    # ── Save manifest ──
    out_file = args.output or SCRIPT_DIR / f"manifest_{ds_safe}.json"
    out_path = Path(out_file)
    out_path.write_text(json.dumps(manifest, indent=2))
    print(f"\n  Saved: {out_path}")

    if args.save_only:
        print("\n--save-only: done (manifest saved, not pushed)")
        return

    if args.dry_run:
        print("\n--dry-run: manifest ready, not pushed")
        return

    # ── Step 3: Authenticate to remote ──
    print(f"\n=== 3. Authenticate to remote ({INSTANCE}) ===")
    token = get_remote_token()
    if not token:
        sys.exit("Failed to get remote token")

    inst = load_instance(INSTANCE)
    host = inst["host"].replace("https://", "").replace("http://", "").rstrip("/")
    base_osdu = f"https://{host}"

    # ── Step 4: Push manifest ──
    print(f"\n=== 4. Push manifest to OSDU catalog ===")
    if args.storage:
        ok = push_via_storage(token, base_osdu, manifest)
    else:
        ok = push_via_workflow(token, base_osdu, manifest)
        if not ok:
            print("  Workflow failed, trying Storage API fallback...")
            ok = push_via_storage(token, base_osdu, manifest)

    if ok:
        print(f"\nDone — {total} records indexed in OSDU catalog")
    else:
        print(f"\nSome records failed to index")
        sys.exit(1)


if __name__ == "__main__":
    main()
