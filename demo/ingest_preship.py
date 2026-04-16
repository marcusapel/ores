#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ingest_preship.py — Ingest demo data into an OSDU instance.

Reads target-instance config from .env  (INSTANCE_<NAME>_* variables)
so the same script works against any registered backend.

Datasets
  dg1       Drogon DG1 — business-decision, risks, volumes, master data
  dg2       Drogon DG2 — development concepts, seismic, documents, …
  seisint   Seismic interpretation demo

Usage
  python demo/ingest_preship.py                          # all → preship
  python demo/ingest_preship.py --target preship         # explicit target
  python demo/ingest_preship.py --only dg1               # just DG1
  python demo/ingest_preship.py --only dg1 dg2           # DG1 + DG2
  python demo/ingest_preship.py --dry-run                # preview only
  python demo/ingest_preship.py --skip-schemas           # skip schema reg.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import httpx

# ── Paths ─────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT  = SCRIPT_DIR.parent

DG1_RECORDS       = SCRIPT_DIR / "drogon" / "records"
DG2_RECORDS       = SCRIPT_DIR / "drogon_dg2" / "records"
SEISINT_RECORDS   = SCRIPT_DIR / "seisint" / "records"
SEISINT_SCHEMAS   = SCRIPT_DIR / "seisint" / "schemas" / "resolved"
DEVCONCEPT_SCHEMA = SCRIPT_DIR / "drogon" / "schema_devconcept.json"

# ── Instance config (populated by load_instance_config in main) ──────
TARGET: Dict[str, Any] = {}

# Source partition (auto-detected from records, fallback)
SRC_PARTITION = "dev"

# ── kind string parser (from seisint/register_m27_schemas.py) ────────
KIND_RE = re.compile(
    r"^(?P<authority>[^:]+)"
    r":(?P<source>[^:]+)"
    r":(?P<entityType>[^:]+)"
    r":(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$"
)


def parse_kind(kind: str) -> dict:
    m = KIND_RE.match(kind)
    if not m:
        raise ValueError(f"Cannot parse kind string: {kind!r}")
    return {
        "authority": m.group("authority"),
        "source": m.group("source"),
        "entityType": m.group("entityType"),
        "schemaVersionMajor": int(m.group("major")),
        "schemaVersionMinor": int(m.group("minor")),
        "schemaVersionPatch": int(m.group("patch")),
    }


# ── Instance config loader ───────────────────────────────────────────

def load_instance_config(name: str) -> Dict[str, Any]:
    """Build an instance config dict from INSTANCE_<NAME>_* env vars."""
    # Load .env if python-dotenv is available
    env_file = REPO_ROOT / ".env"
    env: Dict[str, str] = {}
    if env_file.exists():
        try:
            from dotenv import dotenv_values  # type: ignore
            env = {k: v for k, v in dotenv_values(env_file).items() if v is not None}
        except ImportError:
            # Fallback: simple parser
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    # Also overlay os.environ so CLI overrides work
    env.update(os.environ)

    prefix = f"INSTANCE_{name.upper()}_"
    raw = {k[len(prefix):].lower(): v for k, v in env.items() if k.startswith(prefix)}
    if not raw:
        sys.exit(f"ERROR: no INSTANCE_{name.upper()}_* variables found in .env")

    host = raw.get("hostname", "")
    if not host.startswith("http"):
        host = f"https://{host}"
    partition = raw.get("data_partition_id", "")
    client_id = raw.get("client_id", "")

    return {
        "host":          host,
        "partition":     partition,
        "tenant":        raw.get("tenant_id", ""),
        "client_id":     client_id,
        "client_secret": raw.get("client_secret", ""),
        "scope":         raw.get("scope", f"{client_id}/.default"),
        "legal_tag":     raw.get("default_legal_tag", f"{partition}-public-usa-dataset-1"),
        "owners":        [raw["default_owners"]] if "default_owners" in raw
                         else [f"data.default.owners@{partition}.dataservices.energy"],
        "viewers":       [raw["default_viewers"]] if "default_viewers" in raw
                         else [f"data.default.viewers@{partition}.dataservices.energy"],
        "countries":     raw.get("default_countries", "US").split(","),
    }


# ── Auth ──────────────────────────────────────────────────────────────

_cached_token: str | None = None
_cached_exp: float = 0.0


def get_access_token() -> str:
    """Mint access_token via client_credentials grant."""
    global _cached_token, _cached_exp
    if _cached_token and time.time() < _cached_exp:
        return _cached_token

    url = f"https://login.microsoftonline.com/{PRESHIP['tenant']}/oauth2/v2.0/token"
    data = {
        "grant_type":    "client_credentials",
        "client_id":     PRESHIP["client_id"],
        "client_secret": PRESHIP["client_secret"],
        "scope":         PRESHIP["scope"],
    }
    r = httpx.post(url, data=data, timeout=30)
    if not r.is_success:
        raise RuntimeError(f"Auth failed ({r.status_code}): {r.text[:600]}")
    body = r.json()
    _cached_token = body["access_token"]
    _cached_exp = time.time() + max(int(body.get("expires_in", 3600)) - 120, 60)
    print(f"  ✓ token acquired (expires_in={body.get('expires_in', '?')}s)")
    return _cached_token


def api_headers() -> dict:
    return {
        "Authorization": f"Bearer {get_access_token()}",
        "Content-Type":  "application/json",
        "data-partition-id": PRESHIP["partition"],
    }


# ── Schema registration ──────────────────────────────────────────────

def check_schema_exists(client: httpx.Client, kind: str) -> bool:
    r = client.get(
        f"{PRESHIP['host']}/api/schema-service/v1/schema/{kind}",
        headers=api_headers(),
    )
    return r.status_code == 200


def register_schema(client: httpx.Client, schema_body: dict,
                    *, authority_override: str | None = None,
                    dry_run: bool = False) -> bool:
    kind = schema_body.get("x-osdu-schema-source", "")
    if not kind:
        print("    ✗ schema has no x-osdu-schema-source")
        return False

    identity = parse_kind(kind)
    if authority_override:
        identity["authority"] = authority_override

    payload = {
        "schemaInfo": {
            "schemaIdentity": identity,
            "status": "DEVELOPMENT",
        },
        "schema": schema_body,
    }

    label = f"{identity['authority']}:{identity['source']}:{identity['entityType']}:{identity['schemaVersionMajor']}.{identity['schemaVersionMinor']}.{identity['schemaVersionPatch']}"

    if dry_run:
        print(f"    [dry-run] would register {label}")
        return True

    url = f"{PRESHIP['host']}/api/schema-service/v1/schema"
    r = client.put(url, json=payload, headers=api_headers(), timeout=60)
    if r.status_code in (200, 201):
        print(f"    ✓ registered {label}")
        return True
    elif r.status_code == 409:
        print(f"    ≈ {label} already exists")
        return True
    else:
        print(f"    ✗ {label}: {r.status_code} {r.text[:200]}")
        return False


def register_missing_schemas(client: httpx.Client, dry_run: bool = False) -> None:
    """Register schemas that are missing on the pre-ship instance."""
    print("\n── Registering missing schemas ──")

    # 1. Resolved SeisInt schemas
    if SEISINT_SCHEMAS.is_dir():
        for f in sorted(SEISINT_SCHEMAS.glob("*.json")):
            body = json.loads(f.read_text(encoding="utf-8"))
            kind = body.get("x-osdu-schema-source", "")
            if kind and not check_schema_exists(client, kind):
                register_schema(client, body, dry_run=dry_run)
            else:
                print(f"    ≈ {kind} exists (or unresolvable)")

    # 2. DevelopmentConcept custom schema
    if DEVCONCEPT_SCHEMA.exists():
        body = json.loads(DEVCONCEPT_SCHEMA.read_text(encoding="utf-8"))
        kind = body.get("x-osdu-schema-source", "")
        # For preship, register under "opendes" authority instead of "dev"
        tgt_kind = kind.replace("dev:", f"{TGT_PARTITION}:") if kind.startswith("dev:") else kind
        if not check_schema_exists(client, tgt_kind) and not check_schema_exists(client, kind):
            register_schema(client, body, authority_override=TGT_PARTITION, dry_run=dry_run)
        else:
            print(f"    ≈ {kind} exists")

    # 3. ActivityTemplate and work-product — these are standard OSDU schemas
    #    that should exist on M26 but may not. We'll create minimal schemas.
    for missing_kind, entity_type, group in [
        ("osdu:wks:work-product-component--ActivityTemplate:1.0.0",
         "work-product-component--ActivityTemplate", "work-product-component"),
        ("osdu:wks:work-product:1.0.0", "work-product", "work-product"),
    ]:
        if not check_schema_exists(client, missing_kind):
            # Create a minimal permissive schema
            minimal = {
                "x-osdu-schema-source": missing_kind,
                "$schema": "http://json-schema.org/draft-07/schema#",
                "title": entity_type,
                "type": "object",
                "additionalProperties": True,
            }
            register_schema(client, minimal, dry_run=dry_run)
        else:
            print(f"    ≈ {missing_kind} exists")

    # 4. LocalBoundaryFeature — from seisint unresloved schemas
    lbf_schema = SCRIPT_DIR / "seisint" / "schemas" / "LocalBoundaryFeature.1.1.0.json"
    lbf_kind = "osdu:wks:master-data--LocalBoundaryFeature:1.1.0"
    if lbf_schema.exists() and not check_schema_exists(client, lbf_kind):
        body = json.loads(lbf_schema.read_text(encoding="utf-8"))
        register_schema(client, body, dry_run=dry_run)
    else:
        print(f"    ≈ {lbf_kind} exists (or no source)")


# ── Record transformation ────────────────────────────────────────────

def transform_record(rec: dict) -> dict:
    """Re-prefix record ID, rewrite ACL/legal for preship instance."""
    # 1. Re-prefix ID:  dev:master-data--X:uuid:1 → opendes:master-data--X:uuid:1
    rid = rec.get("id", "")
    if ":" in rid:
        parts = rid.split(":", 1)
        if parts[0] == SRC_PARTITION:
            rec["id"] = f"{TGT_PARTITION}:{parts[1]}"

    # 2. Re-prefix kind if it starts with "dev:"
    kind = rec.get("kind", "")
    if kind.startswith(f"{SRC_PARTITION}:"):
        rec["kind"] = f"{TGT_PARTITION}:{kind[len(SRC_PARTITION)+1:]}"

    # 3. Rewrite ACL
    acl = rec.get("acl", {})
    acl["owners"] = PRESHIP["owners"][:]
    acl["viewers"] = PRESHIP["viewers"][:]
    rec["acl"] = acl

    # 4. Rewrite legal
    legal = rec.get("legal", {})
    legal["legaltags"] = [PRESHIP["legal_tag"]]
    legal["otherRelevantDataCountries"] = PRESHIP["countries"][:]
    rec["legal"] = legal

    # 5. Rewrite any embedded partition references in data.* fields
    _rewrite_partition_refs(rec.get("data", {}))

    return rec


def _rewrite_partition_refs(obj: Any) -> None:
    """Recursively rewrite dev: → opendes: in nested string values."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and v.startswith(f"{SRC_PARTITION}:"):
                obj[k] = f"{TGT_PARTITION}:{v[len(SRC_PARTITION)+1:]}"
            elif isinstance(v, (dict, list)):
                _rewrite_partition_refs(v)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if isinstance(item, str) and item.startswith(f"{SRC_PARTITION}:"):
                obj[i] = f"{TGT_PARTITION}:{item[len(SRC_PARTITION)+1:]}"
            elif isinstance(item, (dict, list)):
                _rewrite_partition_refs(item)


# ── Ingestion ────────────────────────────────────────────────────────

MAX_RETRIES = 4
RETRY_BACKOFF = [3, 6, 10, 15]


def ingest_records(client: httpx.Client, records: List[dict],
                   label: str, *, dry_run: bool = False) -> dict:
    """Ingest a batch of records via Storage API."""
    print(f"\n── Ingesting {label} ({len(records)} records) ──")

    if dry_run:
        for r in records:
            print(f"    [dry-run] {r.get('id', '?')[:60]}")
        return {"created": 0, "skipped": 0, "failed": 0}

    created, skipped, failed = [], [], []

    # Try batch first
    print(f"  Attempting batch PUT ({len(records)} records) …")
    url = f"{PRESHIP['host']}/api/storage/v2/records"

    for attempt in range(MAX_RETRIES + 1):
        resp = client.put(url, json=records, headers=api_headers(), timeout=120)
        if resp.status_code == 404 and attempt < MAX_RETRIES:
            wait = RETRY_BACKOFF[attempt]
            print(f"    404 on attempt {attempt+1}, retrying in {wait}s …")
            time.sleep(wait)
            continue
        break

    if resp.is_success:
        result = resp.json()
        created = result.get("recordIds", [])
        skipped = result.get("skippedRecordIds", [])
        print(f"    ✓ batch OK: created={len(created)}  skipped={len(skipped)}")
    else:
        print(f"    ✗ batch failed ({resp.status_code}): {resp.text[:300]}")
        print(f"    Falling back to sequential ingestion …")

        for i, rec in enumerate(records):
            rid = rec.get("id", "?")
            short = rid.split(":")[-1][:35] if ":" in rid else rid[:40]
            for attempt in range(MAX_RETRIES + 1):
                resp = client.put(url, json=[rec], headers=api_headers(), timeout=60)
                if resp.is_success:
                    r = resp.json()
                    created.extend(r.get("recordIds", []))
                    skipped.extend(r.get("skippedRecordIds", []))
                    tag = "✓" if r.get("recordIds") else "≈"
                    print(f"    [{i+1:02d}/{len(records)}] {tag} {short}")
                    break
                if resp.status_code == 404 and attempt < MAX_RETRIES:
                    wait = RETRY_BACKOFF[attempt]
                    print(f"        ↳ 404 — retry in {wait}s …")
                    time.sleep(wait)
                    continue
                failed.append(f"{rid}: {resp.status_code} {resp.text[:150]}")
                print(f"    [{i+1:02d}/{len(records)}] ✗ {short} ({resp.status_code})")
                break
            time.sleep(1)  # small delay between individual puts

    summary = {"created": len(created), "skipped": len(skipped), "failed": len(failed)}
    print(f"  Summary: created={summary['created']}  skipped={summary['skipped']}  failed={summary['failed']}")
    if failed:
        print("  Failures:")
        for f in failed:
            print(f"    {f}")
    return summary


def load_and_transform(records_dir: Path) -> List[dict]:
    """Load JSON records from a directory, transform for preship."""
    records = []
    for f in sorted(records_dir.glob("*.json")):
        rec = json.loads(f.read_text(encoding="utf-8"))
        rec = transform_record(rec)
        records.append(rec)
    return records


# ── Main ──────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Ingest demos to pre-ship OSDU")
    ap.add_argument("--dry-run", action="store_true", help="Preview only, no changes")
    ap.add_argument("--skip-schemas", action="store_true", help="Skip schema registration")
    ap.add_argument("--only", choices=["dg2", "seisint"], help="Only run one demo")
    args = ap.parse_args()

    print("=" * 60)
    print("  Pre-ship OSDU Ingestion")
    print(f"  Host:      {PRESHIP['host']}")
    print(f"  Partition:  {PRESHIP['partition']}")
    print(f"  Legal tag:  {PRESHIP['legal_tag']}")
    print("=" * 60)

    print("\n── Authenticating ──")
    get_access_token()

    with httpx.Client(timeout=120) as client:

        # 1. Register missing schemas
        if not args.skip_schemas:
            register_missing_schemas(client, dry_run=args.dry_run)

        totals = {"created": 0, "skipped": 0, "failed": 0}

        # 2. Ingest drogon_dg2 records
        if args.only in (None, "dg2"):
            if DG2_RECORDS.is_dir():
                recs = load_and_transform(DG2_RECORDS)
                result = ingest_records(client, recs, "drogon_dg2", dry_run=args.dry_run)
                for k in totals:
                    totals[k] += result[k]
            else:
                print(f"\n⚠ DG2 records not found at {DG2_RECORDS}")
                print("  Run: python demo/run_pipeline.py demo/drogon_dg2")

        # 3. Ingest seisint records
        if args.only in (None, "seisint"):
            if SEISINT_RECORDS.is_dir():
                recs = load_and_transform(SEISINT_RECORDS)
                result = ingest_records(client, recs, "seisint", dry_run=args.dry_run)
                for k in totals:
                    totals[k] += result[k]
            else:
                print(f"\n⚠ SeisInt records not found at {SEISINT_RECORDS}")
                print("  Run the seisint pipeline first.")

        # Summary
        print("\n" + "=" * 60)
        print("  TOTAL")
        print(f"  created/updated: {totals['created']}")
        print(f"  skipped:         {totals['skipped']}")
        print(f"  failed:          {totals['failed']}")
        print("=" * 60)


if __name__ == "__main__":
    main()
