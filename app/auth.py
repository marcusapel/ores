
from __future__ import annotations
import os
import secrets
import time
import logging
from typing import Optional, Dict, Any
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, JSONResponse
from authlib.integrations.httpx_client import AsyncOAuth2Client
from .tokenstore import (
    upsert as _ts_upsert,
    fetch as _ts_fetch,
    delete as _ts_delete,
    decode_id_token_payload,
    get_cached_at as _ts_get_cached_at,
    set_cached_at as _ts_set_cached_at,
)

log = logging.getLogger("rddms-admin")

# ─────────────────────────────────────────────────────────────
# Azure AD / Microsoft identity platform
# Mode 1: shared refresh_token from env  (zero-click, demo)
# Mode 2: per-user PKCE login            (fallback when no env token)
#
# NOTE: These module-level vars are overwritten by instances.py
#       _apply_instance() at startup and on every instance switch.
#       Initial values are just safe defaults.
# ─────────────────────────────────────────────────────────────
TENANT = os.getenv("AZURE_TENANT_ID", "") or os.getenv("INSTANCE_EQNDEV_TENANT_ID", "")
CLIENT_ID = os.getenv("AZURE_CLIENT_ID", "") or os.getenv("INSTANCE_EQNDEV_CLIENT_ID", "")
SCOPES = (os.getenv("AZURE_SCOPE", "") or os.getenv("INSTANCE_EQNDEV_SCOPE", "openid offline_access")).split()

# SMDA API resource App ID (audience) - used by az CLI to mint tokens.
SMDA_CLIENT_ID = os.getenv("SMDA_CLIENT_ID", "")

AUTH_BASE = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0"
AUTHORIZE_URL = f"{AUTH_BASE}/authorize"
TOKEN_URL = f"{AUTH_BASE}/token"

# True when a shared refresh-token is available (from active instance)
ENV_REFRESH_TOKEN: Optional[str] = (
    os.getenv("REFRESH_TOKEN")
    or os.getenv("refresh_token")
    or None
)
AUTH_MODE = "env_token" if ENV_REFRESH_TOKEN else "per_user_pkce"

router = APIRouter(tags=["auth"])

# Paths that must be accessible without any token
PUBLIC_PATHS: set[str] = {"/login", "/login-page",
                          "/auth/callback", "/auth", "/logout"}

# ─────────────────────────────────────────────────────────────
# Mode 1 - shared env token
# ─────────────────────────────────────────────────────────────
_cached_env_token: Dict[str, Any] = {}
_cached_env_token_exp: float = 0.0

async def tokens_from_env() -> Optional[Dict[str, Any]]:
    """Mint access_token from the shared REFRESH_TOKEN in env.  Returns None if unavailable."""
    global _cached_env_token, _cached_env_token_exp, ENV_REFRESH_TOKEN
    if not ENV_REFRESH_TOKEN or not CLIENT_ID or not TENANT:
        return None
    if _cached_env_token and time.time() < _cached_env_token_exp:
        return _cached_env_token

    # Confidential clients require client_secret in every token request
    client_secret = _get_client_secret()
    oauth_kwargs: Dict[str, Any] = dict(client_id=CLIENT_ID, scope=SCOPES)
    if client_secret:
        oauth_kwargs["client_secret"] = client_secret

    async with AsyncOAuth2Client(**oauth_kwargs) as cli:
        token = await cli.fetch_token(
            TOKEN_URL,
            grant_type="refresh_token",
            refresh_token=ENV_REFRESH_TOKEN,
            scope=" ".join(SCOPES),
        )
    # Rotate the refresh token if Azure AD issued a new one (#15)
    new_rt = token.get("refresh_token") or ENV_REFRESH_TOKEN
    if new_rt != ENV_REFRESH_TOKEN:
        ENV_REFRESH_TOKEN = new_rt
        try:
            from .instances import get_active
            active = get_active()
            if active.refresh_token:
                active.refresh_token = new_rt
        except Exception:
            pass
        log.info("Env refresh token rotated")
    result = {
        "access_token": token.get("access_token"),
        "refresh_token": new_rt,
        "expires_in": token.get("expires_in"),
        "id_token": token.get("id_token", ""),
    }
    _cached_env_token = result
    _cached_env_token_exp = time.time() + max(int(token.get("expires_in", 3600)) - 60, 60)
    return result


# ─────────────────────────────────────────────────────────────
# Mode 2 - per-user PKCE login (Authorization Code + PKCE)
# ─────────────────────────────────────────────────────────────

def _get_client_secret() -> str:
    """Return the active instance's client_secret (empty string if none)."""
    try:
        from .instances import get_active
        return get_active().client_secret or ""
    except Exception:
        return ""


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

    # Allow PKCE login even for client_credentials instances, so users can
    # fall back to their own Microsoft account when the service-principal
    # token fails (e.g. expired secret).  The app registration just needs a
    # redirect URI configured for this to work.

    redirect_uri = _build_redirect_uri(request)
    code_verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(32)
    request.session["pkce_verifier"] = code_verifier
    request.session["pkce_state"] = state
    request.session["redirect_uri"] = redirect_uri

    # Ensure offline_access and openid are present.
    # offline_access → Azure AD returns a refresh token (#16)
    # openid → Azure AD returns an id_token with oid/upn (required for session)
    login_scopes = list(SCOPES)
    if "offline_access" not in login_scopes:
        login_scopes.append("offline_access")
        log.warning("offline_access missing from SCOPES - added for PKCE login")
    if "openid" not in login_scopes:
        login_scopes.append("openid")
        log.warning("openid missing from SCOPES - added for PKCE login")

    client_secret = _get_client_secret()
    oauth_kwargs: Dict[str, Any] = dict(
        client_id=CLIENT_ID,
        scope=" ".join(login_scopes),
        redirect_uri=redirect_uri,
        code_challenge_method="S256",
    )
    if client_secret:
        oauth_kwargs["client_secret"] = client_secret

    async with AsyncOAuth2Client(**oauth_kwargs) as cli:
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
        return JSONResponse({"error": "State mismatch - possible CSRF"}, status_code=400)

    code_verifier = request.session.get("pkce_verifier", "")
    redirect_uri = request.session.get("redirect_uri", _build_redirect_uri(request))

    # Confidential clients (those with a client_secret) require it in the
    # token exchange - otherwise Azure AD returns AADSTS7000218.
    client_secret = _get_client_secret()
    oauth_kwargs: Dict[str, Any] = dict(
        client_id=CLIENT_ID,
        scope=" ".join(SCOPES),
        redirect_uri=redirect_uri,
        code_challenge_method="S256",
    )
    if client_secret:
        oauth_kwargs["client_secret"] = client_secret

    async with AsyncOAuth2Client(**oauth_kwargs) as cli:
        try:
            token = await cli.fetch_token(
                TOKEN_URL,
                code=code,
                code_verifier=code_verifier,
            )
        except Exception as exc:
            log.error("PKCE token exchange failed: %s", exc)
            return JSONResponse({"error": f"Token exchange failed: {exc}"}, status_code=502)

    # Clean up PKCE state
    request.session.pop("pkce_verifier", None)
    request.session.pop("pkce_state", None)
    request.session.pop("redirect_uri", None)

    # ── Extract identity from token response ──────────────────────────
    rt = token.get("refresh_token", "")
    at = token.get("access_token", "")
    exp = time.time() + int(token.get("expires_in", 3600)) - 60

    # Try id_token first (preferred, returned when scope includes 'openid')
    id_token_raw = token.get("id_token", "")
    claims = decode_id_token_payload(id_token_raw) if id_token_raw else {}
    oid = claims.get("oid", "")
    upn = claims.get("preferred_username") or claims.get("upn") or claims.get("email", "")

    if not oid:
        # Fallback: Azure AD access tokens are also JWTs containing oid/upn.
        # This covers cases where the token endpoint omits id_token
        # (e.g. .default scope, certain confidential-client configurations).
        at_claims = decode_id_token_payload(at) if at else {}
        oid = at_claims.get("oid", "")
        upn = upn or at_claims.get("preferred_username") or at_claims.get("upn") or at_claims.get("email", "")
        if not oid:
            log.warning("PKCE callback: no oid found in id_token or access_token")

    # ── Persist session & tokens ──────────────────────────────────────
    from .instances import get_active_name
    inst_name = get_active_name()
    request.session["oid"] = oid
    request.session["instance_name"] = inst_name

    # Tokens stored server-side only (encrypted in SQLite, AT cached in-memory)
    if oid and at:
        _ts_set_cached_at(oid, inst_name, at, exp)
    if oid and rt:
        _ts_upsert(oid, inst_name, rt, upn)
        log.info("PKCE login OK: oid=%s… inst=%s (AT + RT)", oid[:8], inst_name)
    elif oid and at:
        log.info("PKCE login OK: oid=%s… inst=%s (AT only)", oid[:8], inst_name)
    else:
        log.warning("PKCE login: no oid — session will not authenticate")
    response = RedirectResponse("/")
    response.set_cookie("ores_user", "1", max_age=30 * 24 * 3600,
                        samesite="lax", httponly=False)
    return response


@router.get("/logout")
async def logout(request: Request):
    """Clear session, remove persisted token, and redirect to home."""
    oid = request.session.get("oid", "")
    instance_name = request.session.get("instance_name", "")
    if oid:
        _ts_delete(oid, instance_name)
    request.session.clear()
    response = RedirectResponse("/login-page")
    response.delete_cookie("ores_user")
    return response


async def tokens_from_session(request: Request) -> Optional[Dict[str, Any]]:
    """Return access token using server-side stores only.

    The session cookie contains **only** the user's ``oid`` and the
    ``instance_name`` they logged in against.  All sensitive tokens live
    server-side (encrypted in SQLite + in-memory AT cache).

    Recovery order:
      1. In-memory AT cache still valid → return immediately.
      2. SQLite RT → mint a new AT, cache it, return.
      3. Nothing available → return None (caller redirects to /login).
    """
    oid = request.session.get("oid", "")
    instance_name = request.session.get("instance_name", "")
    if not oid:
        return None

    # 1. In-memory AT cache
    cached_at = _ts_get_cached_at(oid, instance_name)
    if cached_at:
        return {"access_token": cached_at}

    # 2. Fetch RT from SQLite
    rt = _ts_fetch(oid, instance_name)
    if not rt:
        log.debug("No stored RT for oid=%s inst=%s", oid[:8], instance_name)
        return None

    # Resolve instance config for the correct token endpoint (#5)
    try:
        from .instances import get_instances, get_active
        instances = get_instances()
        inst = instances.get(instance_name) or get_active()
        client_id = inst.client_id
        scopes = (inst.scope or "openid offline_access").split()
        token_url = inst.token_url
    except Exception:
        client_id = CLIENT_ID
        scopes = SCOPES
        token_url = TOKEN_URL

    try:
        async with AsyncOAuth2Client(client_id=client_id, scope=scopes) as cli:
            token = await cli.fetch_token(
                token_url,
                grant_type="refresh_token",
                refresh_token=rt,
                scope=" ".join(scopes),
            )
        new_rt = token.get("refresh_token") or rt
        new_at = token.get("access_token", "")
        new_exp = time.time() + int(token.get("expires_in", 3600)) - 60

        # Update server-side stores
        _ts_set_cached_at(oid, instance_name, new_at, new_exp)
        if new_rt != rt:
            _ts_upsert(oid, instance_name, new_rt)

        log.info("Minted AT from stored RT for oid=%s inst=%s", oid[:8], instance_name)
        return {"access_token": new_at}
    except Exception as e:
        log.warning("Session refresh failed: %s - redirecting to login", e)
        return None


# ─────────────────────────────────────────────────────────────
# SMDA token  (separate audience - uses session SMDA refresh token)
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

    log.debug("No SMDA token available - run 'az login'")
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
        log.debug("az CLI not found - skipping az token strategy")
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
    logged_in = bool(request.session.get("oid")) if hasattr(request, "session") else False
    oid = request.session.get("oid", "") if hasattr(request, "session") else ""
    inst = request.session.get("instance_name", "") if hasattr(request, "session") else ""
    has_cached_at = False
    if oid and inst:
        has_cached_at = _ts_get_cached_at(oid, inst) is not None
    return {
        "azure_tenant": TENANT[:8] + "..." if TENANT else "",
        "client_id": CLIENT_ID[:8] + "..." if CLIENT_ID else "",
        "scopes": SCOPES,
        "mode": AUTH_MODE,
        "env_token_available": bool(ENV_REFRESH_TOKEN),
        "smda_api_id": SMDA_CLIENT_ID[:8] + "..." if SMDA_CLIENT_ID else "",
        "session_logged_in": logged_in,
        "session_oid": oid[:8] + "…" if oid else "",
        "session_instance": inst,
        "has_cached_at": has_cached_at,
        "session_keys": sorted(request.session.keys()) if hasattr(request, "session") else [],
    }
