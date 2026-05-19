"""
SOS Kernel — Typed settings loader (pydantic-settings v2).

Single source of truth for environment-driven configuration. Replaces the
~100 scattered ``os.environ.get`` call sites by grouping env vars into
typed ``BaseSettings`` classes.

Design principles
-----------------
- **Back-compat on env names.** Every field reads from the same env-var
  name the legacy ``os.environ.get`` call used. No renames in this pass.
- **Validation without cliff.** Most fields are optional with sensible
  defaults, so importing this module can never crash a service. The
  ``validate_startup_env()`` helper is opt-in: services that *require*
  secrets at boot can invoke it to fail fast.
- **Secrets never logged.** Credentials use ``SecretStr`` — their repr is
  redacted, so ``logger.info(settings)`` is safe.
- **Single cached accessor.** ``get_settings()`` is LRU-cached; the module
  also exposes ``settings`` as a convenience alias for call sites that
  prefer module-level attribute access.

Not a schema registry
---------------------
This file IS the env-var catalog. There is intentionally no ``.env.schema``
— new env vars get documented by landing here as typed fields.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------------------------------------------------------------------------
# Base class — all groups share the same .env + os.environ loader contract.
# ---------------------------------------------------------------------------


class _BaseGroup(BaseSettings):
    """Shared config: reads `.env`, ignores extra keys, case-insensitive env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


# ---------------------------------------------------------------------------
# Redis — collapses ~25 duplicated reads across services + kernel.
# ---------------------------------------------------------------------------


class RedisSettings(_BaseGroup):
    """Redis connection config.

    Historical sprawl reconciled here:

    - ``REDIS_URL`` — preferred connection URL.
    - ``SOS_REDIS_URL`` — legacy alias; if both set, ``REDIS_URL`` wins for
      the ``url`` field and ``SOS_REDIS_URL`` remains available as
      ``legacy_sos_url`` for the handful of kernel modules that read it
      by name.
    - ``REDIS_PASSWORD`` — auth password; when set and ``url`` is bare
      ``redis://localhost:...``, ``resolved_url`` injects the auth.
    - ``REDIS_HOST`` / ``REDIS_PORT`` — host/port split used by a few
      services (health, brain, journeys) that build URLs manually.
    """

    url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    legacy_sos_url: Optional[str] = Field(default=None, alias="SOS_REDIS_URL")
    password: SecretStr = Field(default=SecretStr(""), alias="REDIS_PASSWORD")
    host: str = Field(default="localhost", alias="REDIS_HOST")
    port: int = Field(default=6379, alias="REDIS_PORT")

    # Stream defaults — historically literal 1000/10000 inline. Expose so
    # ops can tune without redeploy, but keep conservative defaults.
    stream_maxlen_default: int = Field(default=1000, alias="SOS_REDIS_STREAM_MAXLEN")
    audit_stream_maxlen: int = Field(default=10000, alias="SOS_AUDIT_STREAM_MAXLEN")

    @property
    def password_str(self) -> str:
        """Plain password (empty string if unset). Use sparingly."""
        return self.password.get_secret_value()

    @property
    def resolved_url(self) -> str:
        """URL with password injected if `url` is bare localhost + password set.

        Mirrors the legacy pattern:
            REDIS_URL or f"redis://:{pw}@localhost:6379/0" if pw else default
        """
        pw = self.password_str
        explicit = self.legacy_sos_url or None
        base = explicit or self.url
        if pw and "@" not in base and base.startswith("redis://"):
            # Inject auth: redis://host:port/db -> redis://:pw@host:port/db
            return base.replace("redis://", f"redis://:{pw}@", 1)
        return base

    def build_url(self, *, db: int | None = None) -> str:
        """Build a redis URL honouring host/port/password. Used by services
        that construct URLs from parts instead of taking REDIS_URL whole.
        """
        pw = self.password_str
        auth = f":{pw}@" if pw else ""
        db_suffix = f"/{db}" if db is not None else ""
        return f"redis://{auth}{self.host}:{self.port}{db_suffix}"


# ---------------------------------------------------------------------------
# Service URLs — internal service discovery. Collapses ~40 duplicated reads.
# ---------------------------------------------------------------------------


class ServiceURLSettings(_BaseGroup):
    """Internal HTTP base URLs for SOS services.

    Defaults mirror the legacy inline defaults. Field names match the
    short service name; env aliases preserve the two historical naming
    conventions (``FOO_URL`` and ``SOS_FOO_URL``).
    """

    saas: str = Field(default="http://localhost:8075", alias="SOS_SAAS_URL")
    squad: str = Field(default="http://127.0.0.1:8060", alias="SQUAD_URL")
    squad_alt: Optional[str] = Field(default=None, alias="SOS_SQUAD_URL")
    engine: str = Field(default="http://localhost:6060", alias="SOS_ENGINE_URL")
    memory: str = Field(default="http://localhost:6061", alias="SOS_MEMORY_URL")
    economy: str = Field(default="http://localhost:6062", alias="SOS_ECONOMY_URL")
    tools: str = Field(default="http://localhost:6063", alias="SOS_TOOLS_URL")
    identity: str = Field(default="http://localhost:6064", alias="SOS_IDENTITY_URL")
    integrations: Optional[str] = Field(default=None, alias="SOS_INTEGRATIONS_URL")
    registry: str = Field(default="http://localhost:6067", alias="SOS_REGISTRY_URL")
    operations: Optional[str] = Field(default=None, alias="SOS_OPERATIONS_URL")
    brain: Optional[str] = Field(default=None, alias="SOS_BRAIN_URL")
    mirror: str = Field(default="http://localhost:8844", alias="MIRROR_URL")

    @property
    def squad_url(self) -> str:
        """Prefer SOS_SQUAD_URL when set (newer convention), else SQUAD_URL."""
        return self.squad_alt or self.squad


# ---------------------------------------------------------------------------
# Audit — disk path + retention knobs.
# ---------------------------------------------------------------------------


class AuditSettings(_BaseGroup):
    """Audit-log configuration."""

    dir_override: Optional[Path] = Field(default=None, alias="SOS_AUDIT_DIR")
    retention_days: int = Field(default=90, alias="SOS_AUDIT_RETENTION_DAYS")

    @property
    def dir(self) -> Path:
        """Resolved audit directory — default ~/.sos/audit."""
        if self.dir_override is not None:
            return self.dir_override
        return Path.home() / ".sos" / "audit"


# ---------------------------------------------------------------------------
# Gateway — internal API key + registry path.
# ---------------------------------------------------------------------------


class GatewaySettings(_BaseGroup):
    """API Gateway / bridge config."""

    internal_key: SecretStr = Field(default=SecretStr(""), alias="MUMEGA_INTERNAL_KEY")
    tenant_registry_path: Optional[Path] = Field(
        default=None, alias="MUMEGA_TENANT_REGISTRY"
    )

    @property
    def internal_key_str(self) -> str:
        return self.internal_key.get_secret_value()

    @property
    def has_internal_key(self) -> bool:
        return bool(self.internal_key_str)


# ---------------------------------------------------------------------------
# Feature flags — boolean switches.
# ---------------------------------------------------------------------------


def _parse_bool(val: str | int | bool | None) -> bool:
    if isinstance(val, bool):
        return val
    if val is None:
        return False
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


class FeatureFlags(_BaseGroup):
    """Boolean feature flags.

    All flags default to ``False`` to match the legacy "flag absent =
    disabled" behaviour. The env-var values follow the legacy truthy-set
    ``{"1","true","yes","on"}``.
    """

    require_capabilities: bool = Field(default=False, alias="SOS_REQUIRE_CAPABILITIES")
    tools_execution_enabled: bool = Field(default=False, alias="SOS_TOOLS_EXECUTION")
    dreams_enabled: bool = Field(default=False, alias="SOS_ENABLE_DREAMS")
    avatar_enabled: bool = Field(default=False, alias="SOS_ENABLE_AVATAR")
    social_enabled: bool = Field(default=False, alias="SOS_ENABLE_SOCIAL")
    telemetry_enabled: bool = Field(default=False, alias="SOS_TELEMETRY_ENABLED")

    # pydantic-settings parses bools from standard truthy strings, but the
    # legacy code also accepted "on" — normalize via validator
    def __init__(self, **data):
        # Accept the legacy truthy set even though pydantic would reject "on"
        truthy_overrides = {}
        import os as _os

        for alias, attr in (
            ("SOS_REQUIRE_CAPABILITIES", "require_capabilities"),
            ("SOS_TOOLS_EXECUTION", "tools_execution_enabled"),
            ("SOS_ENABLE_DREAMS", "dreams_enabled"),
            ("SOS_ENABLE_AVATAR", "avatar_enabled"),
            ("SOS_ENABLE_SOCIAL", "social_enabled"),
            ("SOS_TELEMETRY_ENABLED", "telemetry_enabled"),
        ):
            raw = _os.environ.get(alias)
            if raw is not None and attr not in data:
                truthy_overrides[attr] = _parse_bool(raw)
        data = {**truthy_overrides, **data}
        super().__init__(**data)


# ---------------------------------------------------------------------------
# Integrations — OAuth / payment / chain credentials. All SecretStr.
# Not migrated aggressively in this pass; included so call sites *can* move
# as they're touched.
# ---------------------------------------------------------------------------


class IntegrationSettings(_BaseGroup):
    """Third-party integration credentials.

    All secrets use ``SecretStr`` — ``repr(settings.integrations)`` is safe
    to log; accessing plaintext requires ``.get_secret_value()``.
    """

    # GHL
    ghl_client_id: Optional[SecretStr] = Field(default=None, alias="GHL_CLIENT_ID")
    ghl_client_secret: Optional[SecretStr] = Field(default=None, alias="GHL_CLIENT_SECRET")

    # Stripe
    stripe_api_key: Optional[SecretStr] = Field(default=None, alias="STRIPE_API_KEY")
    stripe_webhook_secret: Optional[SecretStr] = Field(
        default=None, alias="STRIPE_WEBHOOK_SECRET"
    )

    # Solana
    solana_rpc_url: Optional[str] = Field(default=None, alias="SOLANA_RPC_URL")
    solana_private_key: Optional[SecretStr] = Field(default=None, alias="SOLANA_PRIVATE_KEY")

    # Google OAuth / Gemini
    google_client_id: Optional[SecretStr] = Field(default=None, alias="GOOGLE_CLIENT_ID")
    google_client_secret: Optional[SecretStr] = Field(
        default=None, alias="GOOGLE_CLIENT_SECRET"
    )
    gemini_api_key: Optional[SecretStr] = Field(default=None, alias="GEMINI_API_KEY")
    anthropic_api_key: Optional[SecretStr] = Field(default=None, alias="ANTHROPIC_API_KEY")
    openai_api_key: Optional[SecretStr] = Field(default=None, alias="OPENAI_API_KEY")


# ---------------------------------------------------------------------------
# Auth-gateway bridge — acknowledge the service-local settings rather than
# duplicating. This leaves the auth_gateway-owned config in its own module
# while exposing only the fields other layers legitimately need.
# ---------------------------------------------------------------------------


class AuthGatewaySettings(_BaseGroup):
    """Minimal bridge to the ``sos/services/auth_gateway/`` config surface.

    The auth_gateway service owns its own DB/vault settings (see
    ``sos/services/auth_gateway/database.py`` and ``vault.py``). This
    class only exposes the handful of env vars other parts of SOS read.
    """

    system_token: SecretStr = Field(default=SecretStr(""), alias="SOS_SYSTEM_TOKEN")
    registry_token: Optional[SecretStr] = Field(default=None, alias="SOS_REGISTRY_TOKEN")

    @property
    def system_token_str(self) -> str:
        return self.system_token.get_secret_value()


# ---------------------------------------------------------------------------
# Top-level aggregate.
# ---------------------------------------------------------------------------


class Settings(_BaseGroup):
    """Aggregate of every SOS settings group.

    Prefer ``get_settings()`` or the module-level ``settings`` singleton
    over instantiating directly — it caches and uses a single snapshot of
    the environment. Tests that manipulate env vars should call
    ``reload_settings()`` after mutation.
    """

    redis: RedisSettings = Field(default_factory=RedisSettings)
    services: ServiceURLSettings = Field(default_factory=ServiceURLSettings)
    audit: AuditSettings = Field(default_factory=AuditSettings)
    gateway: GatewaySettings = Field(default_factory=GatewaySettings)
    features: FeatureFlags = Field(default_factory=FeatureFlags)
    integrations: IntegrationSettings = Field(default_factory=IntegrationSettings)
    auth: AuthGatewaySettings = Field(default_factory=AuthGatewaySettings)


# ---------------------------------------------------------------------------
# Accessors.
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor — safe to call per-request in FastAPI dependencies."""
    return Settings()


def reload_settings() -> Settings:
    """Drop the cache and rebuild from the current environment. Tests."""
    get_settings.cache_clear()
    return get_settings()


# Module-level convenience alias. Do NOT mutate. Tests should use
# reload_settings() after touching os.environ.
settings: Settings = get_settings()


# ---------------------------------------------------------------------------
# Startup validation.
# ---------------------------------------------------------------------------


class ConfigError(RuntimeError):
    """Raised when required environment config is missing at startup."""


def validate_startup_env(
    *,
    require_system_token: bool = False,
    require_gateway_key: bool = False,
) -> None:
    """Fail-fast startup check. Call from service `__main__` or app factory.

    Kept permissive by default so smoke tests and dev runs don't trip.
    Services that *must* have a secret at boot pass the matching
    ``require_*`` flag.

    Raises
    ------
    ConfigError
        With a clear message naming every missing var.
    """
    s = get_settings()
    missing: list[str] = []

    if require_system_token and not s.auth.system_token_str:
        missing.append("SOS_SYSTEM_TOKEN")

    if require_gateway_key and not s.gateway.has_internal_key:
        missing.append("MUMEGA_INTERNAL_KEY")

    if missing:
        raise ConfigError(
            "Missing required environment variables: " + ", ".join(missing)
        )


__all__ = [
    "RedisSettings",
    "ServiceURLSettings",
    "AuditSettings",
    "GatewaySettings",
    "FeatureFlags",
    "IntegrationSettings",
    "AuthGatewaySettings",
    "Settings",
    "ConfigError",
    "get_settings",
    "reload_settings",
    "settings",
    "validate_startup_env",
]
