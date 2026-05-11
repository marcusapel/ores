#!/usr/bin/env python3
"""
mint_refresh_token.py - Interactive PKCE login to get a refresh_token.

Opens a browser for user sign-in, captures the authorization code via a
localhost callback, and exchanges it for tokens.  Prints the refresh_token
so you can paste it into k8s/secret.yaml.

Prerequisites:
  - The app registration must have http://localhost:8400/callback as a
    redirect URI (Web or SPA platform).
  - "Allow public client flows" should be enabled in Azure Portal
    (Authentication blade) if the app has no client_secret.

Usage:
  python demo/mint_refresh_token.py

  # Or override defaults:
  python demo/mint_refresh_token.py \\
      --client-id 21b442a9-6c1c-4551-b234-afdf010dd3be \\
      --tenant 3aa4a235-b6e2-48d5-9195-7fcf05b459b0 \\
      --scope "https://energy.azure.com/.default openid offline_access"

After sign-in, paste the printed REFRESH_TOKEN into:
  k8s/secret.yaml → INSTANCE_EQNDEV_REFRESH_TOKEN
"""
from __future__ import annotations

import argparse
import hashlib
import http.server
import secrets
import sys
import threading
import urllib.parse
import webbrowser

try:
    import httpx
except ImportError:
    sys.exit("pip install httpx")

# ── Defaults (ores-dev app) ──────────────────────────────────────────────
DEFAULT_CLIENT_ID = "21b442a9-6c1c-4551-b234-afdf010dd3be"
DEFAULT_TENANT    = "3aa4a235-b6e2-48d5-9195-7fcf05b459b0"
DEFAULT_SCOPE     = "https://energy.azure.com/.default openid offline_access"
REDIRECT_PORT     = 8400
REDIRECT_URI      = f"http://localhost:{REDIRECT_PORT}/callback"


def _pkce_pair():
    """Generate PKCE code_verifier + code_challenge (S256)."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    import base64
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def main():
    ap = argparse.ArgumentParser(description="Mint a refresh_token via interactive PKCE login")
    ap.add_argument("--client-id", default=DEFAULT_CLIENT_ID, help="App (client) ID")
    ap.add_argument("--tenant", default=DEFAULT_TENANT, help="Azure AD tenant ID")
    ap.add_argument("--scope", default=DEFAULT_SCOPE, help="Space-separated scopes")
    args = ap.parse_args()

    client_id = args.client_id
    tenant = args.tenant
    scopes = args.scope

    auth_base = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0"
    authorize_url = f"{auth_base}/authorize"
    token_url = f"{auth_base}/token"

    code_verifier, code_challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)

    # ── Build authorize URL ──────────────────────────────────────────────
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": scopes,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    url = f"{authorize_url}?{urllib.parse.urlencode(params)}"

    # ── Tiny HTTP server to capture the callback ─────────────────────────
    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            qs = urllib.parse.urlparse(self.path).query
            p = urllib.parse.parse_qs(qs)
            result["code"] = p.get("code", [None])[0]
            result["state"] = p.get("state", [None])[0]
            result["error"] = p.get("error", [None])[0]
            result["error_description"] = p.get("error_description", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            if result.get("code"):
                self.wfile.write(b"<h2>Login successful!</h2><p>You can close this tab.</p>")
            else:
                msg = result.get("error_description") or result.get("error") or "Unknown error"
                self.wfile.write(f"<h2>Error</h2><p>{msg}</p>".encode())
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        def log_message(self, *a):
            pass  # suppress request logging

    server = http.server.HTTPServer(("127.0.0.1", REDIRECT_PORT), Handler)

    print(f"\n  App:    {client_id}\n  Scopes: {scopes}\n")
    print(f"  Open this URL in your browser:\n")
    print(f"  {url}\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass  # WSL / headless - user clicks the URL above
    print(f"  Waiting for callback on http://localhost:{REDIRECT_PORT}/callback ...\n")
    server.handle_request()  # blocks until one request
    server.server_close()

    if result.get("error"):
        print(f"ERROR: {result['error']}: {result.get('error_description', '')}")
        sys.exit(1)

    code = result.get("code")
    if not code:
        print("ERROR: No authorization code received.")
        sys.exit(1)

    if result.get("state") != state:
        print("ERROR: State mismatch (possible CSRF).")
        sys.exit(1)

    # ── Exchange code for tokens ─────────────────────────────────────────
    print("  Exchanging code for tokens...")
    r = httpx.post(token_url, data={
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": code_verifier,
        "scope": scopes,
    }, timeout=30)

    if not r.is_success:
        print(f"ERROR: Token exchange failed ({r.status_code}):\n{r.text[:500]}")
        sys.exit(1)

    body = r.json()
    access_token = body.get("access_token", "")
    refresh_token = body.get("refresh_token", "")
    expires_in = body.get("expires_in", "?")

    print(f"\n{'='*60}")
    print(f"  Access token:  {access_token[:40]}... (expires_in={expires_in}s)")
    if refresh_token:
        print(f"\n  REFRESH TOKEN (paste into k8s/secret.yaml):\n")
        print(f"  {refresh_token}")
        print(f"\n{'='*60}")
        print(f"\n  Update k8s/secret.yaml:")
        print(f'  INSTANCE_EQNDEV_REFRESH_TOKEN: "{refresh_token[:60]}..."')
        print(f'  INSTANCE_EQNDEV_CLIENT_ID: "{client_id}"')
        print(f'  INSTANCE_EQNDEV_SCOPE: "{scopes}"')
    else:
        print("\n  WARNING: No refresh_token returned!")
        print("  Make sure 'offline_access' is in your scopes")
        print("  and the app has 'Allow public client flows' enabled.")
    print()


if __name__ == "__main__":
    main()
