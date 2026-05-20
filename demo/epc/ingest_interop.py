#!/usr/bin/env python3
"""
ingest_interop.py – Ingest the curated Drogon demo EPC into the ADME Interop
instance and generate/push the OSDU catalog manifest.

Target:
  Host:       admeinterop.energy.azure.com
  Partition:  opendes
  Dataspace:  demo/drogon
  EPC:        drogon_demo.epc (401 objects, 217 MB H5)

Steps:
  1. Authenticate (client_credentials via INSTANCE_INTEROP_* config)
  2. Create dataspace demo/drogon on interop RDDMS (REST, fallback ETP)
  3. Import drogon_demo.epc via ETP (osdu-etp-sslclient Docker image)
  4. Verify import via REST
  5. Build OSDU manifest from local RDDMS (/manifests/build)
  6. Patch manifest with interop ACLs + legal tags
  7. Save manifest (and optionally push to OSDU catalog)

Usage:
  cd demo/epc && python ingest_interop.py               # full pipeline
  python demo/epc/ingest_interop.py --save-only         # skip remote push
  python demo/epc/ingest_interop.py --skip-etp          # only build manifest
  python demo/epc/ingest_interop.py --dry-run           # no remote changes
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

try:
    import httpx
except ImportError:
    sys.exit("pip install httpx")

# ── Paths ─────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent          # demo/epc/
DEMO_DIR = SCRIPT_DIR.parent                          # demo/
sys.path.insert(0, str(DEMO_DIR))

from _auth import get_token, load_instance, _mint  # noqa: E402

# ── Config ────────────────────────────────────────────────────────────────
INSTANCE = "interop"
DATASPACE = "demo/drogon"
EPC_FILE = SCRIPT_DIR / "drogon_demo.epc"
LOCAL_RDDMS = "http://localhost:3000/api/reservoir-ddms/v2"
IMAGE_SSL = "osdu-etp-sslclient"

# Interop OSDU ACL / Legal (from configmap.yaml)
LEGAL_TAG = "opendes-default-legal-tag"
OWNERS = ["data.default.owners@opendes.dataservices.energy"]
VIEWERS = ["data.default.viewers@opendes.dataservices.energy"]
COUNTRIES = ["US"]
PARTITION = "opendes"


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "data-partition-id": PARTITION,
        "Content-Type": "application/json",
    }


# ═══════════════════════════════════════════════════════════════════════════
# 1. Auth
# ═══════════════════════════════════════════════════════════════════════════

def authenticate() -> str:
    """Get access token for interop instance (client_credentials)."""
    print("=== 1. Authenticate ===")
    token = get_token(INSTANCE, verbose=True)
    if not token:
        sys.exit("Failed to get access token for interop")
    return token


# ═══════════════════════════════════════════════════════════════════════════
# 2. Create dataspace
# ═══════════════════════════════════════════════════════════════════════════

def create_dataspace(token: str, base: str) -> bool:
    """Create demo/drogon dataspace on remote RDDMS via REST."""
    print("\n=== 2. Create dataspace ===")
    ds_enc = DATASPACE.replace("/", "%2F")
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
    r = httpx.post(f"{base}/dataspaces", headers=_headers(token),
                   json=payload, timeout=30)
    if r.status_code in (200, 201):
        print(f"  ✓ Created dataspace {DATASPACE}")
        return True
    if r.status_code in (400, 409):
        print(f"  ✓ Dataspace {DATASPACE} already exists ({r.status_code})")
        return True
    if r.status_code in (401, 403):
        print(f"  ⚠ REST create failed ({r.status_code}), trying ETP...")
        return create_dataspace_etp(token)
    print(f"  ✗ Failed: {r.status_code} {r.text[:300]}")
    return False


def create_dataspace_etp(token: str) -> bool:
    """Create dataspace via ETP client (fallback)."""
    inst = load_instance(INSTANCE)
    host = inst["host"].replace("https://", "").replace("http://", "").rstrip("/")
    etp_url = f"wss://{host}/api/reservoir-ddms-etp/v2/"

    xdata = json.dumps({
        "legaltags": [LEGAL_TAG],
        "otherRelevantDataCountries": COUNTRIES,
        "owners": OWNERS,
        "viewers": VIEWERS,
    })

    tok_file = SCRIPT_DIR / ".etp_token"
    tok_file.write_text(token)

    inner = (
        f"export JWT=$(cat /data/.etp_token) && "
        f"/bin/openETPServer space "
        f"--server-url {etp_url} "
        f"--data-partition-id {PARTITION} "
        f"--auth bearer --jwt-token $JWT "
        f"--new -s {DATASPACE} "
        f"--xdata '{xdata}'"
    )
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{SCRIPT_DIR}:/data",
        "--entrypoint=sh", IMAGE_SSL, "-c", inner,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    tok_file.unlink(missing_ok=True)

    combined = result.stdout + result.stderr
    if result.returncode == 0:
        print(f"  ✓ Created dataspace {DATASPACE} via ETP")
        return True
    if "already exist" in combined.lower():
        print(f"  ✓ Dataspace {DATASPACE} already exists")
        return True
    print(f"  ✗ ETP create failed (rc={result.returncode})")
    print(f"    {combined[-300:]}")
    return False


# ═══════════════════════════════════════════════════════════════════════════
# 3. Import EPC via ETP
# ═══════════════════════════════════════════════════════════════════════════

def import_epc(token: str) -> bool:
    """Import drogon_demo.epc into interop RDDMS via ETP."""
    print("\n=== 3. Import EPC via ETP ===")
    inst = load_instance(INSTANCE)
    host = inst["host"].replace("https://", "").replace("http://", "").rstrip("/")
    etp_url = f"wss://{host}/api/reservoir-ddms-etp/v2/"

    tok_file = SCRIPT_DIR / ".etp_token"
    tok_file.write_text(token)

    inner = (
        f"export JWT=$(cat /data/.etp_token) && "
        f"/bin/openETPServer space "
        f"--server-url {etp_url} "
        f"--data-partition-id {PARTITION} "
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
    print(f"  ETP URL: {etp_url}")
    result = subprocess.run(cmd, text=True, timeout=600)
    tok_file.unlink(missing_ok=True)

    if result.returncode == 0:
        print(f"  ✓ EPC import succeeded")
        return True
    print(f"  ✗ EPC import failed (rc={result.returncode})")
    return False


# ═══════════════════════════════════════════════════════════════════════════
# 4. Verify import
# ═══════════════════════════════════════════════════════════════════════════

def verify_import(token: str, base: str) -> bool:
    """Check resources in the remote dataspace."""
    print("\n=== 4. Verify import ===")
    ds_enc = DATASPACE.replace("/", "%2F")
    r = httpx.get(f"{base}/dataspaces/{ds_enc}/resources",
                  headers=_headers(token), timeout=30)
    if not r.is_success:
        print(f"  ⚠ Could not verify: {r.status_code} {r.text[:200]}")
        return False
    resources = r.json()
    if isinstance(resources, list):
        total = sum(t.get("count", 0) for t in resources)
        print(f"  ✓ {total} objects across {len(resources)} types")
        for t in resources:
            print(f"    {t.get('name', '?')}: {t.get('count', '?')}")
    else:
        print(f"  Response: {json.dumps(resources)[:300]}")
    return True


# ═══════════════════════════════════════════════════════════════════════════
# 5. Build OSDU manifest via RDDMS manifest builder
# ═══════════════════════════════════════════════════════════════════════════

def build_manifest_remote(token: str, base_rddms: str) -> dict:
    """Call POST /manifests/build on the interop RDDMS (after EPC import)."""
    print("\n=== 5. Build OSDU manifest (remote) ===")
    url = f"{base_rddms}/manifests/build"
    body = {
        "uris": [f"eml:///dataspace('{DATASPACE}')"],
        "createMissingReferences": True,
    }
    print(f"  POST {url}")
    print(f"  uris: {body['uris']}")

    r = httpx.post(url, json=body, headers=_headers(token), timeout=120)
    if r.status_code >= 300:
        print(f"  FAIL {r.status_code}: {r.text[:500]}")
        sys.exit(1)

    manifest = r.json()
    data = manifest.get("Data", {})
    counts = {k: len(v) for k, v in data.items() if isinstance(v, list)}
    print(f"  ✓ kind={manifest.get('kind')}  {counts}")
    return manifest


def build_manifest_local() -> dict:
    """Call POST /manifests/build on the local RDDMS REST API (fallback)."""
    print("\n=== 5. Build OSDU manifest (local) ===")
    url = f"{LOCAL_RDDMS}/manifests/build"
    body = {
        "uris": [f"eml:///dataspace('{DATASPACE}')"],
        "createMissingReferences": True,
    }
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
    counts = {k: len(v) for k, v in data.items() if isinstance(v, list)}
    print(f"  ✓ kind={manifest.get('kind')}  {counts}")
    return manifest


# ═══════════════════════════════════════════════════════════════════════════
# 6. Patch manifest for interop
# ═══════════════════════════════════════════════════════════════════════════

def patch_manifest(manifest: dict) -> dict:
    """Patch all records with interop ACLs, legal tags, and partition."""
    print("\n=== 6. Patch manifest for interop ===")
    data = manifest.get("Data", {})

    # Find the ETPDataspace record ID
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
            rec["acl"] = {"owners": OWNERS, "viewers": VIEWERS}
            rec["legal"] = {
                "legaltags": [LEGAL_TAG],
                "otherRelevantDataCountries": COUNTRIES,
                "status": "compliant",
            }
            # Link WPC → ETPDataspace
            if ds_id and rec.get("kind", "").startswith("osdu:wks:work-product-component"):
                rec_data = rec.setdefault("data", {})
                if "DatasetIDs" not in rec_data:
                    rec_data["DatasetIDs"] = [ds_id]
            patched += 1

    print(f"  Patched {patched} records (ACL + legal)")
    if ds_id:
        print(f"  DatasetIDs → {ds_id}")
    return manifest


# ═══════════════════════════════════════════════════════════════════════════
# 7. Save / Push manifest
# ═══════════════════════════════════════════════════════════════════════════

def save_manifest(manifest: dict, output: Path | None = None) -> Path:
    """Save manifest to disk."""
    out = output or SCRIPT_DIR / "manifest_demo_drogon_interop.json"
    out.write_text(json.dumps(manifest, indent=2))
    size_mb = out.stat().st_size / 1024 / 1024
    print(f"\n  Saved: {out} ({size_mb:.1f} MB)")
    return out


def push_via_workflow(token: str, base_osdu: str, manifest: dict) -> bool:
    """Push manifest via Workflow API (Osdu_ingest)."""
    url = f"{base_osdu}/api/workflow/v1/workflow/Osdu_ingest/workflowRun"
    hdrs = _headers(token)
    body = {
        "executionContext": {
            "manifest": manifest,
            "Payload": {
                "data-partition-id": PARTITION,
                "AppKey": "ores-drogon-ingest",
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


def push_via_storage(token: str, base_osdu: str, manifest: dict) -> bool:
    """Fallback: push records directly via Storage API PUT /records."""
    url = f"{base_osdu}/api/storage/v2/records"
    hdrs = _headers(token)

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
            print(f"    ✓ {cnt} stored")
        else:
            fail += len(batch)
            print(f"    ✗ {r.status_code}: {r.text[:200]}")

    print(f"  Results: {ok} stored, {fail} failed")
    return fail == 0


def _poll_workflow(token: str, base_osdu: str, run_id: str,
                   timeout: int = 300, interval: int = 5) -> str:
    """Poll workflow run until terminal state or timeout."""
    url = f"{base_osdu}/api/workflow/v1/workflow/Osdu_ingest/workflowRun/{run_id}"
    hdrs = _headers(token)
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


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(
        description="Ingest Drogon demo EPC into ADME Interop and build OSDU manifest")
    ap.add_argument("--skip-etp", action="store_true",
                    help="Skip ETP import (only build manifest)")
    ap.add_argument("--local-manifest", action="store_true",
                    help="Build manifest from local RDDMS instead of remote interop")
    ap.add_argument("--save-only", action="store_true",
                    help="Save manifest to file, don't push to remote catalog")
    ap.add_argument("--dry-run", action="store_true",
                    help="Build manifest but don't push or import")
    ap.add_argument("--storage", action="store_true",
                    help="Use Storage API instead of Workflow API for catalog indexing")
    ap.add_argument("-o", "--output", type=Path,
                    help="Output path for manifest JSON")
    args = ap.parse_args()

    print(f"{'═' * 60}")
    print(f"  Drogon Demo → ADME Interop ({DATASPACE})")
    print(f"  EPC: {EPC_FILE.name} (401 objects)")
    print(f"  Target: admeinterop.energy.azure.com / {PARTITION}")
    print(f"{'═' * 60}\n")

    # ── Auth (only needed for remote operations) ──
    token = None
    need_remote = not args.save_only and not args.dry_run
    if need_remote or not args.skip_etp:
        token = authenticate()

    if not args.dry_run and not args.skip_etp:
        if not token:
            token = authenticate()
        # ── Create dataspace ──
        inst = load_instance(INSTANCE)
        host = inst["host"].replace("https://", "").replace("http://", "").rstrip("/")
        base_rddms = f"https://{host}/api/reservoir-ddms/v2"

        create_dataspace(token, base_rddms)

        # ── Import EPC ──
        ok = import_epc(token)
        if not ok:
            print("  ⚠ ETP import failed - continuing with manifest build")

        # ── Verify ──
        verify_import(token, base_rddms)

    # ── Build manifest ──
    if args.local_manifest or args.skip_etp:
        manifest = build_manifest_local()
    else:
        # Use remote interop manifest builder (authoritative after import)
        if not token:
            token = authenticate()
        inst = load_instance(INSTANCE)
        host = inst["host"].replace("https://", "").replace("http://", "").rstrip("/")
        base_rddms = f"https://{host}/api/reservoir-ddms/v2"
        manifest = build_manifest_remote(token, base_rddms)

    # ── Patch for interop ──
    manifest = patch_manifest(manifest)

    # ── Summary ──
    data = manifest.get("Data", {})
    total = sum(len(v) for v in data.values() if isinstance(v, list))
    print(f"\n  Manifest summary:")
    for k, v in data.items():
        if isinstance(v, list):
            print(f"    {k}: {len(v)}")
    print(f"    Total: {total} records")

    # ── Save ──
    out_path = save_manifest(manifest, args.output)

    if args.save_only or args.dry_run:
        print(f"\n{'─' * 60}")
        print(f"  Done (manifest saved, not pushed)")
        return

    # ── Push to OSDU catalog ──
    print(f"\n=== 7. Push manifest to OSDU catalog ===")
    if not token:
        token = authenticate()
    inst = load_instance(INSTANCE)
    host = inst["host"].replace("https://", "").replace("http://", "").rstrip("/")
    base_osdu = f"https://{host}"

    if args.storage:
        ok = push_via_storage(token, base_osdu, manifest)
    else:
        ok = push_via_workflow(token, base_osdu, manifest)
        if not ok:
            print("  Workflow failed, trying Storage API fallback...")
            ok = push_via_storage(token, base_osdu, manifest)

    if ok:
        print(f"\n{'═' * 60}")
        print(f"  ✓ {total} records indexed in OSDU catalog")
    else:
        print(f"\n  ⚠ Some records failed to index")
        sys.exit(1)


if __name__ == "__main__":
    main()
