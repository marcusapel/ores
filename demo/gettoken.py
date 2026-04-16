#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gettoken.py — Mint an OSDU access token for swedev or preship.

Secrets are read from environment variables (store in .bashrc):
  SWEDEV_REFRESH_TOKEN    — Equinor SWE dev (refresh_token grant)
  PRESHIP_CLIENT_ID       — MS pre-ship M26 (client_credentials grant)
  PRESHIP_CLIENT_SECRET   — MS pre-ship M26

Usage:
  python demo/gettoken.py swedev          # prints access token
  python demo/gettoken.py preship         # prints access token

  # bash one-liners
  export SWEDEV_TOKEN=$(python demo/gettoken.py swedev)
  export PRESHIP_TOKEN=$(python demo/gettoken.py preship)

  # verbose (shows expiry etc. on stderr, token on stdout)
  python demo/gettoken.py swedev -v

  # show both
  eval "$(python demo/gettoken.py --export)"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict

try:
    import httpx
except ImportError:
    sys.exit("Missing httpx — pip install httpx")


# ── Instance defaults (non-secret) ──────────────────────────────────────── #

INSTANCES: Dict[str, Dict[str, Any]] = {
    "swedev": {
        "label":     "Equinor SWE dev",
        "tenant_id": "3aa4a235-b6e2-48d5-9195-7fcf05b459b0",
        "client_id": "ebd2bfee-ecba-47b7-a33c-017d0131879d",
        "scope":     "7daee810-3f78-40c4-84c2-7a199428de18/.default openid offline_access",
        "grant":     "refresh_token",
        "hostname":  "equinorswedev.energy.azure.com",
        "partition": "dev",
        "legal_tag": "dev-equinor-private-default",
        "owners":    "data.default.owners@dev.dataservices.energy",
        "viewers":   "data.default.viewers@dev.dataservices.energy",
        "countries": "NO",
    },
    "preship": {
        "label":     "MS pre-ship M26",
        "tenant_id": "58975fd3-4977-44d0-bea8-37af0baac100",
        "client_id": None,   # from env: PRESHIP_CLIENT_ID
        "scope":     None,   # derived from client_id
        "grant":     "client_credentials",
        "hostname":  "osdu-ship.msft-osdu-test.org",
        "partition": "opendes",
        "legal_tag": "opendes-RDDMS-LegalTag",
        "owners":    "data.default.owners@opendes.contoso.com",
        "viewers":   "data.default.viewers@opendes.contoso.com",
        "countries": "US",
    },
}

# Aliases so existing INSTANCE_ names work too
ALIASES = {"eqndev": "swedev"}


# ── Token minting ────────────────────────────────────────────────────────── #

def mint_token(name: str, *, verbose: bool = False) -> str:
    """Mint an access token for the named instance. Returns the token string."""

    name = ALIASES.get(name.lower(), name.lower())
    if name not in INSTANCES:
        sys.exit(f"Unknown instance '{name}'. Choose from: {', '.join(INSTANCES)}")

    inst = INSTANCES[name]
    tenant = inst["tenant_id"]
    url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"

    if inst["grant"] == "refresh_token":
        # swedev: refresh_token flow
        refresh = os.environ.get("SWEDEV_REFRESH_TOKEN", "")
        if not refresh:
            sys.exit("ERROR: SWEDEV_REFRESH_TOKEN not set.\n"
                     "  Add to ~/.bashrc:  export SWEDEV_REFRESH_TOKEN='...'")
        form = {
            "grant_type":    "refresh_token",
            "client_id":     inst["client_id"],
            "refresh_token": refresh,
            "scope":         inst["scope"],
        }
        label = "refresh_token"

    elif inst["grant"] == "client_credentials":
        # preship: client_credentials flow
        client_id = os.environ.get("PRESHIP_CLIENT_ID", inst["client_id"] or "")
        secret    = os.environ.get("PRESHIP_CLIENT_SECRET", "")
        if not client_id or not secret:
            sys.exit("ERROR: PRESHIP_CLIENT_ID and/or PRESHIP_CLIENT_SECRET not set.\n"
                     "  Add to ~/.bashrc:\n"
                     "    export PRESHIP_CLIENT_ID='...'\n"
                     "    export PRESHIP_CLIENT_SECRET='...'")
        scope = inst["scope"] or f"{client_id}/.default"
        form = {
            "grant_type":    "client_credentials",
            "client_id":     client_id,
            "client_secret": secret,
            "scope":         scope,
        }
        label = "client_credentials"
    else:
        sys.exit(f"Unknown grant type '{inst['grant']}' for '{name}'")

    r = httpx.post(url, data=form, timeout=30)
    if not r.is_success:
        sys.exit(f"Auth failed ({label}): {r.status_code}\n{r.text[:500]}")

    data = r.json()
    token = data.get("access_token")
    if not token:
        sys.exit(f"No access_token in response: {list(data.keys())}")

    if verbose:
        exp = data.get("expires_in", "?")
        print(f"# {inst['label']} ({label}) — expires_in={exp}s", file=sys.stderr)

    return token


# ── ETP URL helper ───────────────────────────────────────────────────────── #

def etp_url(name: str) -> str:
    """Return the wss:// ETP endpoint for the named instance."""
    name = ALIASES.get(name.lower(), name.lower())
    host = INSTANCES[name]["hostname"]
    return f"wss://{host}/api/reservoir-ddms-etp/v2/"


def partition(name: str) -> str:
    """Return the data partition for the named instance."""
    name = ALIASES.get(name.lower(), name.lower())
    return INSTANCES[name]["partition"]


# ── CLI ──────────────────────────────────────────────────────────────────── #

def main():
    ap = argparse.ArgumentParser(
        description="Mint an OSDU access token for swedev or preship",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  python demo/gettoken.py swedev
  python demo/gettoken.py preship -v
  export SWEDEV_TOKEN=$(python demo/gettoken.py swedev)
  eval "$(python demo/gettoken.py --export)"
""",
    )
    ap.add_argument("instance", nargs="?", default=None,
                    help="Instance name: swedev | preship (default: swedev)")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="Print metadata to stderr")
    ap.add_argument("--export", action="store_true",
                    help="Print bash export lines for ALL instances "
                         "(eval-friendly)")
    ap.add_argument("--json", action="store_true",
                    help="Print JSON with token + metadata")
    args = ap.parse_args()

    if args.export:
        # Export tokens for all instances
        lines = []
        for name in INSTANCES:
            try:
                tok = mint_token(name, verbose=True)
                var = f"{name.upper()}_TOKEN"
                lines.append(f"export {var}='{tok}'")
            except SystemExit as e:
                print(f"# skip {name}: {e}", file=sys.stderr)
        print("\n".join(lines))
        return

    inst_name = args.instance or "swedev"
    token = mint_token(inst_name, verbose=args.verbose)

    if args.json:
        name = ALIASES.get(inst_name.lower(), inst_name.lower())
        print(json.dumps({
            "instance":  name,
            "token":     token,
            "etp_url":   etp_url(name),
            "partition": partition(name),
        }, indent=2))
    else:
        print(token)


if __name__ == "__main__":
    main()
