"""
tests/conftest.py — Shared fixtures for auth & tokenstore tests.

Provides:
  • An isolated temporary SQLite DB per test (no leftover state).
  • A pre-configured FastAPI TestClient with session middleware.
  • Helpers to forge fake JWT id_tokens and fake Azure AD responses.
  • Simulated user personas (Alice, Bob, Carol) with distinct OIDs.
"""
from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Dict, Any
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import pytest_asyncio


# ── Fake JWT helpers ─────────────────────────────────────────────────────────

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_id_token(claims: dict) -> str:
    """Build a fake JWT (header.payload.signature) — signature is 'fake'."""
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps(claims).encode())
    return f"{header}.{payload}.fakesig"


# ── User personas ────────────────────────────────────────────────────────────

USERS: Dict[str, Dict[str, Any]] = {
    "alice": {
        "oid": "aaaa-1111-aaaa-1111-aaaa",
        "upn": "alice@example.com",
        "name": "Alice A",
        "refresh_token": "rt-alice-secret-123",
        "access_token": "at-alice-fake-token",
    },
    "bob": {
        "oid": "bbbb-2222-bbbb-2222-bbbb",
        "upn": "bob@example.com",
        "name": "Bob B",
        "refresh_token": "rt-bob-secret-456",
        "access_token": "at-bob-fake-token",
    },
    "carol": {
        "oid": "cccc-3333-cccc-3333-cccc",
        "upn": "carol@example.com",
        "name": "Carol C",
        "refresh_token": "rt-carol-secret-789",
        "access_token": "at-carol-fake-token",
    },
}


def id_token_for(user_key: str) -> str:
    u = USERS[user_key]
    return make_id_token({
        "oid": u["oid"],
        "preferred_username": u["upn"],
        "name": u["name"],
        "exp": int(time.time()) + 3600,
    })


# ── Tokenstore isolation ────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolate_tokenstore(tmp_path: Path):
    """
    Force tokenstore to use a fresh SQLite DB in a temp directory per test.
    Also reset module-level caches so tests are fully independent.
    """
    db_file = str(tmp_path / "test_tokens.db")
    os.environ["TOKEN_DB_PATH"] = db_file
    os.environ["SECRET_KEY"] = "test-secret-key-for-unit-tests"

    # Reset module-level state in tokenstore
    import app.tokenstore as ts
    ts._conn = None
    ts._db_path = None
    ts._fernet = None
    ts._fernet_init = False
    ts._at_cache.clear()

    yield

    # Cleanup
    ts._conn = None
    ts._db_path = None
    ts._fernet = None
    ts._fernet_init = False
    ts._at_cache.clear()
    os.environ.pop("TOKEN_DB_PATH", None)


# ── Fake Azure AD token response ────────────────────────────────────────────

def fake_token_response(user_key: str, *, rotate_rt: bool = False) -> dict:
    """Build the JSON body that Azure AD /token would return."""
    u = USERS[user_key]
    return {
        "access_token": u["access_token"],
        "refresh_token": (u["refresh_token"] + "-rotated") if rotate_rt else u["refresh_token"],
        "expires_in": 3600,
        "id_token": id_token_for(user_key),
        "token_type": "Bearer",
    }


# ── FastAPI TestClient fixture ──────────────────────────────────────────────

@pytest.fixture()
def test_app():
    """
    Return a fresh FastAPI app configured for testing with a mock instance.
    Patches instance loading so no real env vars are needed.
    """
    from app.instances import OsduInstance

    mock_instance = OsduInstance(
        name="testinst",
        hostname="https://test.osdu.example.com",
        data_partition_id="testpart",
        tenant_id="fake-tenant-id-0000",
        client_id="fake-client-id-1111",
        scope="openid offline_access fake-scope/.default",
        refresh_token="",  # No env-token by default → forces per_user_pkce path
        auth_mode="none",
    )

    with patch("app.instances.get_active", return_value=mock_instance), \
         patch("app.instances.get_active_name", return_value="testinst"), \
         patch("app.instances.get_instances", return_value={"testinst": mock_instance}), \
         patch("app.main.get_active", return_value=mock_instance), \
         patch("app.main.get_active_name", return_value="testinst"), \
         patch("app.main.get_instances", return_value={"testinst": mock_instance}):

        # Reload to pick up patched instances
        import app.auth as auth_mod
        auth_mod.TENANT = mock_instance.tenant_id
        auth_mod.CLIENT_ID = mock_instance.client_id
        auth_mod.SCOPES = mock_instance.scope.split()
        auth_mod.AUTH_BASE = f"https://login.microsoftonline.com/{mock_instance.tenant_id}/oauth2/v2.0"
        auth_mod.AUTHORIZE_URL = f"{auth_mod.AUTH_BASE}/authorize"
        auth_mod.TOKEN_URL = f"{auth_mod.AUTH_BASE}/token"
        auth_mod.ENV_REFRESH_TOKEN = None
        auth_mod.AUTH_MODE = "per_user_pkce"
        auth_mod._cached_env_token = {}
        auth_mod._cached_env_token_exp = 0.0

        from app.main import app as fastapi_app
        yield fastapi_app


@pytest.fixture()
def client(test_app):
    """HTTPX-based TestClient for the FastAPI app."""
    from starlette.testclient import TestClient
    with TestClient(test_app, base_url="http://testserver") as c:
        yield c


# ── Pre-authed app (instance token always available) ────────────────────────

@pytest.fixture()
def authed_app():
    """
    Return a FastAPI app where the auth middleware always succeeds.
    The mock OsduInstance.get_access_token() returns a fake token,
    so the middleware sets request.state.access_token on every request.
    """
    from app.instances import OsduInstance

    mock_instance = OsduInstance(
        name="testinst",
        hostname="test.osdu.example.com",
        data_partition_id="testpart",
        tenant_id="fake-tenant-id-0000",
        client_id="fake-client-id-1111",
        scope="openid offline_access fake-scope/.default",
        refresh_token="",
        auth_mode="none",
    )
    # Make get_access_token() always return a fake token
    mock_instance.get_access_token = AsyncMock(return_value="fake-at-for-route-tests")

    with patch("app.instances.get_active", return_value=mock_instance), \
         patch("app.instances.get_active_name", return_value="testinst"), \
         patch("app.instances.get_instances", return_value={"testinst": mock_instance}), \
         patch("app.main.get_active", return_value=mock_instance), \
         patch("app.main.get_active_name", return_value="testinst"), \
         patch("app.main.get_instances", return_value={"testinst": mock_instance}):

        import app.auth as auth_mod
        auth_mod.TENANT = mock_instance.tenant_id
        auth_mod.CLIENT_ID = mock_instance.client_id
        auth_mod.SCOPES = mock_instance.scope.split()
        auth_mod.AUTH_BASE = f"https://login.microsoftonline.com/{mock_instance.tenant_id}/oauth2/v2.0"
        auth_mod.AUTHORIZE_URL = f"{auth_mod.AUTH_BASE}/authorize"
        auth_mod.TOKEN_URL = f"{auth_mod.AUTH_BASE}/token"
        auth_mod.ENV_REFRESH_TOKEN = None
        auth_mod.AUTH_MODE = "per_user_pkce"
        auth_mod._cached_env_token = {}
        auth_mod._cached_env_token_exp = 0.0

        import app.osdu as osdu_mod
        osdu_mod.OSDU_BASE_URL = mock_instance.hostname
        osdu_mod.DATA_PARTITION_ID = mock_instance.data_partition_id

        from app.main import app as fastapi_app
        yield fastapi_app


@pytest.fixture()
def authed_client(authed_app):
    """TestClient with a pre-authed app — every request gets an access_token."""
    from starlette.testclient import TestClient
    with TestClient(authed_app, base_url="http://testserver") as c:
        yield c
