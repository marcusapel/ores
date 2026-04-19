"""
app/tokenstore.py — Persistent per-user refresh-token store.

Tokens are persisted in a SQLite database so that users remain logged in
across server restarts.  The database path defaults to
``/data/ores_tokens.db`` (writable in k8s via a PVC) and falls back to
``./ores_tokens.db`` in the current working directory for local dev.

Schema
------
users(oid TEXT PRIMARY KEY, upn TEXT, refresh_token TEXT, updated_at REAL)

``oid`` is the Azure AD Object-ID extracted from the PKCE id_token JWT
payload — it is stable across re-logins and is never shown to the user.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("rddms-admin.tokenstore")

# ── DB path ──────────────────────────────────────────────────────────────────
_DEFAULT_PATHS = ["/data/ores_tokens.db", "./ores_tokens.db"]

def _resolve_db_path() -> Path:
    env = os.getenv("TOKEN_DB_PATH")
    if env:
        return Path(env)
    for p in _DEFAULT_PATHS:
        try:
            path = Path(p)
            path.parent.mkdir(parents=True, exist_ok=True)
            # quick write-test
            path.touch(exist_ok=True)
            return path
        except OSError:
            continue
    return Path("./ores_tokens.db")

DB_PATH: Path = _resolve_db_path()


# ── Schema init ──────────────────────────────────────────────────────────────
def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS users (
               oid          TEXT PRIMARY KEY,
               upn          TEXT,
               refresh_token TEXT NOT NULL,
               updated_at   REAL NOT NULL
           )"""
    )
    conn.commit()
    return conn


# ── Public API ────────────────────────────────────────────────────────────────
def upsert(oid: str, refresh_token: str, upn: str = "") -> None:
    """Persist (or update) the refresh token for a user identified by OID."""
    if not oid or not refresh_token:
        return
    try:
        with _get_conn() as conn:
            conn.execute(
                """INSERT INTO users (oid, upn, refresh_token, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(oid) DO UPDATE SET
                       upn          = excluded.upn,
                       refresh_token = excluded.refresh_token,
                       updated_at   = excluded.updated_at""",
                (oid, upn, refresh_token, time.time()),
            )
        log.debug("tokenstore: upserted token for oid=%s upn=%s", oid[:8], upn)
    except Exception as exc:
        log.warning("tokenstore.upsert failed: %s", exc)


def fetch(oid: str) -> Optional[str]:
    """Return the stored refresh token for *oid*, or None."""
    if not oid:
        return None
    try:
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT refresh_token FROM users WHERE oid = ?", (oid,)
            ).fetchone()
        if row:
            log.debug("tokenstore: found stored token for oid=%s", oid[:8])
            return row[0]
    except Exception as exc:
        log.warning("tokenstore.fetch failed: %s", exc)
    return None


def delete(oid: str) -> None:
    """Remove a user's stored token (called on logout)."""
    if not oid:
        return
    try:
        with _get_conn() as conn:
            conn.execute("DELETE FROM users WHERE oid = ?", (oid,))
        log.debug("tokenstore: deleted token for oid=%s", oid[:8])
    except Exception as exc:
        log.warning("tokenstore.delete failed: %s", exc)


# ── JWT payload helper ────────────────────────────────────────────────────────
def _b64pad(s: str) -> str:
    return s + "=" * (-len(s) % 4)


def decode_id_token_payload(id_token: str) -> dict:
    """Decode the payload of a JWT without signature verification.

    Only used to extract stable claims (oid, upn/preferred_username).
    Signature is already verified by Azure AD during the PKCE exchange.
    """
    try:
        parts = id_token.split(".")
        if len(parts) < 2:
            return {}
        payload = base64.urlsafe_b64decode(_b64pad(parts[1]))
        return json.loads(payload)
    except Exception as exc:
        log.debug("id_token decode failed: %s", exc)
        return {}
