#!/usr/bin/env python3
"""
Quick OSDU Search CLI - uses _auth.py for token management.

Usage:
    # List StratigraphicColumn records
    python demo/osdu_search.py "osdu:wks:work-product-component--StratigraphicColumn:*"

    # Search with a query
    python demo/osdu_search.py "osdu:wks:work-product-component--StratigraphicColumn:*" -q "ICS*"

    # Search multiple kinds at once
    python demo/osdu_search.py \
        "osdu:wks:work-product-component--StratigraphicColumn:*" \
        "osdu:wks:work-product-component--StratigraphicColumnRankInterpretation:*" \
        "osdu:wks:work-product-component--StratigraphicUnitInterpretation:*"

    # Fetch a specific record by ID
    python demo/osdu_search.py --id "dev:work-product-component--StratigraphicColumn:ChronoStratigraphicScheme:ICS2017:"

    # List all kinds matching a pattern
    python demo/osdu_search.py --list-kinds "Stratigraphic"

    # JSON output
    python demo/osdu_search.py "osdu:wks:work-product-component--StratigraphicColumn:*" -o json

Requires:  eval "$(python k8s/env_from_k8s.py)"  to load env vars first
  (or run from the repo root where k8s/ is visible).
"""
from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path
from typing import Any, Dict, List

try:
    import httpx
except ImportError:
    # Fall back to urllib if httpx not installed system-wide
    httpx = None  # type: ignore[assignment]

import urllib.request
import urllib.parse
import ssl

# ── Locate _auth from sibling module ──
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import _auth  # noqa: E402


# ── Thin HTTP helpers (works without httpx) ─────────────────────────────

_CTX = ssl.create_default_context()


def _post(url: str, headers: dict, json_body: dict, timeout: int = 60) -> tuple[int, dict]:
    """POST JSON, return (status, body_dict)."""
    if httpx is not None:
        r = httpx.post(url, headers=headers, json=json_body, timeout=timeout)
        return r.status_code, r.json() if r.status_code == 200 else {}
    data = json.dumps(json_body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:500]
        print(f"HTTP {e.code}: {body}", file=sys.stderr)
        return e.code, {}


def _get(url: str, headers: dict, timeout: int = 60) -> tuple[int, dict]:
    """GET, return (status, body_dict)."""
    if httpx is not None:
        r = httpx.get(url, headers=headers, timeout=timeout)
        return r.status_code, r.json() if r.status_code == 200 else {}
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:500]
        print(f"HTTP {e.code}: {body}", file=sys.stderr)
        return e.code, {}


def _get_auth(instance: str) -> tuple[str, str, str]:
    """Return (token, hostname, partition)."""
    # Bypass ALIASES for environ resolution (env vars use INSTANCE_EQNDEV_*, not INSTANCE_SWEDEV_*)
    fields = _auth._resolve_from_environ(instance)
    if not fields:
        fields = _auth._resolve_from_k8s(instance, _HERE.parent / "k8s")
    if not fields:
        raise SystemExit(f"Cannot resolve instance '{instance}'. Load env vars first:\n"
                         f"  eval \"$(python k8s/env_from_k8s.py)\"")
    host = fields["hostname"]
    partition = fields.get("data_partition_id", "opendes")
    inst = {
        "name": instance, "source": "env",
        "host": f"https://{host}", "partition": partition,
        "tenant": fields.get("tenant_id", ""),
        "client_id": fields.get("client_id", ""),
        "scope": fields.get("scope", ""),
        "refresh_token": fields.get("refresh_token", ""),
        "client_secret": fields.get("client_secret", ""),
        "grant": "refresh_token" if fields.get("refresh_token") else
                 ("client_credentials" if fields.get("client_secret") else "none"),
    }
    token = _auth._mint(inst, verbose=True)
    return token, host, partition


def _headers(token: str, partition: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "data-partition-id": partition,
    }


# ── Commands ──────────────────────────────────────────────────────────────

def cmd_search(args):
    """Search by kind(s)."""
    token, host, partition = _get_auth(args.instance)
    hdr = _headers(token, partition)
    url = f"https://{host}/api/search/v2/query"

    for kind in args.kinds:
        short = kind.split("--")[-1].split(":")[0] if "--" in kind else kind
        payload: Dict[str, Any] = {
            "kind": kind,
            "query": args.query or "*",
            "limit": args.limit,
            "returnedFields": ["id", "kind", "version", "data.Name"],
            "trackTotalCount": True,
        }
        status, data = _post(url, hdr, payload)
        if status != 200:
            print(f"ERROR {status} for {short}: {json.dumps(data, indent=2)}", file=sys.stderr)
            continue
        total = data.get("totalCount", "?")
        results = data.get("results") or []

        print(f"\n{'━' * 60}")
        print(f"  {short}  -  {total} total, showing {len(results)}")
        print(f"{'━' * 60}")

        if args.output == "json":
            print(json.dumps(results, indent=2))
        else:
            for i, rec in enumerate(results, 1):
                rid = rec.get("id", "?")
                name = ((rec.get("data") or {}).get("Name")) or ""
                ver = rec.get("version") or ""
                print(f"  {i:3d}. {name or '(unnamed)'}")
                print(f"       id:  {rid}")
                if ver:
                    print(f"       ver: {ver}")


def cmd_fetch(args):
    """Fetch a record by ID from Storage."""
    token, host, partition = _get_auth(args.instance)
    hdr = _headers(token, partition)
    url = f"https://{host}/api/storage/v2/records/{args.record_id}"

    status, rec = _get(url, hdr)
    if status != 200:
        print(f"ERROR {status} fetching record", file=sys.stderr)
        sys.exit(1)
    if args.output == "json":
        print(json.dumps(rec, indent=2))
    else:
        print(f"  id:        {rec.get('id')}")
        print(f"  kind:      {rec.get('kind')}")
        print(f"  version:   {rec.get('version')}")
        name = (rec.get("data") or {}).get("Name", "")
        if name:
            print(f"  Name:      {name}")
        print(f"  keys(data): {list((rec.get('data') or {}).keys())}")
        acl = rec.get("acl") or {}
        print(f"  acl.viewers: {acl.get('viewers', [])}")
        print(f"  acl.owners:  {acl.get('owners', [])}")


def cmd_list_kinds(args):
    """Search for kinds matching a substring."""
    token, host, partition = _get_auth(args.instance)
    hdr = _headers(token, partition)
    url = f"https://{host}/api/search/v2/query"

    # Use a broad wildcard kind with schema filter
    pattern = args.pattern.lower()
    # Try searching with the pattern in the kind string
    kind_guess = f"osdu:wks:*--*{args.pattern}*:*"
    payload = {
        "kind": kind_guess,
        "query": "*",
        "limit": 1,
        "trackTotalCount": True,
    }
    status, data = _post(url, hdr, payload)
    if status == 200:
        total = data.get("totalCount", 0)
        if total > 0:
            print(f"  ✓ {kind_guess}  →  {total} records")

    # Also try common prefixes
    for prefix in ["work-product-component", "master-data", "reference-data", "work-product"]:
        kind = f"osdu:wks:{prefix}--*{args.pattern}*:*"
        payload = {"kind": kind, "query": "*", "limit": 0, "trackTotalCount": True}
        status, kdata = _post(url, hdr, payload)
        if status == 200:
            total = kdata.get("totalCount", 0)
            if total > 0:
                print(f"  {prefix}--*{args.pattern}*  →  {total} records")


def main():
    p = argparse.ArgumentParser(
        description="OSDU Search CLI (uses _auth.py for tokens)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              %(prog)s "osdu:wks:work-product-component--StratigraphicColumn:*"
              %(prog)s --id "dev:work-product-component--StratigraphicColumn:ICS2017:"
              %(prog)s --list-kinds Stratigraphic
        """),
    )
    p.add_argument("kinds", nargs="*", help="OSDU kind pattern(s) to search")
    p.add_argument("-q", "--query", default="*", help="Search query (default: *)")
    p.add_argument("-l", "--limit", type=int, default=50, help="Max results per kind (default: 50)")
    p.add_argument("-o", "--output", choices=["table", "json"], default="table")
    p.add_argument("-i", "--instance", default="eqndev", help="Instance name (default: eqndev)")
    p.add_argument("--id", dest="record_id", help="Fetch a single record by ID from Storage")
    p.add_argument("--list-kinds", dest="pattern", help="List kinds matching a pattern")

    args = p.parse_args()

    if args.record_id:
        cmd_fetch(args)
    elif args.pattern:
        cmd_list_kinds(args)
    elif args.kinds:
        cmd_search(args)
    else:
        p.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
