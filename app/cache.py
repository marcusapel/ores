"""
app/cache.py — Lightweight async-aware TTL cache for expensive API results.

Provides a simple in-memory cache keyed by (function_name, *args) with a
configurable time-to-live (TTL).  Designed for data that is expensive to
fetch but changes infrequently — dataspace lists, reference data, etc.

Thread-safe for asyncio (single event-loop) by design.

Usage:
    from app.cache import ttl_cache

    @ttl_cache(ttl=120)
    async def list_dataspaces(at: str) -> list:
        ...

    # Or manual usage:
    result = await cached_call("dataspaces", ttl=120, fn=fetch_fn)

    # Invalidate a specific key:
    cache_invalidate("list_dataspaces")

    # Invalidate everything:
    cache_clear()
"""
from __future__ import annotations

import asyncio
import functools
import logging
import time
from typing import Any, Callable, Dict, Optional, Tuple

log = logging.getLogger("rddms-admin.cache")

# ── Storage ──────────────────────────────────────────────────────────────────

# key -> (value, expiry_timestamp)
_store: Dict[str, Tuple[Any, float]] = {}

# key -> asyncio.Lock (to prevent thundering herd on concurrent misses)
_locks: Dict[str, asyncio.Lock] = {}


def _get_lock(key: str) -> asyncio.Lock:
    if key not in _locks:
        _locks[key] = asyncio.Lock()
    return _locks[key]


# ── Public API ───────────────────────────────────────────────────────────────

def cache_get(key: str) -> Optional[Any]:
    """Return cached value if present and not expired, else None."""
    entry = _store.get(key)
    if entry is None:
        return None
    value, expiry = entry
    if time.monotonic() > expiry:
        _store.pop(key, None)
        return None
    return value


def cache_set(key: str, value: Any, ttl: float) -> None:
    """Store a value with the given TTL (seconds)."""
    _store[key] = (value, time.monotonic() + ttl)


def cache_invalidate(*prefixes: str) -> int:
    """Remove entries whose key starts with any of the given prefixes.
    Returns number of entries removed.  With no args, removes nothing."""
    if not prefixes:
        return 0
    to_del = [k for k in _store if any(k.startswith(p) for p in prefixes)]
    for k in to_del:
        _store.pop(k, None)
        _locks.pop(k, None)
    return len(to_del)


def cache_clear() -> int:
    """Remove all cached entries. Returns count removed."""
    n = len(_store)
    _store.clear()
    _locks.clear()
    return n


async def cached_call(
    key: str,
    ttl: float,
    fn: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Return cached result or call *fn* and cache the result.

    Uses an asyncio Lock per key to prevent thundering-herd: if two
    concurrent requests both miss the cache, only one calls the backend.
    """
    hit = cache_get(key)
    if hit is not None:
        log.debug("cache HIT: %s", key)
        return hit

    lock = _get_lock(key)
    async with lock:
        # Re-check after acquiring the lock (another coroutine may have filled it)
        hit = cache_get(key)
        if hit is not None:
            return hit

        log.debug("cache MISS: %s — calling backend", key)
        result = await fn(*args, **kwargs)
        cache_set(key, result, ttl)
        return result


# ── Decorator ────────────────────────────────────────────────────────────────

def ttl_cache(ttl: float = 120, key_prefix: str = ""):
    """Decorator that caches an async function's result for *ttl* seconds.

    The cache key is built from the function name + all positional args.
    The first positional arg is typically an access_token which changes
    per user; set ``skip_first_arg=True`` if you want to share the cache
    across users (e.g. for dataspace lists that are instance-global).
    """
    def decorator(fn: Callable):
        prefix = key_prefix or fn.__qualname__

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            # Build cache key — include all positional args except
            # the access_token (first arg) since the data is the same
            # regardless of who fetches it.
            cache_key = f"{prefix}:{':'.join(str(a) for a in args[1:])}" if len(args) > 1 else prefix
            return await cached_call(cache_key, ttl, fn, *args, **kwargs)
        return wrapper
    return decorator
