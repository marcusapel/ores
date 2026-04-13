#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
register_m27_schemas.py — Register the M27 JSON Schema definitions with
the OSDU Schema Service on the dev platform.

These schemas were fetched from the OSDU Data Definitions GitLab repo
(project 214, master branch) and live in the ``schemas/`` sub-directory.

Each JSON file is the full JSON Schema (draft-07) for a record kind.
The ``x-osdu-schema-source`` field inside each file carries the canonical
kind identifier, e.g.  osdu:wks:work-product-component--StructureMap:1.0.0

The script wraps each schema body in the ``{ schemaInfo, schema }``
envelope expected by ``PUT /api/schema-service/v1/schema`` and registers
it as status=DEVELOPMENT so that the platform can validate records
against the definition.

Usage:
  python register_m27_schemas.py                  # register all
  python register_m27_schemas.py --dry-run        # preview payloads
  python register_m27_schemas.py --schema schemas/StructureMap.1.0.0.json
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List

import httpx

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT  = SCRIPT_DIR.parent.parent
SCHEMAS_DIR = SCRIPT_DIR / "schemas" / "resolved"

sys.path.insert(0, str(SCRIPT_DIR))
from _shared import load_env  # noqa: E402

# ── kind string parser ────────────────────────────────────────────────
# e.g.  "osdu:wks:work-product-component--StructureMap:1.0.0"
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


# ── auth ──────────────────────────────────────────────────────────────

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


# ── registration ──────────────────────────────────────────────────────

def build_payload(schema_body: dict, *, authority_override: str | None = None) -> dict:
    """Wrap a downloaded JSON Schema body in the Schema Service envelope."""
    kind = schema_body.get("x-osdu-schema-source", "")
    if not kind:
        raise ValueError("Schema has no x-osdu-schema-source field")

    identity = parse_kind(kind)

    # Allow overriding the authority (e.g. "osdu" → "dev") if the
    # platform rejects osdu-authority registrations.
    if authority_override:
        identity["authority"] = authority_override

    return {
        "schemaInfo": {
            "schemaIdentity": identity,
            "status": "DEVELOPMENT",
        },
        "schema": schema_body,
    }


def register_one(env: Dict[str, str], token: str, payload: dict,
                 *, dry_run: bool = False) -> bool:
    """PUT one schema to the Schema Service.  Returns True on success."""
    url = f"{env['host']}/api/schema-service/v1/schema"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
        "data-partition-id": env["partition"],
    }

    si = payload["schemaInfo"]["schemaIdentity"]
    kind = (
        f"{si['authority']}:{si['source']}:"
        f"{si['entityType']}:"
        f"{si['schemaVersionMajor']}.{si['schemaVersionMinor']}.{si['schemaVersionPatch']}"
    )

    print(f"\n  Schema kind : {kind}")

    if dry_run:
        print("  [DRY-RUN] Would PUT schema — skipping.")
        return True

    r = httpx.put(url, headers=headers, json=payload, timeout=60)

    if r.status_code == 201:
        print(f"  ✓ Registered ({r.status_code})")
        return True
    elif r.status_code == 409:
        print(f"  ✓ Already exists ({r.status_code})")
        return True
    elif r.status_code == 200:
        print(f"  ✓ Updated ({r.status_code})")
        return True
    else:
        corr = r.headers.get("x-correlation-id") or r.headers.get("x-request-id") or ""
        print(f"  ✗ Failed ({r.status_code}) corr={corr}")
        print(f"    {r.text[:1000]}")
        return False


# ── main ──────────────────────────────────────────────────────────────

def discover_schemas(schema_dir: Path) -> List[Path]:
    """Return all .json files in the schemas/ directory, sorted."""
    return sorted(schema_dir.glob("*.json"))


def main():
    ap = argparse.ArgumentParser(
        description="Register M27 JSON Schema definitions on the dev platform"
    )
    ap.add_argument(
        "--schema", nargs="*", default=None,
        help="Specific schema file(s) to register.  Default: all in schemas/",
    )
    ap.add_argument(
        "--authority", default=None,
        help="Override authority (e.g. 'dev' if platform rejects 'osdu').",
    )
    ap.add_argument(
        "--env-file", nargs="*",
        default=[str(REPO_ROOT / ".env")],
        help="One or more .env files (merged left-to-right).",
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="Print kinds without sending.")
    args = ap.parse_args()

    env = load_env(args.env_file)
    print(f"  host      : {env['host']}")
    print(f"  partition : {env['partition']}")

    # Collect schema files
    if args.schema:
        files = [Path(s) for s in args.schema]
    else:
        files = discover_schemas(SCHEMAS_DIR)

    if not files:
        print("  No schema files found — nothing to do.")
        return

    print(f"\n  Found {len(files)} schema file(s):")
    for f in files:
        print(f"    • {f.name}")

    token = get_access_token(env)

    ok, fail = 0, 0
    for schema_path in files:
        with open(schema_path, encoding="utf-8") as f:
            schema_body = json.load(f)
        payload = build_payload(schema_body, authority_override=args.authority)
        if register_one(env, token, payload, dry_run=args.dry_run):
            ok += 1
        else:
            fail += 1

    print(f"\n  Done: {ok} succeeded, {fail} failed (of {ok + fail} total)")
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
