"""
tests/test_multiuser.py — Multi-user simulation tests.

Simulates multiple users (Alice, Bob, Carol) operating concurrently
against the SAME server, each with their own session and tokenstore entries.

This is the key test file that answers:
  "Can we mimic multiple users locally?"

It does so by:
  1. Running 3 simulated PKCE flows (one per user) against a shared app
  2. Verifying each user's tokens are isolated in the DB
  3. Verifying the AT cache holds separate entries per user
  4. Testing that one user logging out doesn't affect others
  5. Testing instance switching while multiple users are logged in
"""
from __future__ import annotations

import time
import threading
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from app.tokenstore import (
    upsert,
    fetch,
    delete,
    get_cached_at,
    set_cached_at,
    clear_cached_at,
)
from tests.conftest import USERS, fake_token_response


# ─────────────────────────────────────────────────────────────────────────────
# Simulate multiple users with direct tokenstore operations
# ─────────────────────────────────────────────────────────────────────────────

class TestMultiUserDirect:
    """Test multi-user scenarios by directly driving tokenstore + auth functions."""

    def test_three_users_same_instance(self):
        """Alice, Bob, Carol all log into the same instance."""
        for key, u in USERS.items():
            upsert(u["oid"], "shared-inst", u["refresh_token"], u["upn"])

        # Each user's RT is independently stored
        for key, u in USERS.items():
            assert fetch(u["oid"], "shared-inst") == u["refresh_token"]

    def test_three_users_different_instances(self):
        """Each user on a different OSDU instance."""
        assignments = [("alice", "dev"), ("bob", "staging"), ("carol", "prod")]
        for user_key, inst in assignments:
            u = USERS[user_key]
            upsert(u["oid"], inst, u["refresh_token"], u["upn"])

        for user_key, inst in assignments:
            u = USERS[user_key]
            assert fetch(u["oid"], inst) == u["refresh_token"]
            # No cross-contamination: alice on staging should be None
            other_insts = [i for _, i in assignments if i != inst]
            for oi in other_insts:
                assert fetch(u["oid"], oi) is None

    def test_user_switches_instance(self):
        """One user has tokens on two instances, fetches each independently."""
        alice = USERS["alice"]
        upsert(alice["oid"], "dev", "rt-alice-dev")
        upsert(alice["oid"], "prod", "rt-alice-prod")

        assert fetch(alice["oid"], "dev") == "rt-alice-dev"
        assert fetch(alice["oid"], "prod") == "rt-alice-prod"

        # Update dev token (re-login or rotation)
        upsert(alice["oid"], "dev", "rt-alice-dev-rotated")
        assert fetch(alice["oid"], "dev") == "rt-alice-dev-rotated"
        assert fetch(alice["oid"], "prod") == "rt-alice-prod"  # unaffected

    def test_one_user_logout_preserves_others(self):
        """Bob logs out; Alice and Carol remain."""
        for key, u in USERS.items():
            upsert(u["oid"], "inst1", u["refresh_token"], u["upn"])
            set_cached_at(u["oid"], "inst1", u["access_token"], time.time() + 3600)

        # Bob logs out
        bob = USERS["bob"]
        delete(bob["oid"], "inst1")
        clear_cached_at(bob["oid"], "inst1")

        assert fetch(bob["oid"], "inst1") is None
        assert get_cached_at(bob["oid"], "inst1") is None

        # Alice & Carol unaffected
        for key in ("alice", "carol"):
            u = USERS[key]
            assert fetch(u["oid"], "inst1") == u["refresh_token"]
            assert get_cached_at(u["oid"], "inst1") == u["access_token"]

    def test_at_cache_per_user_expiry(self):
        """Each user's AT cache expires independently."""
        alice, bob = USERS["alice"], USERS["bob"]

        set_cached_at(alice["oid"], "inst1", "at-alice", time.time() - 1)    # expired
        set_cached_at(bob["oid"], "inst1", "at-bob", time.time() + 3600)     # valid

        assert get_cached_at(alice["oid"], "inst1") is None   # expired
        assert get_cached_at(bob["oid"], "inst1") == "at-bob" # still valid


# ─────────────────────────────────────────────────────────────────────────────
# Simulate concurrent sessions via tokens_from_session
# ─────────────────────────────────────────────────────────────────────────────

class TestMultiUserSessionRecovery:
    """
    Simulate multiple users each with their own session cookie (oid + instance_name),
    recovering ATs from stored RTs via tokens_from_session().
    """

    def test_concurrent_session_recovery(self, test_app):
        """3 users with RTs in DB → each gets their own AT minted."""
        import asyncio
        import app.auth as auth_mod

        # Pre-populate the tokenstore
        for key, u in USERS.items():
            upsert(u["oid"], "testinst", u["refresh_token"], u["upn"])

        results = {}

        async def _recover(user_key):
            u = USERS[user_key]
            mock_request = MagicMock()
            mock_request.session = {"oid": u["oid"], "instance_name": "testinst"}

            fake_token = fake_token_response(user_key)

            with patch("app.auth.AsyncOAuth2Client") as MockClient:
                mock_ctx = AsyncMock()
                mock_ctx.fetch_token = AsyncMock(return_value=fake_token)
                MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
                MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

                result = await auth_mod.tokens_from_session(mock_request)
                results[user_key] = result

        loop = asyncio.get_event_loop()
        for key in USERS:
            loop.run_until_complete(_recover(key))

        # Each user got their own AT
        for key, u in USERS.items():
            assert results[key] is not None
            assert results[key]["access_token"] == u["access_token"]

    def test_at_cache_populated_after_recovery(self, test_app):
        """After session recovery, the AT cache is populated (no repeated DB/Azure hits)."""
        import asyncio
        import app.auth as auth_mod

        alice = USERS["alice"]
        upsert(alice["oid"], "testinst", alice["refresh_token"])

        mock_request = MagicMock()
        mock_request.session = {"oid": alice["oid"], "instance_name": "testinst"}

        fake_token = fake_token_response("alice")
        with patch("app.auth.AsyncOAuth2Client") as MockClient:
            mock_ctx = AsyncMock()
            mock_ctx.fetch_token = AsyncMock(return_value=fake_token)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            loop = asyncio.get_event_loop()
            loop.run_until_complete(auth_mod.tokens_from_session(mock_request))

        # Now AT cache should be warm
        cached = get_cached_at(alice["oid"], "testinst")
        assert cached == alice["access_token"]

        # Second call should use cache (no mocking needed)
        result2 = asyncio.get_event_loop().run_until_complete(
            auth_mod.tokens_from_session(mock_request)
        )
        assert result2 == {"access_token": alice["access_token"]}


# ─────────────────────────────────────────────────────────────────────────────
# Token rotation during multi-user scenario
# ─────────────────────────────────────────────────────────────────────────────

class TestMultiUserTokenRotation:
    """When Azure rotates an RT, only that user's stored token updates."""

    def test_rotation_only_affects_target_user(self, test_app):
        """Bob's RT gets rotated; Alice's stays the same."""
        import asyncio
        import app.auth as auth_mod

        alice, bob = USERS["alice"], USERS["bob"]
        upsert(alice["oid"], "testinst", alice["refresh_token"])
        upsert(bob["oid"], "testinst", bob["refresh_token"])

        # Bob gets a rotated RT from Azure
        bob_rotated_response = fake_token_response("bob", rotate_rt=True)

        mock_request = MagicMock()
        mock_request.session = {"oid": bob["oid"], "instance_name": "testinst"}

        with patch("app.auth.AsyncOAuth2Client") as MockClient:
            mock_ctx = AsyncMock()
            mock_ctx.fetch_token = AsyncMock(return_value=bob_rotated_response)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            loop = asyncio.get_event_loop()
            loop.run_until_complete(auth_mod.tokens_from_session(mock_request))

        # Bob's stored RT was updated
        bob_stored = fetch(bob["oid"], "testinst")
        assert bob_stored == bob["refresh_token"] + "-rotated"

        # Alice's RT is unchanged
        alice_stored = fetch(alice["oid"], "testinst")
        assert alice_stored == alice["refresh_token"]


# ─────────────────────────────────────────────────────────────────────────────
# Threaded stress test
# ─────────────────────────────────────────────────────────────────────────────

class TestConcurrentStress:
    """Hammer the tokenstore from multiple threads simultaneously."""

    def test_concurrent_upsert_fetch_delete(self):
        """50 threads doing mixed operations should not corrupt data."""
        import random

        errors = []

        def _worker(worker_id):
            try:
                oid = f"worker-{worker_id}"
                inst = f"inst-{worker_id % 3}"
                rt = f"rt-{worker_id}-{random.randint(0, 9999)}"

                upsert(oid, inst, rt, f"user{worker_id}@test.com")
                fetched = fetch(oid, inst)
                if fetched != rt:
                    errors.append(f"Worker {worker_id}: expected {rt}, got {fetched}")

                # Half the workers also delete
                if worker_id % 2 == 0:
                    delete(oid, inst)
                    fetched_after = fetch(oid, inst)
                    if fetched_after is not None:
                        errors.append(f"Worker {worker_id}: expected None after delete")
            except Exception as e:
                errors.append(f"Worker {worker_id}: exception: {e}")

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Concurrent errors: {errors}"
