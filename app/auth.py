
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

# SMDA OPUS — same tenant, different API resource.
# SMDA_CLIENT_ID here is the SMDA *API* resource App ID (audience).
# We still use our own CLIENT_ID for the PKCE authorize, but request
# SMDA-specific scopes so the resulting token targets the SMDA API.
SMDA_CLIENT_ID = os.getenv("SMDA_CLIENT_ID", "")  # SMDA API App ID
_smda_scope_raw = os.getenv("SMDA_SCOPE", "").split()
# Resource-specific scopes (e.g. 691a29c5.../user_impersonation)
SMDA_SCOPES = [s for s in _smda_scope_raw if s not in ("openid", "offline_access")] or None
# Full scope list for authorize/token requests (includes openid + offline_access)
SMDA_SCOPES_FULL = list(SMDA_SCOPES or []) + ["openid", "offline_access"] if SMDA_SCOPES else None

AUTH_BASE = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0"
AUTHORIZE_URL = f"{AUTH_BASE}/authorize"
TOKEN_URL = f"{AUTH_BASE}/token"

# True when a shared refresh-token is available (env or .env)
ENV_REFRESH_TOKEN: Optional[str] = os.getenv("REFRESH_TOKEN") or os.getenv("refresh_token") or None
AUTH_MODE = "env_token" if ENV_REFRESH_TOKEN else "per_user_pkce"

router = APIRouter(tags=["auth"])

# Paths that must be accessible without any token
PUBLIC_PATHS: set[str] = {"/login", "/login-page", "/login/smda",
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


@router.get("/login/smda")
async def login_smda(request: Request):
    """Redirect user to Azure AD authorize endpoint with PKCE (SMDA scopes).

    Uses the SAME client_id (our app registration) but requests SMDA-specific
    scopes so the resulting token targets the SMDA API.  No .default scope.
    Reuses /auth/callback (same redirect URI already registered).
    """
    if not CLIENT_ID or not SMDA_SCOPES_FULL or not TENANT:
        return JSONResponse({"error": "SMDA_SCOPE not configured"}, status_code=500)

    redirect_uri = _build_redirect_uri(request)
    code_verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(32)
    request.session["pkce_verifier"] = code_verifier
    request.session["pkce_state"] = state
    request.session["redirect_uri"] = redirect_uri
    request.session["pkce_target"] = "smda"

    async with AsyncOAuth2Client(
        client_id=CLIENT_ID,
        scope=" ".join(SMDA_SCOPES_FULL),
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
    """Exchange authorization code for tokens.

    Uses session['pkce_target'] to decide whether to store as OSDU or SMDA tokens.
    """
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code:
        return JSONResponse({"error": "Missing authorization code"}, status_code=400)

    expected_state = request.session.get("pkce_state")
    if state != expected_state:
        return JSONResponse({"error": "State mismatch — possible CSRF"}, status_code=400)

    code_verifier = request.session.get("pkce_verifier", "")
    redirect_uri = request.session.get("redirect_uri", _build_redirect_uri(request))
    target = request.session.get("pkce_target", "osdu")

    scopes = SMDA_SCOPES_FULL if target == "smda" else SCOPES

    async with AsyncOAuth2Client(
        client_id=CLIENT_ID,
        scope=" ".join(scopes),
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
    request.session.pop("pkce_target", None)

    if target == "smda":
        request.session["smda_access_token"] = token.get("access_token", "")
        request.session["smda_refresh_token"] = token.get("refresh_token", "")
        request.session["smda_token_exp"] = time.time() + int(token.get("expires_in", 3600)) - 60
        log.info("PKCE login successful (SMDA), redirecting to /strat")
        return RedirectResponse("/strat")
    else:
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
    """Return SMDA access token, refreshing if expired.

    Tries (in order):
    1. Session SMDA token (from /login/smda PKCE flow)
    2. ENV_REFRESH_TOKEN with SMDA scopes (zero-click)

    Returns None if neither source is available.
    """
    if not SMDA_SCOPES_FULL or not CLIENT_ID or not TENANT:
        log.debug("SMDA not configured (SMDA_SCOPE)")
        return None

    # 1) Session token
    if hasattr(request, "session"):
        at = request.session.get("smda_access_token")
        exp = request.session.get("smda_token_exp", 0)
        if at and time.time() < exp:
            return at

        rt = request.session.get("smda_refresh_token")
        if rt:
            result = await _refresh_smda_token(rt)
            if result:
                request.session["smda_access_token"] = result["access_token"]
                request.session["smda_refresh_token"] = result.get("refresh_token", rt)
                request.session["smda_token_exp"] = result["exp"]
                return result["access_token"]

    # 2) ENV refresh token fallback
    if ENV_REFRESH_TOKEN:
        global _smda_cached_token, _smda_cached_exp
        if _smda_cached_token and time.time() < _smda_cached_exp:
            return _smda_cached_token
        result = await _refresh_smda_token(ENV_REFRESH_TOKEN)
        if result:
            _smda_cached_token = result["access_token"]
            _smda_cached_exp = result["exp"]
            return result["access_token"]

    log.debug("No SMDA token available (no session, no env refresh token)")
    return None


_smda_cached_token: Optional[str] = None
_smda_cached_exp: float = 0.0


async def _refresh_smda_token(refresh_token: str) -> Optional[Dict[str, Any]]:
    """Exchange a refresh token for an SMDA-audience access token."""
    try:
        async with AsyncOAuth2Client(client_id=CLIENT_ID, scope=SMDA_SCOPES_FULL) as cli:
            token = await cli.fetch_token(
                TOKEN_URL,
                grant_type="refresh_token",
                refresh_token=refresh_token,
                scope=" ".join(SMDA_SCOPES_FULL),
            )
        at = token.get("access_token", "")
        if at:
            log.info("Minted SMDA access token (expires_in=%s)", token.get("expires_in"))
            return {
                "access_token": at,
                "refresh_token": token.get("refresh_token") or refresh_token,
                "exp": time.time() + max(int(token.get("expires_in", 3600)) - 60, 60),
            }
    except Exception as e:
        log.warning("Failed to mint SMDA token: %s", e)
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
        "smda_scopes": SMDA_SCOPES_FULL,
        "mode": AUTH_MODE,
        "env_token_available": bool(ENV_REFRESH_TOKEN),
        "smda_configured": bool(SMDA_SCOPES),
        "smda_api_id": SMDA_CLIENT_ID[:8] + "..." if SMDA_CLIENT_ID else "",
        "session_logged_in": logged_in,
        "smda_logged_in": smda_logged_in,
    }
