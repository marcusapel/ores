"""
app/instances.py — Multi-instance OSDU configuration.

Each instance is defined by env vars with the pattern INSTANCE_<NAME>_<KEY>.
The "default" instance uses the existing top-level env vars.

Token strategies (tried in order of preference):
  1. refresh_token  → uses stored REFRESH_TOKEN (user-level, preferred)
  2. client_credentials → uses CLIENT_ID + CLIENT_SECRET (service-level fallback)

Store *at least one of* REFRESH_TOKEN or CLIENT_SECRET per instance.
"""
from __future__ import annotations

import os
import time
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict

import httpx

log = logging.getLogger("rddms-admin.instances")


@dataclass
class OsduInstance:
    """Configuration for a single OSDU instance."""
    name: str
    hostname: str                           # e.g. equinorswedev.energy.azure.com
    data_partition_id: str                  # e.g. dev, opendes
    tenant_id: str = ""
    client_id: str = ""
    client_secret: str = ""                 # only for client_credentials flow
    scope: str = ""                         # e.g. "<client_id>/.default openid ..."
    authority: str = "osdu"                 # schema authority prefix
    schema_source: str = "wks"
    default_legal_tag: str = ""
    default_owners: str = ""
    default_viewers: str = ""
    default_countries: str = "NO"
    refresh_token: str = ""                 # shared refresh token (if any)
    auth_mode: str = "refresh_token"        # refresh_token | client_credentials | az_cli

    # --- runtime token cache ---
    _cached_token: str = field(default="", repr=False)
    _cached_exp: float = field(default=0.0, repr=False)

    @property
    def base_url(self) -> str:
        return self.hostname

    @property
    def token_url(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"

    def _partition_suffix(self) -> str:
        return f"{self.data_partition_id}.dataservices.energy" if self.data_partition_id else ""

    async def get_access_token(self) -> Optional[str]:
        """Mint or return cached access_token for this instance.

        Tries refresh_token first, then client_credentials.
        """
        if self._cached_token and time.time() < self._cached_exp:
            return self._cached_token

        token = None

        # 1. Prefer refresh_token (user-level access)
        if self.refresh_token:
            token = await self._refresh_token_flow()

        # 2. Fallback to client_credentials (service-principal)
        if not token and self.client_secret:
            token = await self._client_credentials()

        if token:
            self._cached_token = token["access_token"]
            self._cached_exp = time.time() + max(int(token.get("expires_in", 3600)) - 120, 60)
            return self._cached_token
        return None

    async def _client_credentials(self) -> Optional[Dict]:
        """OAuth2 client_credentials grant."""
        scope = self.scope or f"{self.client_id}/.default"
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": scope,
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(self.token_url, data=data)
                if r.status_code >= 400:
                    log.warning("client_credentials failed for %s: %s %s",
                                self.name, r.status_code, r.text[:200])
                    return None
                body = r.json()
                log.info("Got client_credentials token for instance '%s' (expires_in=%s)",
                         self.name, body.get("expires_in"))
                return body
        except Exception as e:
            log.warning("client_credentials error for %s: %s", self.name, e)
            return None

    async def _refresh_token_flow(self) -> Optional[Dict]:
        """OAuth2 refresh_token grant."""
        scope_str = self.scope or f"{self.client_id}/.default openid offline_access"
        data = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "refresh_token": self.refresh_token,
            "scope": scope_str,
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(self.token_url, data=data)
                if r.status_code >= 400:
                    log.warning("refresh_token failed for %s: %s %s",
                                self.name, r.status_code, r.text[:200])
                    return None
                body = r.json()
                # Update refresh token if rotated
                if body.get("refresh_token"):
                    self.refresh_token = body["refresh_token"]
                return body
        except Exception as e:
            log.warning("refresh_token error for %s: %s", self.name, e)
            return None


# ── Registry ──────────────────────────────────────────────────────────────

_instances: Dict[str, OsduInstance] = {}
_active_instance_name: str = ""


def _load_instances():
    """Build instance registry from environment variables.

    Convention:
      INSTANCE_<NAME>_HOSTNAME, INSTANCE_<NAME>_DATA_PARTITION_ID, etc.
    The "default" instance is always built from existing top-level env vars.
    """
    global _active_instance_name

    # Default instance (existing Equinor dev / whatever is in .env)
    _rt = os.getenv("REFRESH_TOKEN", "") or os.getenv("refresh_token", "")
    default = OsduInstance(
        name="default",
        hostname=os.getenv("OSDU_BASE_URL", "equinordev.energy.azure.com"),
        data_partition_id=os.getenv("DATA_PARTITION_ID", "dev"),
        tenant_id=os.getenv("AZURE_TENANT_ID", ""),
        client_id=os.getenv("AZURE_CLIENT_ID", ""),
        scope=os.getenv("AZURE_SCOPE", ""),
        refresh_token=_rt,
        default_legal_tag=os.getenv("DEFAULT_LEGAL_TAG", ""),
        default_owners=os.getenv("DEFAULT_OWNERS", ""),
        default_viewers=os.getenv("DEFAULT_VIEWERS", ""),
        default_countries=os.getenv("DEFAULT_COUNTRIES", "NO"),
        auth_mode="refresh_token" if _rt else "none",
    )
    _instances["default"] = default
    _active_instance_name = "default"

    # Scan for INSTANCE_<NAME>_HOSTNAME patterns
    seen: set[str] = set()
    for key in os.environ:
        if key.startswith("INSTANCE_") and key.count("_") >= 2:
            parts = key.split("_", 2)  # INSTANCE, NAME, FIELD
            seen.add(parts[1].lower())

    for inst_name in sorted(seen):
        prefix = f"INSTANCE_{inst_name.upper()}_"
        _get = lambda field, default="": os.getenv(f"{prefix}{field}", default)

        hostname = _get("HOSTNAME")
        if not hostname:
            continue  # skip incomplete entries

        # Describe auth mode based on available credentials
        client_secret = _get("CLIENT_SECRET")
        refresh = _get("REFRESH_TOKEN")
        if refresh and client_secret:
            mode = "refresh_token+client_credentials"
        elif refresh:
            mode = "refresh_token"
        elif client_secret:
            mode = "client_credentials"
        else:
            mode = "none"

        inst = OsduInstance(
            name=inst_name,
            hostname=hostname,
            data_partition_id=_get("DATA_PARTITION_ID", "opendes"),
            tenant_id=_get("TENANT_ID"),
            client_id=_get("CLIENT_ID"),
            client_secret=client_secret,
            scope=_get("SCOPE"),
            authority=_get("AUTHORITY", "osdu"),
            schema_source=_get("SCHEMA_SOURCE", "wks"),
            default_legal_tag=_get("DEFAULT_LEGAL_TAG"),
            default_owners=_get("DEFAULT_OWNERS"),
            default_viewers=_get("DEFAULT_VIEWERS"),
            default_countries=_get("DEFAULT_COUNTRIES", "NO"),
            refresh_token=refresh,
            auth_mode=mode,
        )
        _instances[inst_name] = inst
        log.info("Registered OSDU instance '%s' → %s (partition=%s, auth=%s)",
                 inst_name, hostname, inst.data_partition_id, mode)


def get_instances() -> Dict[str, OsduInstance]:
    if not _instances:
        _load_instances()
    return _instances


def get_active() -> OsduInstance:
    if not _instances:
        _load_instances()
    return _instances[_active_instance_name]


def set_active(name: str) -> OsduInstance:
    """Switch the active instance. Returns the new active instance."""
    global _active_instance_name
    if not _instances:
        _load_instances()
    if name not in _instances:
        raise ValueError(f"Unknown instance: {name!r}. Available: {list(_instances.keys())}")

    _active_instance_name = name
    inst = _instances[name]

    # Update osdu.py module-level globals so all existing code works
    import app.osdu as osdu_mod
    osdu_mod.OSDU_BASE_URL = inst.hostname
    osdu_mod.DATA_PARTITION_ID = inst.data_partition_id
    osdu_mod.DEFAULT_LEGAL_TAG = inst.default_legal_tag or (
        f"{inst.data_partition_id}-equinor-private-default" if inst.data_partition_id else ""
    )
    pfx = inst._partition_suffix()
    osdu_mod.DEFAULT_OWNERS = [x.strip() for x in (inst.default_owners or f"data.default.owners@{pfx}").split(",") if x.strip()]
    osdu_mod.DEFAULT_VIEWERS = [x.strip() for x in (inst.default_viewers or f"data.default.viewers@{pfx}").split(",") if x.strip()]
    osdu_mod.DEFAULT_COUNTRIES = [x.strip() for x in (inst.default_countries or "NO").split(",") if x.strip()]

    log.info("Switched active instance → '%s' (%s, partition=%s)",
             name, inst.hostname, inst.data_partition_id)
    return inst


def get_active_name() -> str:
    if not _instances:
        _load_instances()
    return _active_instance_name
