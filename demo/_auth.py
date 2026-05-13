"""
demo/_auth.py - Single auth & env helper for all demo scripts.

Replaces the scattered inline ``httpx.post`` / ``requests.post`` token
flows found across drogon/, seisint/, strat/, and top-level demo scripts
with **one** function: ``get_token()``.

Secret sources (tried in order):
  1. k8s/secret.yaml + k8s/configmap.yaml  (``INSTANCE_<NAME>_*``)
  2. os.environ  (``INSTANCE_<NAME>_*``)
  3. Legacy .env file  (old-style flat keys)

Grant types detected automatically:
  - refresh_token   (when REFRESH_TOKEN present)
  - client_credentials  (when CLIENT_SECRET present)

Typical usage in a demo script::

    from _auth import get_token, load_instance

    inst = load_instance("eqndev")       # dict with host, partition, token, headers …
    # or simply:
    token = get_token("eqndev")
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import httpx
except ImportError:
    sys.exit("Missing httpx - pip install httpx")

# ── Paths ────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
K8S_DIR = REPO_ROOT / "k8s"

# ── Cached tokens (avoid re-minting within the same process) ────────────
_token_cache: Dict[str, tuple[str, float]] = {}   # name → (token, expiry_ts)


# ═══════════════════════════════════════════════════════════════════════════
# 1.  k8s YAML loader  (canonical - re-exported by gettoken.py)
# ═══════════════════════════════════════════════════════════════════════════

def _load_k8s_yaml(path: Path) -> Dict[str, str]:
    """Parse a k8s ConfigMap or Secret YAML into a flat dict."""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    try:
        import yaml
        doc = yaml.safe_load(text) or {}
        return {**(doc.get("data") or {}), **(doc.get("stringData") or {})}
    except ImportError:
        pass
    # Minimal fallback parser
    result: Dict[str, str] = {}
    in_data = False
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            if in_data and not raw.startswith((" ", "\t")):
                in_data = False
            continue
        if s in ("data:", "stringData:"):
            in_data = True
            continue
        if not raw[0].isspace():
            in_data = False
            continue
        if in_data and ":" in s:
            k, _, v = s.partition(":")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and not k.startswith("#"):
                result[k] = v
    return result


def load_k8s_env(k8s_dir: Optional[Path] = None) -> Dict[str, str]:
    """Merge configmap + secret from the k8s directory."""
    d = k8s_dir or K8S_DIR
    return {**_load_k8s_yaml(d / "configmap.yaml"),
            **_load_k8s_yaml(d / "secret.yaml")}


# ═══════════════════════════════════════════════════════════════════════════
# 2.  .env file loader  (legacy - still supported for backward compat)
# ═══════════════════════════════════════════════════════════════════════════

def parse_dotenv(path: Path) -> Dict[str, str]:
    """Parse a KEY=VALUE .env file into a dict, stripping quotes."""
    vals: Dict[str, str] = {}
    if not path.exists():
        return vals
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
            v = v[1:-1]
        vals[k] = v
    return vals


def _first(env: Dict[str, str], keys: List[str]) -> str:
    """Return first non-empty value for any of *keys* in *env*."""
    for k in keys:
        v = (env.get(k) or "").strip()
        if v:
            return v
    return ""


# ═══════════════════════════════════════════════════════════════════════════
# 3.  Instance resolution  (k8s → env → .env file)
# ═══════════════════════════════════════════════════════════════════════════

# Canonical alias map - used everywhere
ALIASES: Dict[str, str] = {"eqndev": "eqndev", "swedev": "eqndev", "preship": "preship", "oresdev": "oresdev"}


def _resolve_from_k8s(name: str, k8s_dir: Optional[Path] = None) -> Optional[Dict[str, str]]:
    """Try to build an instance config from k8s YAML files."""
    env = load_k8s_env(k8s_dir)
    prefix = f"INSTANCE_{name.upper()}_"
    fields = {k[len(prefix):].lower(): v for k, v in env.items()
              if k.startswith(prefix) and v}
    if not fields.get("tenant_id") and not fields.get("client_id"):
        return None
    return fields


def _resolve_from_environ(name: str) -> Optional[Dict[str, str]]:
    """Try to build an instance config from os.environ INSTANCE_<NAME>_* vars."""
    prefix = f"INSTANCE_{name.upper()}_"
    fields = {k[len(prefix):].lower(): v for k, v in os.environ.items()
              if k.startswith(prefix) and v}
    if not fields.get("tenant_id") and not fields.get("client_id"):
        return None
    return fields


def _resolve_from_dotenv(env_file: Optional[str] = None) -> Optional[Dict[str, str]]:
    """Try to build an instance config from a legacy .env file."""
    path = Path(env_file) if env_file else REPO_ROOT / ".env"
    if not path.exists():
        return None
    flat = parse_dotenv(path)
    if not flat:
        return None
    return {
        "tenant_id":     _first(flat, ["OSDU_TENANT_ID", "AZURE_TENANT_ID"]),
        "client_id":     _first(flat, ["OSDU_CLIENT_ID", "AZURE_CLIENT_ID"]),
        "scope":         _first(flat, ["OSDU_SCOPE", "AZURE_SCOPE"]),
        "refresh_token": _first(flat, ["refresh_token", "REFRESH_TOKEN"]),
        "client_secret": _first(flat, ["CLIENT_SECRET"]),
        "hostname":      _first(flat, ["OSDU_HOST", "OSDU_BASE_URL"]),
        "data_partition_id": _first(flat, ["OSDU_PARTITION", "DATA_PARTITION_ID"]),
        "default_legal_tag": _first(flat, ["LEGAL_TAG", "DEFAULT_LEGAL_TAG"]),
        "default_owners":    _first(flat, ["OWNERS", "DEFAULT_OWNERS"]),
        "default_viewers":   _first(flat, ["VIEWERS", "DEFAULT_VIEWERS"]),
        "default_countries": _first(flat, ["COUNTRIES", "DEFAULT_COUNTRIES"]),
    }


def load_instance(name: str = "eqndev", *,
                  env_file: Optional[str] = None,
                  k8s_dir: Optional[Path] = None) -> Dict[str, Any]:
    """
    Resolve a full instance config dict ready for use.

    Returns dict with keys:
      name, host, partition, tenant, client_id, scope,
      refresh_token, client_secret, grant,
      legal_tag, owners, viewers, countries
    """
    canonical = ALIASES.get(name.lower(), name.lower())

    # 1. k8s YAML
    fields = _resolve_from_k8s(canonical, k8s_dir)
    source = "k8s"

    # 2. os.environ (INSTANCE_<NAME>_*)
    if not fields:
        fields = _resolve_from_environ(canonical)
        source = "env"

    # 3. Legacy .env file
    if not fields:
        fields = _resolve_from_dotenv(env_file)
        source = "dotenv"

    if not fields:
        raise SystemExit(
            f"No config found for instance '{name}'.\n"
            f"Set INSTANCE_{canonical.upper()}_* in k8s/secret.yaml, env vars, or .env file."
        )

    host = fields.get("hostname", "")
    if host and not host.startswith("http"):
        host = f"https://{host}"
    partition = fields.get("data_partition_id", "")

    has_rt = bool(fields.get("refresh_token"))
    has_cs = bool(fields.get("client_secret"))
    grant = "refresh_token" if has_rt else ("client_credentials" if has_cs else "none")

    return {
        "name":          canonical,
        "source":        source,
        "host":          host,
        "partition":     partition,
        "tenant":        fields.get("tenant_id", ""),
        "client_id":     fields.get("client_id", ""),
        "scope":         fields.get("scope", ""),
        "refresh_token": fields.get("refresh_token", ""),
        "client_secret": fields.get("client_secret", ""),
        "grant":         grant,
        "legal_tag":     fields.get("default_legal_tag", ""),
        "owners":        [x.strip() for x in (fields.get("default_owners") or "").split(",") if x.strip()],
        "viewers":       [x.strip() for x in (fields.get("default_viewers") or "").split(",") if x.strip()],
        "countries":     [x.strip() for x in (fields.get("default_countries") or "NO").split(",") if x.strip()],
    }


# ═══════════════════════════════════════════════════════════════════════════
# 4.  Token minting
# ═══════════════════════════════════════════════════════════════════════════

def mint_from_env(env: Dict[str, str], *, verbose: bool = True) -> str:
    """
    Backward-compatible: mint a token from an old-style env dict
    (keys: tenant, client_id, refresh_token, scope [, client_secret]).

    Drop-in replacement for the per-script ``get_access_token(env)`` functions.

    Usage::
        from _auth import load_env, mint_from_env as get_access_token
        env = load_env([".env"])
        token = get_access_token(env)
    """
    inst = {
        "name":          "(env-dict)",
        "source":        "env-dict",
        "tenant":        env.get("tenant", ""),
        "client_id":     env.get("client_id", ""),
        "refresh_token": env.get("refresh_token", ""),
        "client_secret": env.get("client_secret", ""),
        "scope":         env.get("scope", ""),
        "host":          env.get("host", ""),
        "partition":     env.get("partition", ""),
        "grant":         "refresh_token" if env.get("refresh_token") else
                         ("client_credentials" if env.get("client_secret") else "none"),
    }
    return _mint(inst, verbose=verbose)


def _mint(inst: Dict[str, Any], *, verbose: bool = True) -> str:
    """Mint an access token from a resolved instance config."""
    tenant = inst.get("tenant", "")
    if not tenant:
        raise RuntimeError(f"No tenant_id for instance '{inst.get('name', '?')}'")
    url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"

    grant = inst.get("grant", "none")

    if grant == "refresh_token":
        form = {
            "grant_type":    "refresh_token",
            "client_id":     inst["client_id"],
            "refresh_token": inst["refresh_token"],
            "scope":         inst.get("scope") or f"{inst['client_id']}/.default openid offline_access",
        }
        # Confidential clients require client_secret even for refresh_token grants
        if inst.get("client_secret"):
            form["client_secret"] = inst["client_secret"]
    elif grant == "client_credentials":
        form = {
            "grant_type":    "client_credentials",
            "client_id":     inst["client_id"],
            "client_secret": inst["client_secret"],
            "scope":         inst.get("scope") or f"{inst['client_id']}/.default",
        }
    else:
        raise RuntimeError(f"No usable credentials for '{inst.get('name')}' (grant={grant})")

    r = httpx.post(url, data=form, timeout=30)
    if not r.is_success:
        raise RuntimeError(f"Auth failed ({r.status_code}): {r.text[:500]}")

    data = r.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"No access_token in response: {list(data.keys())}")

    if verbose:
        exp = data.get("expires_in", "?")
        print(f"  ✓ token ({inst.get('source', '?')}/{inst['name']}, "
              f"{grant}) expires_in={exp}s", file=sys.stderr)

    return token


def _mint_full(inst: Dict[str, Any], *, verbose: bool = True) -> Dict[str, Any]:
    """Like _mint() but returns the full token response dict (access_token, refresh_token, etc.)."""
    tenant = inst.get("tenant", "")
    if not tenant:
        raise RuntimeError(f"No tenant_id for instance '{inst.get('name', '?')}'")
    url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"

    grant = inst.get("grant", "none")
    if grant != "refresh_token":
        raise RuntimeError("Token rotation only works with refresh_token grant")

    form = {
        "grant_type":    "refresh_token",
        "client_id":     inst["client_id"],
        "refresh_token": inst["refresh_token"],
        "scope":         inst.get("scope") or f"{inst['client_id']}/.default openid offline_access",
    }

    r = httpx.post(url, data=form, timeout=30)
    if not r.is_success:
        raise RuntimeError(f"Auth failed ({r.status_code}): {r.text[:500]}")

    data = r.json()
    if not data.get("access_token"):
        raise RuntimeError(f"No access_token in response: {list(data.keys())}")

    if verbose:
        exp = data.get("expires_in", "?")
        has_new_rt = "refresh_token" in data
        print(f"  ✓ token ({inst.get('source', '?')}/{inst['name']}, "
              f"{grant}) expires_in={exp}s  new_rt={'yes' if has_new_rt else 'no'}",
              file=sys.stderr)

    return data


def rotate_token(name: str = "eqndev", *,
                 env_file: Optional[str] = None,
                 k8s_dir: Optional[Path] = None,
                 verbose: bool = True) -> Dict[str, str]:
    """
    Exchange the current refresh_token for a new access_token + refresh_token pair.

    Returns dict with keys: access_token, refresh_token (the NEW one), expires_in.
    The caller is responsible for persisting the new refresh_token to storage.
    """
    canonical = ALIASES.get(name.lower(), name.lower())
    inst = load_instance(canonical, env_file=env_file, k8s_dir=k8s_dir)
    data = _mint_full(inst, verbose=verbose)

    new_rt = data.get("refresh_token", inst["refresh_token"])
    return {
        "access_token":  data["access_token"],
        "refresh_token": new_rt,
        "expires_in":    str(data.get("expires_in", "3600")),
        "old_refresh_token": inst["refresh_token"],
        "rotated":       str(new_rt != inst["refresh_token"]).lower(),
    }


def get_token(name: str = "eqndev", *,
              env_file: Optional[str] = None,
              k8s_dir: Optional[Path] = None,
              verbose: bool = True) -> str:
    """
    One-call token minting.  Caches within the process lifetime.

    Usage::
        token = get_token("eqndev")
        token = get_token("preship")
    """
    canonical = ALIASES.get(name.lower(), name.lower())

    # Check cache
    cached = _token_cache.get(canonical)
    if cached and time.time() < cached[1]:
        return cached[0]

    inst = load_instance(canonical, env_file=env_file, k8s_dir=k8s_dir)
    token = _mint(inst, verbose=verbose)

    # Cache with conservative expiry
    _token_cache[canonical] = (token, time.time() + 3000)
    return token


# ═══════════════════════════════════════════════════════════════════════════
# 5.  Convenience: headers, base_url
# ═══════════════════════════════════════════════════════════════════════════

def api_headers(name: str = "eqndev", **kw) -> Dict[str, str]:
    """Return ready-to-use OSDU API headers including a fresh Bearer token."""
    inst = load_instance(name, **kw)
    token = get_token(name, **kw)
    return {
        "Authorization":    f"Bearer {token}",
        "Content-Type":     "application/json",
        "data-partition-id": inst["partition"],
    }


def base_url(name: str = "eqndev", **kw) -> str:
    """Return the OSDU base URL (https://...) for the named instance."""
    inst = load_instance(name, **kw)
    return inst["host"]


# ═══════════════════════════════════════════════════════════════════════════
# 6.  Backward-compatible load_env() for scripts that pass .env paths
# ═══════════════════════════════════════════════════════════════════════════

def load_env(paths: Optional[List[str]] = None, *,
             instance: Optional[str] = None) -> Dict[str, str]:
    """
    Backward-compatible: returns a dict with keys matching the old
    ``_shared.load_env()`` convention, but resolves secrets via the
    k8s → env → .env chain.

    Returned keys: refresh_token, tenant, client_id, scope, host, partition.

    If *instance* is given, resolve from that instance's config.
    Otherwise, try k8s/env first, then fall back to .env file paths.
    """
    if instance:
        inst = load_instance(instance)
    else:
        # Try k8s/env for default instance
        try:
            default = os.environ.get("DEFAULT_INSTANCE", "eqndev")
            inst = load_instance(default)
        except SystemExit:
            # Fall back to .env files
            if not paths:
                paths = [str(REPO_ROOT / ".env")]
            merged: Dict[str, str] = {}
            for p in paths:
                fp = Path(p).expanduser().resolve()
                if fp.exists():
                    merged.update(parse_dotenv(fp))
            inst = {
                "refresh_token": _first(merged, ["refresh_token", "REFRESH_TOKEN"]),
                "tenant":        _first(merged, ["OSDU_TENANT_ID", "AZURE_TENANT_ID"]),
                "client_id":     _first(merged, ["OSDU_CLIENT_ID", "AZURE_CLIENT_ID"]),
                "scope":         _first(merged, ["OSDU_SCOPE", "AZURE_SCOPE"]),
                "host":          _first(merged, ["OSDU_HOST", "OSDU_BASE_URL"]),
                "partition":     _first(merged, ["OSDU_PARTITION", "DATA_PARTITION_ID"]),
            }
            h = inst.get("host", "")
            if h and not h.startswith("http"):
                inst["host"] = f"https://{h}"
            missing = [k for k in ("refresh_token", "tenant", "client_id", "scope", "host", "partition")
                       if not inst.get(k)]
            if missing:
                raise SystemExit(f"Missing keys: {', '.join(missing)}")
            return inst

    # Normalise load_instance() output to old flat dict format
    return {
        "refresh_token": inst.get("refresh_token", ""),
        "tenant":        inst.get("tenant", ""),
        "client_id":     inst.get("client_id", ""),
        "scope":         inst.get("scope", ""),
        "host":          inst.get("host", ""),
        "partition":     inst.get("partition", ""),
    }
