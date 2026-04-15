
from __future__ import annotations
import os
import secrets
import time
import logging
from typing import Optional, Dict, Any
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, JSONResponse
from authlib.integrations.httpx_client import AsyncOAuth2Client

log = logging.getLogger("rddms-admin")

# ─────────────────────────────────────────────────────────────
# Azure AD / Microsoft identity platform
# Mode 1: shared refresh_token from env  (zero-click, demo)
# Mode 2: per-user PKCE login            (fallback when no env token)
# ─────────────────────────────────────────────────────────────
TENANT = os.getenv("AZURE_TENANT_ID", "")
CLIENT_ID = os.getenv("AZURE_CLIENT_ID", "")
SCOPES = os.getenv("AZURE_SCOPE", "openid offline_access").split()

# SMDA API resource App ID (audience) — used by az CLI to mint tokens.
SMDA_CLIENT_ID = os.getenv("SMDA_CLIENT_ID", "")

AUTH_BASE = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0"
AUTHORIZE_URL = f"{AUTH_BASE}/authorize"
TOKEN_URL = f"{AUTH_BASE}/token"

# True when a shared refresh-token is available (env or .env)
ENV_REFRESH_TOKEN: Optional[str] = os.getenv("REFRESH_TOKEN") or os.getenv("refresh_token") or None
AUTH_MODE = "env_token" if ENV_REFRESH_TOKEN else "per_user_pkce"

router = APIRouter(tags=["auth"])

# Paths that must be accessible without any token
PUBLIC_PATHS: set[str] = {"/login", "/login-page",
                          "/auth/callback", "/auth", "/logout"}

# ─────────────────────────────────────────────────────────────
# Mode 1 — shared env token
# ─────────────────────────────────────────────────────────────
_cached_env_token: Dict[str, Any] = {}
_cached_env_token_exp: float = 0.0

async def tokens_from_env() -> Optional[Dict[str, Any]]:
    """Mint access_token from the shared REFRESH_TOKEN in env.  Returns None if unavailable."""
    global _cached_env_token, _cached_env_token_exp
    if not ENV_REFRESH_TOKEN or not CLIENT_ID or not TENANT:
        return None
    if _cached_env_token and time.time() < _cached_env_token_exp:
        return _cached_env_token

    async with AsyncOAuth2Client(client_id=CLIENT_ID, scope=SCOPES) as cli:
        token = await cli.fetch_token(
            TOKEN_URL,
            grant_type="refresh_token",
            refresh_token=ENV_REFRESH_TOKEN,
            scope=" ".join(SCOPES),
        )
    result = {
        "access_token": token.get("access_token"),
        "refresh_token": token.get("refresh_token") or ENV_REFRESH_TOKEN,
        "expires_in": token.get("expires_in"),
        "id_token": token.get("id_token", ""),
    }
    _cached_env_token = result
    _cached_env_token_exp = time.time() + max(int(token.get("expires_in", 3600)) - 60, 60)
    return result


# ─────────────────────────────────────────────────────────────
# Mode 2 — per-user PKCE login (Authorization Code + PKCE)
# ─────────────────────────────────────────────────────────────

def _build_redirect_uri(request: Request) -> str:
    """Build callback URI from the incoming request (works behind proxies / Codespaces)."""
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.hostname
    return f"{proto}://{host}/auth/callback"


@router.get("/login")
async def login(request: Request):
    """Redirect user to Azure AD authorize endpoint with PKCE (OSDU scopes)."""
    if not CLIENT_ID or not TENANT:
        return JSONResponse({"error": "AZURE_CLIENT_ID / AZURE_TENANT_ID not configured"}, status_code=500)

    redirect_uri = _build_redirect_uri(request)
    code_verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(32)
    request.session["pkce_verifier"] = code_verifier
    request.session["pkce_state"] = state
    request.session["redirect_uri"] = redirect_uri
    request.session["pkce_target"] = "osdu"

    async with AsyncOAuth2Client(
        client_id=CLIENT_ID,
        scope=" ".join(SCOPES),
        redirect_uri=redirect_uri,
        code_challenge_method="S256",
    ) as cli:
        url, _state = cli.create_authorization_url(
            AUTHORIZE_URL,
            state=state,
            code_verifier=code_verifier,
        )
    return RedirectResponse(url)


@router.get("/auth/callback")
async def auth_callback(request: Request):
    """Exchange authorization code for tokens (OSDU PKCE flow)."""
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code:
        return JSONResponse({"error": "Missing authorization code"}, status_code=400)

    expected_state = request.session.get("pkce_state")
    if state != expected_state:
        return JSONResponse({"error": "State mismatch — possible CSRF"}, status_code=400)

    code_verifier = request.session.get("pkce_verifier", "")
    redirect_uri = request.session.get("redirect_uri", _build_redirect_uri(request))

    async with AsyncOAuth2Client(
        client_id=CLIENT_ID,
        scope=" ".join(SCOPES),
        redirect_uri=redirect_uri,
        code_challenge_method="S256",
    ) as cli:
        token = await cli.fetch_token(
            TOKEN_URL,
            code=code,
            code_verifier=code_verifier,
        )

    # Clean up PKCE state
    request.session.pop("pkce_verifier", None)
    request.session.pop("pkce_state", None)

    request.session["access_token"] = token.get("access_token", "")
    request.session["refresh_token"] = token.get("refresh_token", "")
    request.session["token_exp"] = time.time() + int(token.get("expires_in", 3600)) - 60
    request.session["user"] = token.get("id_token", "")
    log.info("PKCE login successful (OSDU), redirecting to /")
    return RedirectResponse("/")


@router.get("/logout")
async def logout(request: Request):
    """Clear session and redirect to home."""
    request.session.clear()
    return RedirectResponse("/login")


async def tokens_from_session(request: Request) -> Optional[Dict[str, Any]]:
    """Return OSDU access token from session, refreshing if expired."""
    at = request.session.get("access_token")
    if not at:
        return None

    exp = request.session.get("token_exp", 0)
    if time.time() < exp:
        return {"access_token": at}

    rt = request.session.get("refresh_token")
    if not rt:
        return None
    try:
        async with AsyncOAuth2Client(client_id=CLIENT_ID, scope=SCOPES) as cli:
            token = await cli.fetch_token(
                TOKEN_URL,
                grant_type="refresh_token",
                refresh_token=rt,
                scope=" ".join(SCOPES),
            )
        request.session["access_token"] = token.get("access_token", "")
        request.session["refresh_token"] = token.get("refresh_token") or rt
        request.session["token_exp"] = time.time() + int(token.get("expires_in", 3600)) - 60
        return {"access_token": token["access_token"]}
    except Exception as e:
        log.warning("Session refresh failed: %s — redirecting to login", e)
        return None


# ─────────────────────────────────────────────────────────────
# SMDA token  (separate audience — uses session SMDA refresh token)
# ─────────────────────────────────────────────────────────────

async def smda_access_token(request: Request) -> Optional[str]:
    """Return SMDA access token via Azure CLI.

    Uses `az account get-access-token --resource <SMDA_CLIENT_ID>` which
    leverages Microsoft's first-party app registration with broad consent
    in Equinor's tenant.  Requires the user to have run `az login`.

    Returns None if az CLI is unavailable or not logged in.
    """
    if not SMDA_CLIENT_ID:
        log.debug("SMDA_CLIENT_ID not configured")
        return None

    global _smda_cached_token, _smda_cached_exp
    if _smda_cached_token and time.time() < _smda_cached_exp:
        return _smda_cached_token

    result = await _smda_token_from_az_cli()
    if result:
        _smda_cached_token = result["access_token"]
        _smda_cached_exp = result["exp"]
        return result["access_token"]

    log.debug("No SMDA token available — run 'az login'")
    return None


_smda_cached_token: Optional[str] = None
_smda_cached_exp: float = 0.0


async def _smda_token_from_az_cli() -> Optional[Dict[str, Any]]:
    """Get SMDA-scoped token via Azure CLI (`az account get-access-token`).

    Azure CLI uses Microsoft's first-party app registration (04b07795-...)
    which has broad consent in most enterprise tenants.  This is the
    "standard Equinor token" approach.
    """
    import asyncio
    import json as _json
    try:
        proc = await asyncio.create_subprocess_exec(
            "az", "account", "get-access-token",
            "--resource", SMDA_CLIENT_ID,
            "-o", "json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()[:200]
            log.debug("az CLI SMDA token failed (rc=%d): %s", proc.returncode, err)
            return None
        data = _json.loads(stdout)
        at = data.get("accessToken", "")
        if not at:
            return None
        exp_ts = data.get("expires_on", 0)
        if isinstance(exp_ts, str):
            exp_ts = int(exp_ts)
        exp = exp_ts - 60 if exp_ts > 0 else time.time() + 3000
        log.info("Got SMDA token via az CLI (expires %s)", data.get("expiresOn", "?"))
        return {"access_token": at, "exp": exp}
    except FileNotFoundError:
        log.debug("az CLI not found — skipping az token strategy")
    except asyncio.TimeoutError:
        log.warning("az CLI timed out getting SMDA token")
    except Exception as e:
        log.debug("az CLI SMDA token error: %s", e)
    return None


# ─────────────────────────────────────────────────────────────
# Diagnostics endpoint
# ─────────────────────────────────────────────────────────────
@router.get("/auth")
async def auth_info(request: Request):
    logged_in = bool(request.session.get("access_token")) if hasattr(request, "session") else False
    smda_logged_in = bool(request.session.get("smda_access_token")) if hasattr(request, "session") else False
    return {
        "azure_tenant": TENANT[:8] + "..." if TENANT else "",
        "client_id": CLIENT_ID[:8] + "..." if CLIENT_ID else "",
        "scopes": SCOPES,
        "mode": AUTH_MODE,
        "env_token_available": bool(ENV_REFRESH_TOKEN),
        "smda_api_id": SMDA_CLIENT_ID[:8] + "..." if SMDA_CLIENT_ID else "",
        "session_logged_in": logged_in,
    }
