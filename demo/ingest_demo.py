#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ingest_demo.py - Unified ingestion pipeline for the full ORES demo dataset.

Ingests Drogon DG1, DG2, SeisInt, and Strat data into **any** OSDU instance.
Records are generated with the ``dev`` partition prefix; this script rewrites
IDs, ACL, legal tags, and all embedded references at runtime to match the
target instance.

Supports two ingestion modes (tried in order):
  1. Manifest mode  - POST manifest to Workflow API (Osdu_ingest)
  2. Storage mode   - PUT records directly via Storage API (fallback)

Data sources (read from the repo, nothing pre-shipped):
  demo/drogon/          DG1 manifests  (13 manifests, 22 records)
  demo/drogon_dg2/      DG2 manifests  (11 manifests, 110+ records)
  demo/seisint/         Seismic interpretation manifests (5 manifests)
  demo/strat/           Stratigraphic manifests (3 manifests, 300+ records)

Usage:
  # Ingest everything into eqndev (our default instance):
  python demo/ingest_demo.py

  # Ingest into a named instance (reads INSTANCE_<NAME>_* from k8s/ or env):
  python demo/ingest_demo.py --target preship

  # Override partition and ACL directly (no instance config needed):
  python demo/ingest_demo.py --target custom \\
      --partition opendes \\
      --host https://osdu-ship.msft-osdu-test.org \\
      --legal-tag opendes-public-usa-dataset-1 \\
      --owners data.default.owners@opendes.contoso.com \\
      --viewers data.default.viewers@opendes.contoso.com \\
      --countries US

  # Only certain datasets:
  python demo/ingest_demo.py --only dg1 strat

  # Dry-run (no changes):
  python demo/ingest_demo.py --dry-run

  # Force storage-only mode (skip Workflow API attempt):
  python demo/ingest_demo.py --storage-only

  # Force manifest-only mode (fail if Workflow API is unavailable):
  python demo/ingest_demo.py --manifest-only
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx

# ── Paths ─────────────────────────────────────────────────────────────
SCRIPT_DIR    = Path(__file__).resolve().parent          # demo/
REPO_ROOT     = SCRIPT_DIR.parent
DG1_DIR       = SCRIPT_DIR / "drogon"
DG2_DIR       = SCRIPT_DIR / "drogon_dg2"
SEISINT_DIR   = SCRIPT_DIR / "seisint"
STRAT_DIR     = SCRIPT_DIR / "strat"

SEISINT_SCHEMAS_DIR = SEISINT_DIR / "schemas" / "resolved"
DEVCONCEPT_SCHEMA   = DG1_DIR / "schema_devconcept.json"

# Source partition (all generators produce records with this prefix)
SRC_PARTITION = "dev"
SRC_HOST      = "equinorswedev.energy.azure.com"

# ── Target instance config (populated at startup) ────────────────────
TARGET: Dict[str, Any] = {}


# ═══════════════════════════════════════════════════════════════════════
#  Auth
# ═══════════════════════════════════════════════════════════════════════

sys.path.insert(0, str(SCRIPT_DIR))
from _auth import load_instance as _load_inst, mint_from_env  # noqa: E402

_cached_token: Optional[str] = None
_cached_exp: float = 0.0


def load_instance_config(name: str) -> Dict[str, Any]:
    """Load instance config via _auth resolution chain.

    Falls back to sensible defaults for legal tags, ACL, etc.
    """
    inst = _load_inst(name)
    p = inst["partition"]
    return {
        "host":          inst["host"],
        "partition":     p,
        "tenant":        inst["tenant"],
        "client_id":     inst["client_id"],
        "client_secret": inst.get("client_secret", ""),
        "refresh_token": inst.get("refresh_token", ""),
        "scope":         inst.get("scope") or f"{inst['client_id']}/.default",
        "legal_tag":     inst.get("legal_tag") or f"{p}-public-usa-dataset-1",
        "owners":        inst.get("owners") or [f"data.default.owners@{p}.dataservices.energy"],
        "viewers":       inst.get("viewers") or [f"data.default.viewers@{p}.dataservices.energy"],
        "countries":     inst.get("countries") or ["US"],
    }


def get_access_token() -> str:
    """Mint an access token (cached for ~50 min)."""
    global _cached_token, _cached_exp
    if _cached_token and time.time() < _cached_exp:
        return _cached_token
    env = {
        "tenant":        TARGET["tenant"],
        "client_id":     TARGET["client_id"],
        "client_secret": TARGET.get("client_secret", ""),
        "refresh_token": TARGET.get("refresh_token", ""),
        "scope":         TARGET["scope"],
    }
    _cached_token = mint_from_env(env)
    _cached_exp = time.time() + 3000
    return _cached_token


def api_headers() -> dict:
    return {
        "Authorization":     f"Bearer {get_access_token()}",
        "Content-Type":      "application/json",
        "data-partition-id": TARGET["partition"],
    }


# ═══════════════════════════════════════════════════════════════════════
#  Record transformation  (dev → target partition)
# ═══════════════════════════════════════════════════════════════════════

def transform_record(rec: dict) -> dict:
    """Rewrite a record's ID, kind, ACL, legal, and embedded refs
    from SRC_PARTITION to the target partition."""

    tgt = TARGET["partition"]

    # 1. ID: dev:kind--Entity:uuid:1 → <target>:kind--Entity:uuid:1
    rid = rec.get("id", "")
    if rid.startswith(f"{SRC_PARTITION}:"):
        rec["id"] = f"{tgt}:{rid[len(SRC_PARTITION)+1:]}"

    # 2. Kind: dev:wks:... → <target>:wks:... (only if partition-scoped)
    kind = rec.get("kind", "")
    if kind.startswith(f"{SRC_PARTITION}:"):
        rec["kind"] = f"{tgt}:{kind[len(SRC_PARTITION)+1:]}"

    # 3. ACL
    rec["acl"] = {
        "owners": TARGET["owners"][:],
        "viewers": TARGET["viewers"][:],
    }

    # 4. Legal
    rec["legal"] = {
        "legaltags": [TARGET["legal_tag"]],
        "otherRelevantDataCountries": TARGET["countries"][:],
    }

    # 5. Embedded partition refs in data.*
    _rewrite_refs(rec.get("data", {}))

    return rec


def _rewrite_refs(obj: Any) -> None:
    """Recursively rewrite SRC_PARTITION: → TARGET partition: in strings."""
    tgt = TARGET["partition"]
    tgt_host = TARGET.get("host", "").replace("https://", "").replace("http://", "").rstrip("/")

    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str):
                if v.startswith(f"{SRC_PARTITION}:"):
                    obj[k] = f"{tgt}:{v[len(SRC_PARTITION)+1:]}"
                elif SRC_HOST in v and tgt_host:
                    obj[k] = v.replace(SRC_HOST, tgt_host)
            elif isinstance(v, (dict, list)):
                _rewrite_refs(v)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if isinstance(item, str):
                if item.startswith(f"{SRC_PARTITION}:"):
                    obj[i] = f"{tgt}:{item[len(SRC_PARTITION)+1:]}"
                elif SRC_HOST in item and tgt_host:
                    obj[i] = item.replace(SRC_HOST, tgt_host)
            elif isinstance(item, (dict, list)):
                _rewrite_refs(item)


def transform_manifest(manifest: dict) -> dict:
    """Transform every record inside a manifest envelope."""
    import copy
    m = copy.deepcopy(manifest)
    for grp in ("ReferenceData", "MasterData"):
        for rec in m.get(grp, []):
            if isinstance(rec, dict) and "data" in rec:
                transform_record(rec)
    data = m.get("Data", {})
    wp = data.get("WorkProduct")
    if isinstance(wp, dict) and wp.get("data"):
        transform_record(wp)
    for grp in ("WorkProductComponents", "Datasets"):
        for rec in data.get(grp, []):
            if isinstance(rec, dict) and "data" in rec:
                transform_record(rec)
    return m


# ═══════════════════════════════════════════════════════════════════════
#  Schema registration (only needed for non-standard schemas)
# ═══════════════════════════════════════════════════════════════════════

KIND_RE = re.compile(
    r"^(?P<authority>[^:]+):(?P<source>[^:]+):(?P<entityType>[^:]+)"
    r":(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$"
)


def _parse_kind(kind: str) -> dict:
    m = KIND_RE.match(kind)
    if not m:
        raise ValueError(f"Cannot parse kind: {kind!r}")
    return {
        "authority":          m.group("authority"),
        "source":             m.group("source"),
        "entityType":         m.group("entityType"),
        "schemaVersionMajor": int(m.group("major")),
        "schemaVersionMinor": int(m.group("minor")),
        "schemaVersionPatch": int(m.group("patch")),
    }


def _schema_exists(client: httpx.Client, kind: str) -> bool:
    r = client.get(
        f"{TARGET['host']}/api/schema-service/v1/schema/{kind}",
        headers=api_headers(), timeout=30,
    )
    return r.status_code == 200


def _register_schema(client: httpx.Client, body: dict, *,
                     authority_override: Optional[str] = None,
                     dry_run: bool = False) -> bool:
    kind = body.get("x-osdu-schema-source", "")
    if not kind:
        return False
    identity = _parse_kind(kind)
    if authority_override:
        identity["authority"] = authority_override

    label = f"{identity['authority']}:{identity['source']}:{identity['entityType']}:{identity['schemaVersionMajor']}.{identity['schemaVersionMinor']}.{identity['schemaVersionPatch']}"

    if dry_run:
        print(f"    [dry-run] would register {label}")
        return True

    payload = {
        "schemaInfo": {"schemaIdentity": identity, "status": "DEVELOPMENT"},
        "schema": body,
    }
    r = client.put(
        f"{TARGET['host']}/api/schema-service/v1/schema",
        json=payload, headers=api_headers(), timeout=60,
    )
    if r.status_code in (200, 201):
        print(f"    ✓ registered {label}")
        return True
    if r.status_code == 409:
        print(f"    ≈ {label} already exists")
        return True
    print(f"    ✗ {label}: {r.status_code} {r.text[:200]}")
    return False


def register_missing_schemas(client: httpx.Client, dry_run: bool = False) -> None:
    """Register non-standard schemas that may be missing on the target."""
    print("\n── Registering missing schemas ──")

    # SeisInt resolved schemas
    if SEISINT_SCHEMAS_DIR.is_dir():
        for f in sorted(SEISINT_SCHEMAS_DIR.glob("*.json")):
            body = json.loads(f.read_text(encoding="utf-8"))
            kind = body.get("x-osdu-schema-source", "")
            if kind and not _schema_exists(client, kind):
                _register_schema(client, body, dry_run=dry_run)
            else:
                print(f"    ≈ {kind or f.name} ok")

    # DevelopmentConcept custom schema
    if DEVCONCEPT_SCHEMA.exists():
        body = json.loads(DEVCONCEPT_SCHEMA.read_text(encoding="utf-8"))
        kind = body.get("x-osdu-schema-source", "")
        tgt_kind = kind.replace(f"{SRC_PARTITION}:", f"{TARGET['partition']}:") if kind.startswith(f"{SRC_PARTITION}:") else kind
        if not _schema_exists(client, tgt_kind) and not _schema_exists(client, kind):
            _register_schema(client, body, authority_override=TARGET["partition"], dry_run=dry_run)
        else:
            print(f"    ≈ {kind} ok")

    # ActivityTemplate + work-product (standard OSDU, sometimes missing)
    for missing_kind in [
        "osdu:wks:work-product-component--ActivityTemplate:1.0.0",
        "osdu:wks:work-product:1.0.0",
    ]:
        if not _schema_exists(client, missing_kind):
            parts = _parse_kind(missing_kind)
            minimal = {
                "x-osdu-schema-source": missing_kind,
                "$schema": "http://json-schema.org/draft-07/schema#",
                "title": parts["entityType"],
                "type": "object",
                "additionalProperties": True,
            }
            _register_schema(client, minimal, dry_run=dry_run)
        else:
            print(f"    ≈ {missing_kind} ok")

    # LocalBoundaryFeature
    lbf = SEISINT_DIR / "schemas" / "LocalBoundaryFeature.1.1.0.json"
    lbf_kind = "osdu:wks:master-data--LocalBoundaryFeature:1.1.0"
    if lbf.exists() and not _schema_exists(client, lbf_kind):
        _register_schema(client, json.loads(lbf.read_text(encoding="utf-8")), dry_run=dry_run)
    else:
        print(f"    ≈ {lbf_kind} ok")


# ═══════════════════════════════════════════════════════════════════════
#  Manifest helpers
# ═══════════════════════════════════════════════════════════════════════

def load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def extract_records(manifest: dict) -> List[dict]:
    """All record objects from a manifest envelope."""
    recs: List[dict] = []
    for grp in ("ReferenceData", "MasterData"):
        for r in manifest.get(grp, []):
            if isinstance(r, dict) and "data" in r:
                recs.append(r)
    data = manifest.get("Data", {})
    wp = data.get("WorkProduct")
    if isinstance(wp, dict) and wp.get("data"):
        recs.append(wp)
    for grp in ("WorkProductComponents", "Datasets"):
        for r in data.get(grp, []):
            if isinstance(r, dict) and "data" in r:
                recs.append(r)
    return recs


# ═══════════════════════════════════════════════════════════════════════
#  Ingestion - Workflow API (manifest mode)
# ═══════════════════════════════════════════════════════════════════════
WORKFLOW_ID = "Osdu_ingest"


def _wf_submit(client: httpx.Client, manifest: dict) -> str:
    """POST manifest to Workflow API. Returns runId."""
    url = f"{TARGET['host']}/api/workflow/v1/workflow/{WORKFLOW_ID}/workflowRun"
    payload = {
        "executionContext": {
            "Payload": {
                "AppKey": "ingest_demo.py",
                "data-partition-id": TARGET["partition"],
            },
            "manifest": manifest,
        },
    }
    r = client.post(
        url, content=json.dumps(payload),
        headers={**api_headers(), "x-correlation-id": str(uuid.uuid4())},
        timeout=120,
    )
    if not r.is_success:
        raise RuntimeError(f"Workflow submit failed ({r.status_code}): {r.text[:400]}")
    body = r.json()
    run_id = str(body.get("runId") or body.get("id") or "")
    if not run_id:
        raise RuntimeError(f"No runId in: {json.dumps(body)[:300]}")
    return run_id


def _wf_poll(client: httpx.Client, run_id: str,
             poll_interval: float = 10.0, max_wait: float = 300.0) -> str:
    """Poll Workflow API for completion. Returns final status string."""
    paths = [
        f"/api/workflow/v1/workflow/{WORKFLOW_ID}/workflowRun/{run_id}",
        f"/api/workflow/v1/workflowRun/{run_id}",
    ]
    terminal = {"completed", "succeeded", "failed", "error", "cancelled", "finished"}
    start = time.time()
    while True:
        for p in paths:
            try:
                r = client.get(f"{TARGET['host']}{p}", headers=api_headers(), timeout=60)
                if r.is_success:
                    obj = r.json()
                    status = str(obj.get("status") or obj.get("workflowRunStatus") or obj.get("state") or "").lower()
                    if status:
                        elapsed = int(time.time() - start)
                        print(f"      poll: status={status} ({elapsed}s)")
                    if status in terminal:
                        return status
                    break
            except httpx.TimeoutException:
                pass
        if time.time() - start > max_wait:
            return "timeout"
        time.sleep(poll_interval)


def ingest_via_workflow(client: httpx.Client, manifest: dict,
                        label: str, *, dry_run: bool = False) -> bool:
    """Try ingesting via Workflow API. Returns True on success."""
    records = extract_records(manifest)
    print(f"    Workflow API: submitting {len(records)} records …")

    if dry_run:
        for r in records:
            print(f"      [dry-run] {r.get('id', '?')[:70]}")
        return True

    try:
        run_id = _wf_submit(client, manifest)
        print(f"      runId = {run_id}")
        status = _wf_poll(client, run_id)
        if status in ("completed", "succeeded", "finished"):
            print(f"      ✓ Workflow {status}")
            return True
        print(f"      ✗ Workflow {status}")
        return False
    except Exception as e:
        print(f"      ✗ Workflow error: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════
#  Ingestion - Storage API (record mode)
# ═══════════════════════════════════════════════════════════════════════
MAX_RETRIES = 4
RETRY_BACKOFF = [3, 6, 10, 15]
BATCH_LIMIT = 500


def ingest_via_storage(client: httpx.Client, records: List[dict],
                       label: str, *, dry_run: bool = False) -> dict:
    """PUT records directly to Storage API. Returns summary dict."""
    print(f"    Storage API: {len(records)} records …")

    if dry_run:
        for r in records:
            print(f"      [dry-run] {r.get('id', '?')[:70]}")
        return {"created": 0, "skipped": 0, "failed": 0}

    created, skipped, failed = [], [], []
    url = f"{TARGET['host']}/api/storage/v2/records"

    # Chunk if > BATCH_LIMIT
    chunks = [records[i:i + BATCH_LIMIT] for i in range(0, len(records), BATCH_LIMIT)]

    for ci, chunk in enumerate(chunks):
        chunk_label = f"[{ci+1}/{len(chunks)}]" if len(chunks) > 1 else ""

        # Try batch PUT
        ok = False
        for attempt in range(MAX_RETRIES + 1):
            resp = client.put(url, json=chunk, headers=api_headers(), timeout=120)
            if resp.is_success:
                body = resp.json()
                created.extend(body.get("recordIds", []))
                skipped.extend(body.get("skippedRecordIds", []))
                print(f"      ✓ batch{chunk_label}: created={len(body.get('recordIds',[]))}  "
                      f"skipped={len(body.get('skippedRecordIds',[]))}")
                ok = True
                break
            if resp.status_code == 404 and attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF[attempt]
                print(f"      404 (attempt {attempt+1}) – retry in {wait}s …")
                time.sleep(wait)
                continue
            break

        if ok:
            continue

        # Batch failed → sequential fallback
        print(f"      batch{chunk_label} failed ({resp.status_code}), sequential fallback …")
        for i, rec in enumerate(chunk):
            rid = rec.get("id", "?")
            short = rid.split(":")[-1][:35] if ":" in rid else rid[:40]
            rec_ok = False
            for attempt in range(MAX_RETRIES + 1):
                resp = client.put(url, json=[rec], headers=api_headers(), timeout=60)
                if resp.is_success:
                    r = resp.json()
                    created.extend(r.get("recordIds", []))
                    skipped.extend(r.get("skippedRecordIds", []))
                    tag = "✓" if r.get("recordIds") else "≈"
                    print(f"      [{i+1:03d}/{len(chunk)}] {tag} {short}")
                    rec_ok = True
                    break
                if resp.status_code == 404 and attempt < MAX_RETRIES:
                    wait = RETRY_BACKOFF[attempt]
                    print(f"          ↳ 404 – retry in {wait}s …")
                    time.sleep(wait)
                    continue
                break
            if not rec_ok:
                failed.append(f"{rid}: {resp.status_code}")
                print(f"      [{i+1:03d}/{len(chunk)}] ✗ {short} ({resp.status_code})")
            time.sleep(0.5)

    return {"created": len(created), "skipped": len(skipped), "failed": len(failed)}


# ═══════════════════════════════════════════════════════════════════════
#  Unified manifest ingestion  (workflow → storage fallback)
# ═══════════════════════════════════════════════════════════════════════

def ingest_manifest(client: httpx.Client, path: Path, *,
                    mode: str = "auto",
                    dry_run: bool = False) -> dict:
    """Ingest a manifest file.

    mode:
      "auto"     – try workflow first, fall back to storage
      "manifest" – workflow only (fail if unavailable)
      "storage"  – storage only (skip workflow attempt)
    """
    raw = load_manifest(path)
    manifest = transform_manifest(raw)
    records = extract_records(manifest)

    print(f"\n  [{path.name}]  {len(records)} records")

    if not records:
        print(f"    (empty manifest, skipping)")
        return {"created": 0, "skipped": 0, "failed": 0}

    label = path.stem

    # Workflow mode
    if mode in ("auto", "manifest"):
        wf_ok = ingest_via_workflow(client, manifest, label, dry_run=dry_run)
        if wf_ok:
            return {"created": len(records), "skipped": 0, "failed": 0}
        if mode == "manifest":
            return {"created": 0, "skipped": 0, "failed": len(records)}
        print(f"    → Falling back to Storage API …")

    # Storage mode
    # Records are already transformed inside the manifest
    return ingest_via_storage(client, records, label, dry_run=dry_run)


# ═══════════════════════════════════════════════════════════════════════
#  Dataset definitions  (manifest files in dependency order)
# ═══════════════════════════════════════════════════════════════════════

# DG1 – Drogon exploration data (foundation, must be ingested first)
DG1_MANIFESTS = [
    "reftypes_associatedliquid.json",
    "manifest_masterwp_drogon.json",
    "manifest_wpcraw_drogon.json",
    "manifest_wpcstat_drogon.json",
    "manifest_wpcparams_drogon.json",
    "manifest_risk_drogon.json",
    "manifest_devconcept_drogon.json",
    "manifest_activity_drogon.json",
    "manifest_documents_drogon.json",
    "manifest_bd_drogon.json",
    "manifest_cp_drogon.json",
    "manifest_wells_drogon.json",
    "manifest_litho_strat_drogon.json",
    "manifest_markers_drogon.json",
]

# DG2 – Drogon concept-select data
DG2_MANIFESTS = [
    "manifest_wpcraw_dg2.json",
    "manifest_wpcstat_dg2.json",
    "manifest_wpcparams_dg2.json",
    "manifest_wpc_production_dg2.json",
    "manifest_devconcept_dg2.json",
    "manifest_grid_dg2.json",
    "manifest_maps_dg2.json",
    "manifest_simtables_dg2.json",
    "manifest_polygons_dg2.json",
    "manifest_activity_dg2.json",
    "manifest_risk_dg2.json",
    "manifest_documents_dg2.json",
    "manifest_collection_dg2.json",
    "manifest_bd_dg2.json",
]

# SeisInt – seismic interpretation
SEISINT_MANIFESTS = [
    "manifest_rddms_catalog.json",
    "manifest_rddms_drogon_dg_seismic.json",
    "manifest_volantis_interp.json",
    "manifest_horizon_controlpoints.json",
    "manifest_fault_polylines.json",
]

# Strat – stratigraphic columns (ICS2017 chronostratigraphy)
STRAT_MANIFESTS = [
    "manifest_chronostratics.json",
    "manifest_stratcolumn.json",
    "manifest_horizons.json",
]

DATASETS: Dict[str, Tuple[Path, List[str]]] = {
    "dg1":     (DG1_DIR,     DG1_MANIFESTS),
    "dg2":     (DG2_DIR,     DG2_MANIFESTS),
    "seisint": (SEISINT_DIR, SEISINT_MANIFESTS),
    "strat":   (STRAT_DIR,   STRAT_MANIFESTS),
}


# ═══════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════

def main():
    global TARGET

    ap = argparse.ArgumentParser(
        description="Unified ORES demo ingestion pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python demo/ingest_demo.py                            # → eqndev, all datasets
  python demo/ingest_demo.py --target preship            # → preship instance
  python demo/ingest_demo.py --only dg1 strat            # selected datasets
  python demo/ingest_demo.py --storage-only --dry-run    # preview storage mode
  python demo/ingest_demo.py --partition mypart \\
      --host https://my.osdu.instance \\
      --legal-tag mypart-legal-tag \\
      --owners data.default.owners@mypart.example.com
""",
    )
    ap.add_argument("--target", default="eqndev",
                    help="Instance name (reads INSTANCE_<NAME>_* from k8s/ or env; default: eqndev)")
    ap.add_argument("--only", nargs="+", choices=list(DATASETS.keys()),
                    help="Datasets to ingest (default: all)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Preview records without ingesting")
    ap.add_argument("--skip-schemas", action="store_true",
                    help="Skip schema registration step")
    ap.add_argument("--storage-only", action="store_true",
                    help="Use Storage API only (skip Workflow API)")
    ap.add_argument("--manifest-only", action="store_true",
                    help="Use Workflow API only (fail if unavailable)")
    ap.add_argument("--start", type=int, default=0,
                    help="Skip first N manifests (resume after failure)")

    # Direct overrides for partition, ACL, legal (for instances not in config)
    ov = ap.add_argument_group("Instance overrides",
                               "Override values from --target config")
    ov.add_argument("--host", help="OSDU host URL (https://…)")
    ov.add_argument("--partition", help="Data partition ID")
    ov.add_argument("--legal-tag", help="Legal tag name")
    ov.add_argument("--owners", nargs="+", help="ACL owner group(s)")
    ov.add_argument("--viewers", nargs="+", help="ACL viewer group(s)")
    ov.add_argument("--countries", nargs="+", help="Legal country code(s)")

    args = ap.parse_args()

    if args.storage_only and args.manifest_only:
        ap.error("Cannot use --storage-only and --manifest-only together")

    # 1. Load instance config
    try:
        TARGET = load_instance_config(args.target)
    except (SystemExit, Exception) as e:
        # Instance not found - allow if user provides explicit overrides
        if args.host and args.partition:
            TARGET = {
                "host": "", "partition": "", "tenant": "", "client_id": "",
                "client_secret": "", "refresh_token": "", "scope": "",
                "legal_tag": "", "owners": [], "viewers": [], "countries": [],
            }
        else:
            print(f"Instance '{args.target}' not found. "
                  "Provide --host and --partition explicitly, or configure "
                  f"INSTANCE_{args.target.upper()}_* variables.")
            sys.exit(1)

    # 2. Apply CLI overrides
    if args.host:
        TARGET["host"] = args.host.rstrip("/")
    if args.partition:
        TARGET["partition"] = args.partition
    if args.legal_tag:
        TARGET["legal_tag"] = args.legal_tag
    if args.owners:
        TARGET["owners"] = args.owners
    if args.viewers:
        TARGET["viewers"] = args.viewers
    if args.countries:
        TARGET["countries"] = args.countries

    # Determine ingestion mode
    if args.storage_only:
        mode = "storage"
    elif args.manifest_only:
        mode = "manifest"
    else:
        mode = "auto"

    selected = set(args.only) if args.only else set(DATASETS.keys())

    # Determine if we're targeting the source partition (no transform needed)
    is_same = TARGET["partition"] == SRC_PARTITION

    print("=" * 64)
    print(f"  ORES Demo Pipeline  →  {args.target}")
    print(f"  Host:       {TARGET['host']}")
    print(f"  Partition:  {TARGET['partition']}")
    print(f"  Legal tag:  {TARGET['legal_tag']}")
    print(f"  ACL owners: {TARGET['owners']}")
    print(f"  Countries:  {TARGET['countries']}")
    print(f"  Mode:       {mode}")
    print(f"  Datasets:   {', '.join(sorted(selected))}")
    if is_same:
        print(f"  Transform:  none (target = source partition)")
    else:
        print(f"  Transform:  {SRC_PARTITION}: → {TARGET['partition']}:")
    print("=" * 64)

    # 3. Authenticate
    if not args.dry_run:
        print("\n── Authenticating ──")
        get_access_token()
        print(f"  ✓ token obtained")

    with httpx.Client(timeout=120) as client:

        # 4. Register schemas (if needed)
        if not args.skip_schemas and not args.dry_run:
            register_missing_schemas(client, dry_run=args.dry_run)

        # 5. Ingest datasets in order
        totals = {"created": 0, "skipped": 0, "failed": 0}
        manifest_idx = 0

        # Process datasets in dependency order: dg1 → dg2 → seisint → strat
        for ds_name in ["dg1", "dg2", "seisint", "strat"]:
            if ds_name not in selected:
                continue

            base_dir, manifest_names = DATASETS[ds_name]
            print(f"\n{'─' * 64}")
            print(f"  Dataset: {ds_name}  ({len(manifest_names)} manifests)")
            print(f"{'─' * 64}")

            for mname in manifest_names:
                mpath = base_dir / mname
                if not mpath.exists():
                    print(f"\n  [{mname}]  ⚠ not found, skipping")
                    manifest_idx += 1
                    continue

                if manifest_idx < args.start:
                    print(f"\n  [{mname}]  (skipped, --start {args.start})")
                    manifest_idx += 1
                    continue

                result = ingest_manifest(
                    client, mpath,
                    mode=mode,
                    dry_run=args.dry_run,
                )

                for k in totals:
                    totals[k] += result[k]

                if result["failed"] > 0 and not args.dry_run:
                    print(f"\n  ❌ Failed at manifest #{manifest_idx} ({mname})")
                    print(f"     Resume with: --start {manifest_idx}")
                    break

                manifest_idx += 1
                # Small pause between manifests for indexing
                if not args.dry_run:
                    time.sleep(2)

            else:
                # Inner loop completed without break
                continue
            # Inner loop broke (failure) → stop outer loop too
            break

        # Summary
        print(f"\n{'=' * 64}")
        print(f"  SUMMARY")
        print(f"  created/updated: {totals['created']}")
        print(f"  skipped:         {totals['skipped']}")
        print(f"  failed:          {totals['failed']}")
        print(f"{'=' * 64}")

        sys.exit(0 if totals["failed"] == 0 else 2)


if __name__ == "__main__":
    main()
