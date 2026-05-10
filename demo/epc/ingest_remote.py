#!/usr/bin/env python3
"""
ingest_remote.py – Ingest the cleaned Drogon OSDU EPC into swedev RDDMS
and index the manifest in the OSDU catalog.

Steps:
  1. Create dataspace  maap/drogon_osdu  (with ACL + legal tag)
  2. Import EPC via ETP (osdu-etp-sslclient Docker image)
  3. Verify import via REST
  4. Generate OSDU catalog records from manifest
  5. Index records via Workflow API (Osdu_ingest)

Usage:
  cd demo/epc && python ingest_remote.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────── #
SCRIPT_DIR = Path(__file__).resolve().parent          # demo/epc/
DEMO_DIR   = SCRIPT_DIR.parent                        # demo/
REPO_ROOT  = DEMO_DIR.parent                          # ores/

sys.path.insert(0, str(DEMO_DIR))
from _auth import get_token, load_instance, _mint  # noqa: E402

# ── Config ────────────────────────────────────────────────────────────────── #
INSTANCE    = "swedev"
DATASPACE   = "maap/drogon"
EPC_FILE    = SCRIPT_DIR / "drogon.epc"
MANIFEST    = SCRIPT_DIR / "manifest_drogon_osdu.json"
IMAGE_SSL   = "osdu-etp-sslclient"

# OSDU ACL / Legal
LEGAL_TAG   = "dev-equinor-private-default"
OWNERS      = ["data.default.owners@dev.dataservices.energy"]
VIEWERS     = ["data.default.viewers@dev.dataservices.energy"]
COUNTRIES   = ["NO"]


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "data-partition-id": "dev",
        "Content-Type": "application/json",
    }


# ── Step 1: Create dataspace ──────────────────────────────────────────────── #
def create_dataspace(token: str, base: str) -> bool:
    """Create the dataspace via REST POST.  Returns True if created or exists."""
    import requests

    payload = [{
        "DataspaceId": DATASPACE,
        "Path": DATASPACE,
        "CustomData": {
            "legaltags": [LEGAL_TAG],
            "otherRelevantDataCountries": COUNTRIES,
            "owners": OWNERS,
            "viewers": VIEWERS,
        },
    }]
    r = requests.post(f"{base}/dataspaces", headers=_headers(token),
                      json=payload, timeout=30)
    if r.status_code in (200, 201):
        print(f"  ✓ Created dataspace {DATASPACE}")
        return True
    if r.status_code in (400, 409):
        print(f"  ✓ Dataspace {DATASPACE} already exists ({r.status_code})")
        return True
    if r.status_code in (401, 403):
        print(f"  ⚠ No PutDataspaces permission ({r.status_code})")
        print(f"    {r.text[:200]}")
        # Try ETP
        return create_dataspace_etp(token)
    print(f"  ✗ Failed to create dataspace: {r.status_code} {r.text[:300]}")
    return False


def create_dataspace_etp(token: str) -> bool:
    """Create dataspace via ETP (Docker client)."""
    inst = load_instance(INSTANCE)
    host = inst["host"].replace("https://", "").replace("http://", "").rstrip("/")
    etp_url = f"wss://{host}/api/reservoir-ddms-etp/v2/"

    xdata = json.dumps({
        "legaltags": [LEGAL_TAG],
        "otherRelevantDataCountries": COUNTRIES,
        "owners": OWNERS,
        "viewers": VIEWERS,
    })

    # Write token to a temp file to avoid shell escaping issues
    tok_file = SCRIPT_DIR / ".etp_token"
    tok_file.write_text(token)

    inner = (
        f"export JWT=$(cat /data/.etp_token) && "
        f"/bin/openETPServer space "
        f"--server-url {etp_url} "
        f"--data-partition-id dev "
        f"--auth bearer --jwt-token $JWT "
        f"--new -s {DATASPACE} "
        f"--xdata '{xdata}'"
    )
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{SCRIPT_DIR}:/data",
        "--entrypoint=sh", IMAGE_SSL, "-c", inner,
    ]
    print(f"  Creating via ETP...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    tok_file.unlink(missing_ok=True)

    if result.returncode == 0:
        print(f"  ✓ Created dataspace {DATASPACE} via ETP")
        return True
    # Check if "already exists"
    combined = result.stdout + result.stderr
    if "already exist" in combined.lower():
        print(f"  ✓ Dataspace {DATASPACE} already exists")
        return True
    print(f"  ✗ ETP create failed (rc={result.returncode})")
    print(f"    {combined[-300:]}")
    return False


# ── Step 2: Import EPC via ETP ────────────────────────────────────────────── #
def import_epc(token: str) -> bool:
    """Import the EPC file into the remote dataspace via ETP."""
    inst = load_instance(INSTANCE)
    host = inst["host"].replace("https://", "").replace("http://", "").rstrip("/")
    etp_url = f"wss://{host}/api/reservoir-ddms-etp/v2/"

    # Write token to file to avoid shell escaping issues with long JWTs
    tok_file = SCRIPT_DIR / ".etp_token"
    tok_file.write_text(token)

    inner = (
        f"export JWT=$(cat /data/.etp_token) && "
        f"/bin/openETPServer space "
        f"--server-url {etp_url} "
        f"--data-partition-id dev "
        f"--auth bearer --jwt-token $JWT "
        f"-s {DATASPACE} "
        f"--import-epc /data/{EPC_FILE.name} -j"
    )
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{SCRIPT_DIR}:/data",
        "--entrypoint=sh", IMAGE_SSL, "-c", inner,
    ]
    print(f"  Importing {EPC_FILE.name} → {DATASPACE}...")
    result = subprocess.run(cmd, text=True, timeout=600)
    tok_file.unlink(missing_ok=True)

    if result.returncode == 0:
        print(f"  ✓ EPC import succeeded")
        return True
    print(f"  ✗ EPC import failed (rc={result.returncode})")
    return False


# ── Step 3: Verify ────────────────────────────────────────────────────────── #
def verify_import(token: str, base: str) -> bool:
    import requests
    ds_enc = DATASPACE.replace("/", "%2F")
    r = requests.get(f"{base}/dataspaces/{ds_enc}/resources",
                     headers=_headers(token), timeout=30)
    if not r.ok:
        print(f"  ✗ Could not verify: {r.status_code}")
        return False
    resources = r.json()
    total = sum(t.get("count", 0) for t in resources)
    print(f"  ✓ {total} objects across {len(resources)} types in {DATASPACE}")
    for t in resources:
        print(f"    {t['name']}: {t['count']}")
    return total > 0


# ── Step 4+5: Build & index OSDU catalog records ─────────────────────────── #
def build_osdu_records(token: str) -> list[dict]:
    """Build OSDU catalog records from the EPC manifest."""
    with open(MANIFEST) as f:
        manifest = json.load(f)

    records = []
    for res in manifest.get("resources", []):
        uuid = res["uuid"]
        rtype = res["type"]          # e.g. "resqml20.obj_ContinuousProperty"
        title = res["title"]
        meta = res.get("metadata", {})

        # Map RESQML type to OSDU kind
        short_type = rtype.split(".")[-1]  # obj_ContinuousProperty
        osdu_kind = f"osdu:wks:work-product-component--ResqmlObject:1.0.0"

        record = {
            "kind": osdu_kind,
            "acl": {
                "viewers": VIEWERS,
                "owners": OWNERS,
            },
            "legal": {
                "legaltags": [LEGAL_TAG],
                "otherRelevantDataCountries": COUNTRIES,
            },
            "data": {
                "Name": title,
                "Description": meta.get("osdu:Description", f"RESQML {short_type}"),
                "ResourceTypeID": f"srn:type:{rtype}:",
                "ResourceID": f"srn:reference-data/ResqmlId:{uuid}:",
                "DataspaceID": DATASPACE,
                "ResqmlType": short_type,
                "ResqmlUUID": uuid,
                "ResqmlVersion": "2.0",
                "FieldName": meta.get("osdu:FieldName", "Drogon"),
                "Basin": meta.get("osdu:Basin", "Norwegian Continental Shelf"),
            },
        }

        # Add property-specific metadata
        if "osdu:PropertyKind" in meta:
            record["data"]["PropertyKindName"] = meta["osdu:PropertyKind"]
        if "osdu:UnitOfMeasure" in meta:
            record["data"]["UnitOfMeasure"] = meta["osdu:UnitOfMeasure"]
        if "osdu:QualityIndicator" in meta:
            record["data"]["QualityIndicator"] = meta["osdu:QualityIndicator"]

        records.append(record)

    print(f"  ✓ Built {len(records)} OSDU catalog records")
    return records


def index_via_workflow(token: str, base_osdu: str, records: list[dict]) -> bool:
    """Index records via the OSDU Workflow API (Osdu_ingest)."""
    import requests

    workflow_url = f"{base_osdu}/api/workflow/v1/workflow/Osdu_ingest/workflowRun"
    hdrs = _headers(token)

    # Chunk records into batches (max 100 per manifest)
    BATCH = 100
    total_ok = 0
    total_fail = 0

    for i in range(0, len(records), BATCH):
        batch = records[i:i + BATCH]
        manifest = {
            "kind": "osdu:wks:Manifest:1.0.0",
            "ReferenceData": [],
            "MasterData": [],
            "Data": {
                "WorkProductComponents": batch,
            },
        }
        body = {
            "executionContext": {
                "manifest": manifest,
                "Payload": {
                    "data-partition-id": "dev",
                    "AppKey": "test-app",
                },
            },
        }

        print(f"  Submitting batch {i//BATCH + 1} ({len(batch)} records)...")
        r = requests.post(workflow_url, headers=hdrs, json=body, timeout=60)

        if r.status_code in (200, 201, 202):
            run_id = r.json().get("runId", "?")
            print(f"    → Workflow run: {run_id}")

            # Poll for completion
            status = poll_workflow(token, base_osdu, run_id)
            if status in ("completed", "succeeded", "finished"):
                total_ok += len(batch)
                print(f"    ✓ Batch indexed ({status})")
            else:
                total_fail += len(batch)
                print(f"    ✗ Batch failed ({status})")
        else:
            total_fail += len(batch)
            print(f"    ✗ Submit failed: {r.status_code} {r.text[:200]}")

    print(f"\n  Results: {total_ok} indexed, {total_fail} failed")
    return total_fail == 0


def index_via_storage(token: str, base_osdu: str, records: list[dict]) -> bool:
    """Fallback: index records directly via Storage API PUT /records."""
    import requests

    storage_url = f"{base_osdu}/api/storage/v2/records"
    hdrs = _headers(token)

    BATCH = 100
    total_ok = 0
    total_fail = 0

    for i in range(0, len(records), BATCH):
        batch = records[i:i + BATCH]
        print(f"  PUT batch {i//BATCH + 1} ({len(batch)} records)...")
        r = requests.put(storage_url, headers=hdrs, json=batch, timeout=120)
        if r.ok:
            result = r.json()
            cnt = result.get("recordCount", len(batch))
            total_ok += cnt
            print(f"    ✓ {cnt} records stored")
        else:
            total_fail += len(batch)
            print(f"    ✗ {r.status_code}: {r.text[:200]}")

    print(f"\n  Results: {total_ok} stored, {total_fail} failed")
    return total_fail == 0


def poll_workflow(token: str, base_osdu: str, run_id: str,
                  timeout: int = 300, interval: int = 5) -> str:
    """Poll workflow run status until terminal state or timeout."""
    import requests

    url = f"{base_osdu}/api/workflow/v1/workflow/Osdu_ingest/workflowRun/{run_id}"
    hdrs = _headers(token)
    deadline = time.time() + timeout

    while time.time() < deadline:
        time.sleep(interval)
        r = requests.get(url, headers=hdrs, timeout=30)
        if not r.ok:
            continue
        status = r.json().get("status", "unknown").lower()
        if status in ("completed", "succeeded", "failed", "error",
                       "cancelled", "finished"):
            return status
        print(f"      poll: {status}...")
    return "timeout"


# ── Main ──────────────────────────────────────────────────────────────────── #
def _get_fresh_token() -> str:
    """Use SWEDEV_REFRESH_TOKEN from env if available (fresh token), else fall back to k8s."""
    fresh_rt = os.environ.get("SWEDEV_REFRESH_TOKEN", "")
    if fresh_rt:
        print(f"  Using SWEDEV_REFRESH_TOKEN from env ({len(fresh_rt)} chars)")
        inst = load_instance(INSTANCE)
        inst = dict(inst)  # make mutable copy
        inst["refresh_token"] = fresh_rt
        return _mint(inst, verbose=True)
    return get_token(INSTANCE, verbose=True)


def main():
    inst = load_instance(INSTANCE)
    host = inst["host"].replace("https://", "").replace("http://", "").rstrip("/")
    base_rddms = f"https://{host}/api/reservoir-ddms/v2"
    base_osdu = f"https://{host}"

    print(f"Target: {host}")
    print(f"Dataspace: {DATASPACE}")
    print(f"EPC: {EPC_FILE}")
    print()

    # Auth - prefer fresh env token
    print("=== 1. Authenticate ===")
    token = _get_fresh_token()
    if not token:
        sys.exit("Failed to get access token")
    print()

    # Create dataspace
    print("=== 2. Create dataspace ===")
    ok = create_dataspace(token, base_rddms)
    if not ok:
        print("  Continuing anyway (may already exist)...")
    print()

    # Import EPC
    print("=== 3. Import EPC ===")
    ok = import_epc(token)
    if not ok:
        print("  ✗ Import failed - check permissions")
        # Continue to try indexing anyway
    print()

    # Verify
    print("=== 4. Verify import ===")
    verify_import(token, base_rddms)
    print()

    # Build records
    print("=== 5. Build OSDU catalog records ===")
    records = build_osdu_records(token)
    print()

    # Index
    print("=== 6. Index in OSDU catalog ===")
    # Try workflow first, fallback to storage
    ok = index_via_workflow(token, base_osdu, records)
    if not ok:
        print("  Trying Storage API fallback...")
        ok = index_via_storage(token, base_osdu, records)

    if ok:
        print("\n✓ Done - all records indexed in OSDU catalog")
    else:
        print("\n⚠ Some records failed to index")


if __name__ == "__main__":
    main()
