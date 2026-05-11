"""
tests/test_tokenstore.py - Unit tests for the per-user token store.

Covers:
  • CRUD operations (upsert, fetch, delete)
  • Fernet encryption at rest - tokens in DB are NOT plaintext
  • Multi-user isolation - Alice's token ≠ Bob's token
  • Multi-instance isolation - same user, different instances
  • Access-token in-memory cache behaviour
  • Encryption key rotation (SECRET_KEY change invalidates tokens)
  • Concurrent access from multiple threads
"""
from __future__ import annotations

import os
import threading
import time

import pytest

from app.tokenstore import (
    upsert,
    fetch,
    delete,
    get_cached_at,
    set_cached_at,
    clear_cached_at,
    decode_id_token_payload,
    save_query,
    list_queries,
    delete_query,
    _encrypt,
    _decrypt,
    _get_conn,
)
from test.conftest import USERS, make_id_token


# ─────────────────────────────────────────────────────────────────────────────
# Basic CRUD
# ─────────────────────────────────────────────────────────────────────────────

class TestTokenstoreCRUD:
    """Basic insert / read / update / delete."""

    def test_upsert_and_fetch(self):
        alice = USERS["alice"]
        upsert(alice["oid"], "inst1", alice["refresh_token"], alice["upn"])
        rt = fetch(alice["oid"], "inst1")
        assert rt == alice["refresh_token"]

    def test_fetch_missing_returns_none(self):
        assert fetch("nonexistent-oid", "inst1") is None

    def test_upsert_updates_existing(self):
        alice = USERS["alice"]
        upsert(alice["oid"], "inst1", "old-token", alice["upn"])
        upsert(alice["oid"], "inst1", "new-token", alice["upn"])
        assert fetch(alice["oid"], "inst1") == "new-token"

    def test_delete_removes_token(self):
        bob = USERS["bob"]
        upsert(bob["oid"], "inst1", bob["refresh_token"])
        delete(bob["oid"], "inst1")
        assert fetch(bob["oid"], "inst1") is None

    def test_delete_all_instances(self):
        carol = USERS["carol"]
        upsert(carol["oid"], "inst1", "rt-1")
        upsert(carol["oid"], "inst2", "rt-2")
        # Delete without specifying instance → removes all
        delete(carol["oid"])
        assert fetch(carol["oid"], "inst1") is None
        assert fetch(carol["oid"], "inst2") is None

    def test_upsert_noop_for_empty_oid(self):
        upsert("", "inst1", "some-token")
        # No crash, just a no-op

    def test_upsert_noop_for_empty_token(self):
        upsert("some-oid", "inst1", "")
        assert fetch("some-oid", "inst1") is None


# ─────────────────────────────────────────────────────────────────────────────
# Multi-user isolation
# ─────────────────────────────────────────────────────────────────────────────

class TestMultiUserIsolation:
    """Ensure tokens for different users do NOT leak across each other."""

    def test_alice_and_bob_separate(self):
        alice, bob = USERS["alice"], USERS["bob"]
        upsert(alice["oid"], "inst1", alice["refresh_token"], alice["upn"])
        upsert(bob["oid"], "inst1", bob["refresh_token"], bob["upn"])

        assert fetch(alice["oid"], "inst1") == alice["refresh_token"]
        assert fetch(bob["oid"], "inst1") == bob["refresh_token"]
        assert fetch(alice["oid"], "inst1") != fetch(bob["oid"], "inst1")

    def test_delete_one_user_preserves_other(self):
        alice, bob = USERS["alice"], USERS["bob"]
        upsert(alice["oid"], "inst1", alice["refresh_token"])
        upsert(bob["oid"], "inst1", bob["refresh_token"])

        delete(alice["oid"], "inst1")
        assert fetch(alice["oid"], "inst1") is None
        assert fetch(bob["oid"], "inst1") == bob["refresh_token"]

    def test_three_users_concurrent_writes(self):
        """Simulate 3 users writing tokens at the same time."""
        results = {}

        def _write(user_key):
            u = USERS[user_key]
            upsert(u["oid"], "inst1", u["refresh_token"], u["upn"])
            results[user_key] = fetch(u["oid"], "inst1")

        threads = [threading.Thread(target=_write, args=(k,)) for k in USERS]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for user_key, u in USERS.items():
            assert results[user_key] == u["refresh_token"]


# ─────────────────────────────────────────────────────────────────────────────
# Multi-instance isolation
# ─────────────────────────────────────────────────────────────────────────────

class TestMultiInstanceIsolation:
    """Same user on different OSDU instances gets independent tokens."""

    def test_same_user_different_instances(self):
        alice = USERS["alice"]
        upsert(alice["oid"], "dev", "rt-dev")
        upsert(alice["oid"], "prod", "rt-prod")

        assert fetch(alice["oid"], "dev") == "rt-dev"
        assert fetch(alice["oid"], "prod") == "rt-prod"

    def test_delete_one_instance_preserves_other(self):
        alice = USERS["alice"]
        upsert(alice["oid"], "dev", "rt-dev")
        upsert(alice["oid"], "prod", "rt-prod")
        delete(alice["oid"], "dev")

        assert fetch(alice["oid"], "dev") is None
        assert fetch(alice["oid"], "prod") == "rt-prod"


# ─────────────────────────────────────────────────────────────────────────────
# Encryption at rest
# ─────────────────────────────────────────────────────────────────────────────

class TestEncryption:
    """Verify tokens are encrypted in the DB but decrypted on read."""

    def test_stored_value_is_encrypted(self):
        alice = USERS["alice"]
        upsert(alice["oid"], "inst1", alice["refresh_token"])

        # Read raw from DB - should NOT be plaintext
        conn = _get_conn()
        row = conn.execute(
            "SELECT refresh_token_enc FROM sessions WHERE oid = ?",
            (alice["oid"],),
        ).fetchone()
        assert row is not None
        raw_enc = row[0]
        assert raw_enc != alice["refresh_token"], "Token stored in plaintext!"

    def test_decrypt_roundtrip(self):
        plaintext = "my-secret-refresh-token"
        encrypted = _encrypt(plaintext)
        assert encrypted != plaintext
        assert _decrypt(encrypted) == plaintext

    def test_key_change_invalidates_tokens(self, tmp_path):
        """If SECRET_KEY changes, existing encrypted tokens can't be decrypted."""
        alice = USERS["alice"]
        upsert(alice["oid"], "inst1", alice["refresh_token"])

        # Verify readable with current key
        assert fetch(alice["oid"], "inst1") == alice["refresh_token"]

        # Change SECRET_KEY and reset fernet
        import app.tokenstore as ts
        os.environ["SECRET_KEY"] = "totally-different-key-now"
        ts._fernet = None
        ts._fernet_init = False

        # Now fetch should return None (decryption fails gracefully)
        assert fetch(alice["oid"], "inst1") is None


# ─────────────────────────────────────────────────────────────────────────────
# In-memory access-token cache
# ─────────────────────────────────────────────────────────────────────────────

class TestAccessTokenCache:
    """In-memory AT cache (never persisted)."""

    def test_set_and_get(self):
        set_cached_at("oid1", "inst1", "at-123", time.time() + 3600)
        assert get_cached_at("oid1", "inst1") == "at-123"

    def test_expired_returns_none(self):
        set_cached_at("oid1", "inst1", "at-old", time.time() - 1)
        assert get_cached_at("oid1", "inst1") is None

    def test_clear_cached_at(self):
        set_cached_at("oid1", "inst1", "at-123", time.time() + 3600)
        clear_cached_at("oid1", "inst1")
        assert get_cached_at("oid1", "inst1") is None

    def test_different_users_different_cache(self):
        set_cached_at("alice-oid", "inst1", "at-alice", time.time() + 3600)
        set_cached_at("bob-oid", "inst1", "at-bob", time.time() + 3600)
        assert get_cached_at("alice-oid", "inst1") == "at-alice"
        assert get_cached_at("bob-oid", "inst1") == "at-bob"

    def test_same_user_different_instances(self):
        set_cached_at("oid1", "dev", "at-dev", time.time() + 3600)
        set_cached_at("oid1", "prod", "at-prod", time.time() + 3600)
        assert get_cached_at("oid1", "dev") == "at-dev"
        assert get_cached_at("oid1", "prod") == "at-prod"


# ─────────────────────────────────────────────────────────────────────────────
# JWT decode helper
# ─────────────────────────────────────────────────────────────────────────────

class TestDecodeIdToken:

    def test_decode_valid_token(self):
        token = make_id_token({"oid": "test-oid", "preferred_username": "user@x.com"})
        claims = decode_id_token_payload(token)
        assert claims["oid"] == "test-oid"
        assert claims["preferred_username"] == "user@x.com"

    def test_decode_invalid_token(self):
        claims = decode_id_token_payload("not-a-jwt")
        assert claims == {}

    def test_decode_empty_string(self):
        assert decode_id_token_payload("") == {}


# ─────────────────────────────────────────────────────────────────────────────
# Saved queries
# ─────────────────────────────────────────────────────────────────────────────

class TestSavedQueries:
    """CRUD for the saved_queries table."""

    def test_save_and_list(self):
        alice = USERS["alice"]
        row_id = save_query(alice["oid"], "inst1", "My BD query",
                            "osdu:wks:master-data--BusinessDecision:*",
                            'data.Name:"*Drogon*"')
        assert row_id is not None
        queries = list_queries(alice["oid"], "inst1")
        assert any(q["id"] == row_id for q in queries)
        match = [q for q in queries if q["id"] == row_id][0]
        assert match["name"] == "My BD query"
        assert match["kind"] == "osdu:wks:master-data--BusinessDecision:*"
        assert match["query"] == 'data.Name:"*Drogon*"'

    def test_list_empty(self):
        queries = list_queries("nonexistent-oid", "inst1")
        assert queries == []

    def test_list_ordered_newest_first(self):
        bob = USERS["bob"]
        id1 = save_query(bob["oid"], "inst1", "First", "kind1", "q1")
        id2 = save_query(bob["oid"], "inst1", "Second", "kind2", "q2")
        queries = list_queries(bob["oid"], "inst1")
        names = [q["name"] for q in queries]
        assert names.index("Second") < names.index("First")

    def test_delete_query(self):
        alice = USERS["alice"]
        row_id = save_query(alice["oid"], "inst1", "To delete", "k", "q")
        assert delete_query(row_id, oid=alice["oid"]) is True
        queries = list_queries(alice["oid"], "inst1")
        assert not any(q["id"] == row_id for q in queries)

    def test_delete_enforces_ownership(self):
        alice = USERS["alice"]
        bob = USERS["bob"]
        row_id = save_query(alice["oid"], "inst1", "Alice only", "k", "q")
        # Bob tries to delete Alice's query
        delete_query(row_id, oid=bob["oid"])
        # Alice's query should still exist
        queries = list_queries(alice["oid"], "inst1")
        assert any(q["id"] == row_id for q in queries)

    def test_isolation_by_instance(self):
        carol = USERS["carol"]
        save_query(carol["oid"], "inst1", "Inst1 query", "k", "q")
        save_query(carol["oid"], "inst2", "Inst2 query", "k", "q")
        q1 = list_queries(carol["oid"], "inst1")
        q2 = list_queries(carol["oid"], "inst2")
        assert all(q["name"] != "Inst2 query" for q in q1)
        assert all(q["name"] != "Inst1 query" for q in q2)

    def test_graphql_kind_marker(self):
        """GraphQL queries use kind='__graphql__' and can be filtered."""
        alice = USERS["alice"]
        save_query(alice["oid"], "gql-test", "Search BD", "osdu:wks:BD:*", "q")
        save_query(alice["oid"], "gql-test", "My GQL", "__graphql__", "{ status }")
        all_q = list_queries(alice["oid"], "gql-test")
        search_q = [q for q in all_q if q["kind"] != "__graphql__"]
        graphql_q = [q for q in all_q if q["kind"] == "__graphql__"]
        assert len(search_q) == 1
        assert search_q[0]["name"] == "Search BD"
        assert len(graphql_q) == 1
        assert graphql_q[0]["name"] == "My GQL"
        assert graphql_q[0]["query"] == "{ status }"
