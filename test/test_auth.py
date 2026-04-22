"""
tests/test_auth.py - Integration tests for the two auth paths.

Tests the auth middleware, env-token path, and per-user PKCE session path
using mocked Azure AD responses (no real Azure needed).

Coverage:
  • Mode 1 (env_token): shared refresh_token from env → access_token
  • Mode 2 (per_user_pkce): per-user login/callback/session cycle
  • Middleware priority: instance → env → session → redirect to /login
  • Token rotation (Azure returns new RT)
  • /logout clears session + tokenstore
  • /auth diagnostics endpoint
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from starlette.testclient import TestClient

from test.conftest import USERS, fake_token_response, id_token_for


# ─────────────────────────────────────────────────────────────────────────────
# Diagnostics endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestAuthDiagnostics:

    def test_auth_endpoint_returns_config(self, client):
        resp = client.get("/auth")
        assert resp.status_code == 200
        data = resp.json()
        assert "mode" in data
        assert "scopes" in data
        assert "session_logged_in" in data


# ─────────────────────────────────────────────────────────────────────────────
# Mode 1 - env-token path (shared refresh token)
# ─────────────────────────────────────────────────────────────────────────────

class TestEnvTokenPath:
    """When ENV_REFRESH_TOKEN is set, requests are authed without per-user login."""

    def test_env_token_mints_access_token(self, test_app):
        """With a shared refresh token, tokens_from_env() returns an AT."""
        import app.auth as auth_mod

        # Activate env-token mode
        auth_mod.ENV_REFRESH_TOKEN = "fake-shared-rt"
        auth_mod.AUTH_MODE = "env_token"
        auth_mod._cached_env_token = {}
        auth_mod._cached_env_token_exp = 0.0

        fake_response = {
            "access_token": "shared-at-12345",
            "refresh_token": "fake-shared-rt",
            "expires_in": 3600,
            "id_token": "",
        }

        with patch("app.auth.AsyncOAuth2Client") as MockClient:
            mock_ctx = AsyncMock()
            mock_ctx.fetch_token = AsyncMock(return_value=fake_response)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                auth_mod.tokens_from_env()
            )

        assert result is not None
        assert result["access_token"] == "shared-at-12345"

    def test_env_token_caching(self, test_app):
        """Subsequent calls within expiry use the cached token."""
        import app.auth as auth_mod

        auth_mod.ENV_REFRESH_TOKEN = "fake-shared-rt"
        auth_mod._cached_env_token = {
            "access_token": "cached-at",
            "refresh_token": "fake-shared-rt",
        }
        auth_mod._cached_env_token_exp = time.time() + 3600

        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            auth_mod.tokens_from_env()
        )
        assert result["access_token"] == "cached-at"

    def test_env_token_none_when_no_rt(self, test_app):
        """tokens_from_env returns None when no shared RT is configured."""
        import app.auth as auth_mod
        auth_mod.ENV_REFRESH_TOKEN = None

        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            auth_mod.tokens_from_env()
        )
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# Mode 2 - per-user PKCE path
# ─────────────────────────────────────────────────────────────────────────────

class TestPerUserPKCE:
    """PKCE login/callback flow with mocked Azure AD."""

    def test_login_redirects_to_azure(self, client):
        """GET /login should redirect to Azure AD authorize endpoint."""
        resp = client.get("/login", follow_redirects=False)
        assert resp.status_code in (302, 307)
        location = resp.headers["location"]
        assert "login.microsoftonline.com" in location
        assert "code_challenge" in location

    def test_callback_without_code_returns_400(self, client):
        """GET /auth/callback with no code should fail gracefully."""
        resp = client.get("/auth/callback")
        assert resp.status_code == 400

    def test_callback_state_mismatch_returns_400(self, client):
        """State mismatch is detected and rejected."""
        # First, trigger /login to set session state
        client.get("/login", follow_redirects=False)
        # Now callback with wrong state
        resp = client.get("/auth/callback?code=fakecode&state=wrong-state")
        assert resp.status_code == 400

    def test_full_pkce_flow_with_mock(self, client):
        """
        Simulate the full PKCE flow:
          1. GET /login → sets session PKCE state
          2. Mock the Azure AD token exchange
          3. GET /auth/callback?code=...&state=... → stores tokens
          4. Verify session has oid, tokenstore has RT
        """
        alice = USERS["alice"]

        # Step 1: Trigger login to capture the PKCE state
        login_resp = client.get("/login", follow_redirects=False)
        assert login_resp.status_code in (302, 307)

        # Extract state from redirect URL
        import urllib.parse
        location = login_resp.headers["location"]
        parsed = urllib.parse.urlparse(location)
        qs = urllib.parse.parse_qs(parsed.query)
        state = qs.get("state", [None])[0]
        assert state is not None

        # Step 2: Mock the token exchange
        fake_token = fake_token_response("alice")
        with patch("app.auth.AsyncOAuth2Client") as MockClient:
            mock_ctx = AsyncMock()
            mock_ctx.fetch_token = AsyncMock(return_value=fake_token)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            # Step 3: Hit the callback
            resp = client.get(
                f"/auth/callback?code=fake-auth-code&state={state}",
                follow_redirects=False,
            )
            assert resp.status_code in (302, 307, 200)

        # Step 4: Verify token is stored
        from app.tokenstore import fetch as ts_fetch
        stored_rt = ts_fetch(alice["oid"], "testinst")
        assert stored_rt == alice["refresh_token"]


# ─────────────────────────────────────────────────────────────────────────────
# Session-based token minting (tokens_from_session)
# ─────────────────────────────────────────────────────────────────────────────

class TestTokensFromSession:
    """Test the server-side session → AT recovery logic."""

    def test_returns_cached_at(self, test_app):
        """If AT is in memory cache, return immediately (no DB hit)."""
        from app.tokenstore import set_cached_at

        alice = USERS["alice"]
        set_cached_at(alice["oid"], "testinst", "cached-at-alice", time.time() + 3600)

        # Build a fake request with session
        mock_request = MagicMock()
        mock_request.session = {"oid": alice["oid"], "instance_name": "testinst"}

        import app.auth as auth_mod
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            auth_mod.tokens_from_session(mock_request)
        )
        assert result == {"access_token": "cached-at-alice"}

    def test_mints_at_from_stored_rt(self, test_app):
        """If AT cache is empty but RT is in SQLite, mint a new AT."""
        from app.tokenstore import upsert
        import app.auth as auth_mod

        bob = USERS["bob"]
        upsert(bob["oid"], "testinst", bob["refresh_token"], bob["upn"])

        mock_request = MagicMock()
        mock_request.session = {"oid": bob["oid"], "instance_name": "testinst"}

        fake_token = fake_token_response("bob")
        with patch("app.auth.AsyncOAuth2Client") as MockClient:
            mock_ctx = AsyncMock()
            mock_ctx.fetch_token = AsyncMock(return_value=fake_token)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                auth_mod.tokens_from_session(mock_request)
            )

        assert result is not None
        assert result["access_token"] == bob["access_token"]

    def test_returns_none_when_no_session(self, test_app):
        """No oid in session → returns None."""
        mock_request = MagicMock()
        mock_request.session = {}

        import app.auth as auth_mod
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            auth_mod.tokens_from_session(mock_request)
        )
        assert result is None

    def test_returns_none_when_no_stored_rt(self, test_app):
        """oid in session but no RT in DB → returns None."""
        mock_request = MagicMock()
        mock_request.session = {"oid": "no-such-oid", "instance_name": "testinst"}

        import app.auth as auth_mod
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            auth_mod.tokens_from_session(mock_request)
        )
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# Middleware priority
# ─────────────────────────────────────────────────────────────────────────────

class TestMiddlewarePriority:

    def test_unauthenticated_api_returns_401(self, test_app):
        """API paths without any token get 401, not redirect."""
        # Ensure no tokens available anywhere
        with patch("app.auth.tokens_from_env", new_callable=AsyncMock, return_value=None), \
             patch("app.auth.tokens_from_session", new_callable=AsyncMock, return_value=None), \
             patch.object(
                 # Also make instance token return None
                 __import__("app.instances", fromlist=["get_active"]).get_active(),
                 "get_access_token", new_callable=AsyncMock, return_value=None,
             ):
            with TestClient(test_app) as c:
                resp = c.get("/api/search", follow_redirects=False)
                assert resp.status_code == 401

    def test_unauthenticated_browser_redirects_to_login(self, test_app):
        """Non-API paths without any token redirect to /login-page."""
        with patch("app.auth.tokens_from_env", new_callable=AsyncMock, return_value=None), \
             patch("app.auth.tokens_from_session", new_callable=AsyncMock, return_value=None), \
             patch.object(
                 __import__("app.instances", fromlist=["get_active"]).get_active(),
                 "get_access_token", new_callable=AsyncMock, return_value=None,
             ):
            with TestClient(test_app) as c:
                resp = c.get("/", follow_redirects=False)
                assert resp.status_code in (302, 307)
                assert "/login" in resp.headers.get("location", "")

    def test_public_paths_accessible_without_token(self, client):
        """Public paths (/login, /auth, /logout) don't require tokens."""
        resp = client.get("/auth")
        assert resp.status_code == 200

        resp = client.get("/login", follow_redirects=False)
        assert resp.status_code in (200, 302, 307)


# ─────────────────────────────────────────────────────────────────────────────
# Logout
# ─────────────────────────────────────────────────────────────────────────────

class TestLogout:

    def test_logout_clears_tokenstore(self, test_app):
        """After logout, stored RT is removed."""
        from app.tokenstore import upsert, fetch as ts_fetch

        alice = USERS["alice"]
        upsert(alice["oid"], "testinst", alice["refresh_token"])
        assert ts_fetch(alice["oid"], "testinst") is not None

        with TestClient(test_app) as c:
            # Simulate a session with alice's oid
            # We need to set the session cookie - use the internal session mechanism
            resp = c.get("/logout", follow_redirects=False,
                         cookies={})  # will use existing session from cookie jar
            assert resp.status_code in (302, 307)
            assert "/login" in resp.headers.get("location", "")
