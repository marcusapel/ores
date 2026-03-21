#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sumo_discover.py — Search the Equinor Sumo REST API (and optionally other
REP services) for Drogon data: cases, surfaces/maps, tables, etc.

Requires a valid Equinor Azure AD token. Uses the same .env mechanism as
the rest of the ORES project.

Usage:
  # List Drogon cases
  python demo/sumo_discover.py --cases

  # List surfaces in a case (all ensembles)
  python demo/sumo_discover.py --surfaces --case-uuid <UUID>

  # Search for any Drogon objects
  python demo/sumo_discover.py --search "Drogon AND class:surface"

  # Quick scan — find Drogon cases and show surface counts
  python demo/sumo_discover.py --scan

  # Raw Lucene query against /search
  python demo/sumo_discover.py --raw "fmu.case.name:Drogon"

  # Check Sumo status
  python demo/sumo_discover.py --status

Token sourcing (in priority order):
  1. --token CLI flag
  2. SUMO_TOKEN env var
  3. TOKEN env var (from get_token.py)
  4. Mint new token via REFRESH_TOKEN in .env  (OSDU scope — may need
     separate Sumo scope: api://9e5443dd-3431-4690-9617-31eed61cb55a/.default)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent

# ── Sumo API base URLs ──────────────────────────────────────────────────
SUMO_PROD = "https://main-sumo-prod.radix.equinor.com/api/v1"
SUMO_DEV  = "https://main-sumo-dev.radix.equinor.com/api/v1"

# Sumo OAuth2 scope (Equinor tenant)
SUMO_SCOPE = "api://9e5443dd-3431-4690-9617-31eed61cb55a/.default"

# Default search asset
DEFAULT_ASSET = "Drogon"


# ── .env loader (same as get_token.py) ───────────────────────────────────
def _parse_dotenv(path: Path) -> Dict[str, str]:
    env: Dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        env[key.strip()] = val.strip()
    return env


def _load_env() -> Dict[str, str]:
    merged = _parse_dotenv(REPO_ROOT / ".env")
    merged.update({k: v for k, v in os.environ.items() if v})
    return merged


# ── Token helpers ────────────────────────────────────────────────────────
def _mint_token(env: Dict[str, str], scope: str | None = None) -> str:
    """Exchange REFRESH_TOKEN for an access token (custom scope)."""
    tenant        = env.get("AZURE_TENANT_ID", "")
    client_id     = env.get("AZURE_CLIENT_ID", "")
    refresh_token = env.get("REFRESH_TOKEN", "")
    scope         = scope or env.get("AZURE_SCOPE", "")

    if not all([tenant, client_id, refresh_token, scope]):
        return ""

    url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    resp = httpx.post(url, data={
        "grant_type":    "refresh_token",
        "client_id":     client_id,
        "refresh_token": refresh_token,
        "scope":         scope,
    }, timeout=30)
    if not resp.is_success:
        print(f"  [token] mint failed ({resp.status_code}): {resp.text[:200]}", file=sys.stderr)
        return ""
    return resp.json().get("access_token", "")


def resolve_token(cli_token: str | None = None) -> str:
    """Resolve a bearer token from CLI flag → env vars → .env mint."""
    if cli_token:
        return cli_token

    env = _load_env()

    # Check explicit env vars
    for var in ("SUMO_TOKEN", "TOKEN"):
        t = env.get(var, "")
        if t and len(t) > 40:
            return t

    # Try minting with Sumo scope first, then fall back to OSDU scope
    for scope in (SUMO_SCOPE, env.get("AZURE_SCOPE", "")):
        t = _mint_token(env, scope)
        if t:
            return t

    return ""


# ── HTTP helpers ─────────────────────────────────────────────────────────
def _headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }


def sumo_get(base: str, path: str, token: str,
             params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    url = f"{base}{path}"
    r = httpx.get(url, headers=_headers(token), params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def sumo_post(base: str, path: str, token: str,
              body: Any = None) -> Dict[str, Any]:
    url = f"{base}{path}"
    r = httpx.post(url, headers=_headers(token), json=body, timeout=60)
    r.raise_for_status()
    return r.json()


# ── Commands ─────────────────────────────────────────────────────────────

def cmd_status(base: str, token: str) -> None:
    """GET /status — verify connectivity."""
    try:
        data = sumo_get(base, "/status", token)
        print(json.dumps(data, indent=2))
    except httpx.HTTPStatusError as e:
        print(f"Status check failed: {e.response.status_code}", file=sys.stderr)
        print(e.response.text[:500], file=sys.stderr)


def cmd_userdata(base: str, token: str) -> None:
    """GET /userdata — who am I in Sumo?"""
    try:
        data = sumo_get(base, "/userdata", token)
        print(json.dumps(data, indent=2))
    except httpx.HTTPStatusError as e:
        print(f"User data failed: {e.response.status_code}", file=sys.stderr)


def cmd_search_cases(base: str, token: str, asset: str, size: int = 50) -> List[Dict]:
    """Search for FMU cases by asset name."""
    query = f"_sumo.parent_object:\"\" AND fmu.case.name:*"
    if asset:
        query = f'class:case AND masterdata.smda.field.identifier:"{asset}"'

    params = {"$query": query, "$size": str(size),
              "$select": "fmu.case.name,fmu.case.uuid,_sumo.status,fmu.case.user.id,masterdata.smda.field.identifier"}
    try:
        data = sumo_get(base, "/searchroot", token, params)
    except httpx.HTTPStatusError as e:
        # Fallback: try /objects/search
        print(f"  [searchroot failed {e.response.status_code}, trying /objects/search]", file=sys.stderr)
        try:
            data = sumo_get(base, "/objects/search", token, params)
        except httpx.HTTPStatusError as e2:
            print(f"  Search also failed: {e2.response.status_code}", file=sys.stderr)
            print(e2.response.text[:500], file=sys.stderr)
            return []

    hits = data.get("hits", {}).get("hits", [])
    total = data.get("hits", {}).get("total", {})
    total_val = total.get("value", len(hits)) if isinstance(total, dict) else total

    print(f"\n{'='*70}")
    print(f"  Drogon cases in Sumo ({base.split('//')[1].split('/')[0]})")
    print(f"  Total: {total_val}   Showing: {len(hits)}")
    print(f"{'='*70}\n")

    cases = []
    for h in hits:
        src = h.get("_source", h)
        fmu = src.get("fmu", {}).get("case", {})
        status = src.get("_sumo", {}).get("status", "?")
        user = src.get("fmu", {}).get("case", {}).get("user", {}).get("id", "?")
        field = ""
        md = src.get("masterdata", {}).get("smda", {}).get("field", [])
        if md:
            field = md[0].get("identifier", "") if isinstance(md, list) else md.get("identifier", "")
        case_info = {
            "uuid": fmu.get("uuid", h.get("_id", "?")),
            "name": fmu.get("name", "?"),
            "status": status,
            "user": user,
            "field": field,
        }
        cases.append(case_info)
        print(f"  {case_info['uuid'][:12]}…  {case_info['name']:<35} "
              f"status={case_info['status']:<10} user={case_info['user']}")

    return cases


def cmd_surfaces(base: str, token: str, case_uuid: str,
                 size: int = 100, ensemble: str = "") -> None:
    """List surfaces in a case."""
    query = "class:surface"
    if ensemble:
        query += f' AND fmu.iteration.name:"{ensemble}"'

    params = {"$query": query, "$size": str(size),
              "$select": "data.name,data.tagname,data.content,data.format,"
                         "fmu.iteration.name,fmu.realization.id,data.stratigraphic,"
                         "data.vertical_domain,data.bbox",
              "$buckets": "data.name,data.content,data.tagname,fmu.iteration.name",
              "$bucketsize": "50"}

    path = f"/objects('{case_uuid}')/search"
    try:
        data = sumo_get(base, path, token, params)
    except httpx.HTTPStatusError as e:
        print(f"  Surface search failed: {e.response.status_code}", file=sys.stderr)
        print(e.response.text[:500], file=sys.stderr)
        return

    hits = data.get("hits", {}).get("hits", [])
    total = data.get("hits", {}).get("total", {})
    total_val = total.get("value", len(hits)) if isinstance(total, dict) else total
    buckets = data.get("aggregations", {})

    print(f"\n{'='*70}")
    print(f"  Surfaces in case {case_uuid[:12]}…")
    print(f"  Total: {total_val}   Showing: {len(hits)}")
    print(f"{'='*70}\n")

    # Show bucket summaries if available
    for bname in ("data.name", "data.content", "data.tagname", "fmu.iteration.name"):
        b = buckets.get(bname, {}).get("buckets", [])
        if b:
            vals = [f"{x['key']}({x['doc_count']})" for x in b[:20]]
            print(f"  {bname}: {', '.join(vals)}")
    if buckets:
        print()

    # Show first N surfaces
    for h in hits[:30]:
        src = h.get("_source", h)
        d = src.get("data", {})
        fmu = src.get("fmu", {})
        ens = fmu.get("iteration", {}).get("name", "?")
        real = fmu.get("realization", {}).get("id", "?")
        print(f"  {h.get('_id', '?')[:12]}…  name={d.get('name','?'):<25} "
              f"tag={d.get('tagname',''):<20} content={d.get('content','?'):<15} "
              f"ens={ens} real={real}")


def cmd_scan(base: str, token: str, asset: str) -> None:
    """Quick scan: find Drogon cases and count data types."""
    cases = cmd_search_cases(base, token, asset)
    if not cases:
        print("\nNo cases found. Try --search with a broader query.")
        return

    print(f"\n{'─'*70}")
    print(f"  Scanning first {min(5, len(cases))} case(s) for data types…")
    print(f"{'─'*70}\n")

    for c in cases[:5]:
        uuid = c["uuid"]
        for cls in ("surface", "table", "cube", "polygons", "dictionary"):
            query = f"class:{cls}"
            params = {"$query": query, "$size": "0"}
            path = f"/objects('{uuid}')/search"
            try:
                data = sumo_get(base, path, token, params)
                total = data.get("hits", {}).get("total", {})
                count = total.get("value", 0) if isinstance(total, dict) else total
            except httpx.HTTPStatusError:
                count = "ERR"
            print(f"  {c['name']:<35} {cls:<12} {count}")
        print()


def cmd_raw_search(base: str, token: str, query: str, size: int = 20) -> None:
    """Raw Lucene query against /search."""
    params = {"$query": query, "$size": str(size)}
    try:
        data = sumo_get(base, "/search", token, params)
    except httpx.HTTPStatusError as e:
        print(f"  Search failed: {e.response.status_code}", file=sys.stderr)
        print(e.response.text[:500], file=sys.stderr)
        return

    hits = data.get("hits", {}).get("hits", [])
    total = data.get("hits", {}).get("total", {})
    total_val = total.get("value", len(hits)) if isinstance(total, dict) else total

    print(f"\nQuery: {query}")
    print(f"Total: {total_val}   Showing: {len(hits)}\n")

    for h in hits:
        src = h.get("_source", h)
        cls = src.get("class", "?")
        d = src.get("data", {})
        fmu_case = src.get("fmu", {}).get("case", {}).get("name", "")
        print(f"  {h.get('_id', '?')[:12]}…  class={cls:<12} "
              f"name={d.get('name','?'):<25} case={fmu_case}")


def cmd_search(base: str, token: str, query: str, size: int = 20) -> None:
    """Convenience search — wraps raw search with common patterns."""
    cmd_raw_search(base, token, query, size)


# ── Main ─────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Discover Drogon data in the Equinor Sumo REST API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python demo/sumo_discover.py --status
  python demo/sumo_discover.py --cases
  python demo/sumo_discover.py --scan
  python demo/sumo_discover.py --surfaces --case-uuid <UUID>
  python demo/sumo_discover.py --search "class:surface AND data.name:Valysar"
  python demo/sumo_discover.py --raw "fmu.case.name:Drogon AND class:surface"
  python demo/sumo_discover.py --whoami
""")
    # Connection
    ap.add_argument("--env",   choices=["prod", "dev"], default="prod",
                    help="Sumo environment (default: prod)")
    ap.add_argument("--token", default=None,
                    help="Bearer token (or set SUMO_TOKEN / TOKEN env var)")
    ap.add_argument("--asset", default=DEFAULT_ASSET,
                    help=f"Asset/field filter (default: {DEFAULT_ASSET})")
    ap.add_argument("--size",  type=int, default=50,
                    help="Max results to return (default: 50)")

    # Commands
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--status",   action="store_true", help="Check Sumo API status")
    grp.add_argument("--whoami",   action="store_true", help="Show current user info")
    grp.add_argument("--cases",    action="store_true", help="List Drogon cases")
    grp.add_argument("--scan",     action="store_true", help="Scan cases for data types")
    grp.add_argument("--surfaces", action="store_true", help="List surfaces in a case")
    grp.add_argument("--search",   type=str,            help="Lucene query shortcut")
    grp.add_argument("--raw",      type=str,            help="Raw Lucene query against /search")

    # Surface options
    ap.add_argument("--case-uuid", default=None, help="Case UUID (for --surfaces)")
    ap.add_argument("--ensemble",  default="",   help="Ensemble filter (for --surfaces)")

    args = ap.parse_args()

    # Resolve API base
    base = SUMO_PROD if args.env == "prod" else SUMO_DEV

    # Resolve token
    token = resolve_token(args.token)
    if not token:
        print("ERROR: No valid token found.", file=sys.stderr)
        print("  Set SUMO_TOKEN env var, or pass --token, or ensure .env has REFRESH_TOKEN", file=sys.stderr)
        print(f"  Sumo scope: {SUMO_SCOPE}", file=sys.stderr)
        print(f"\n  Quick fix:  export SUMO_TOKEN=$(python app/get_token.py --quiet)", file=sys.stderr)
        sys.exit(1)

    print(f"[Sumo API: {base}]")

    # Dispatch
    if args.status:
        cmd_status(base, token)
    elif args.whoami:
        cmd_userdata(base, token)
    elif args.cases:
        cmd_search_cases(base, token, args.asset, args.size)
    elif args.scan:
        cmd_scan(base, token, args.asset)
    elif args.surfaces:
        if not args.case_uuid:
            print("ERROR: --surfaces requires --case-uuid", file=sys.stderr)
            sys.exit(1)
        cmd_surfaces(base, token, args.case_uuid, args.size, args.ensemble)
    elif args.search:
        cmd_search(base, token, args.search, args.size)
    elif args.raw:
        cmd_raw_search(base, token, args.raw, args.size)


if __name__ == "__main__":
    main()
