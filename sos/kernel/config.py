"""
SOS Kernel Config - Configuration and runtime paths management.

Configuration in SOS is:
- Layered: defaults → system → user → environment → runtime
- Validated: schema-checked before use
- Observable: changes emit events
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
import json
import os


# ---------------------------------------------------------------------------
# Kernel-level constants (single source of truth for path + tenant defaults).
# Moved from sos/services/squad/service.py in v0.4.4 Wave 4 to fix R1
# violation (policy layer reaching into service layer). Kept intentionally
# trivial — v0.5.0 is free to replace with richer config plumbing.
# ---------------------------------------------------------------------------

SOS_DATA_DIR: Path = Path(os.environ.get("SOS_DATA_DIR", str(Path.home() / ".sos" / "data")))
DB_PATH: Path = SOS_DATA_DIR / "squads.db"
DEFAULT_TENANT_ID: str = "default"


@dataclass
class RuntimePaths:
    """
    Standard paths for SOS runtime.

    All paths are relative to SOS_HOME (default: ~/.sos).
    """
    home: Path = field(default_factory=lambda: Path.home() / ".sos")

    @property
    def config_dir(self) -> Path:
        """Configuration directory."""
        return self.home / "config"

    @property
    def data_dir(self) -> Path:
        """Data storage directory."""
        return self.home / "data"

    @property
    def cache_dir(self) -> Path:
        """Cache directory."""
        return self.home / "cache"

    @property
    def logs_dir(self) -> Path:
        """Logs directory."""
        return self.home / "logs"

    @property
    def plugins_dir(self) -> Path:
        """Plugins directory."""
        return self.home / "plugins"

    @property
    def secrets_dir(self) -> Path:
        """Secrets directory (encrypted)."""
        return self.home / "secrets"

    @property
    def tasks_dir(self) -> Path:
        """Tasks directory."""
        return self.home / "tasks"

    @property
    def memory_dir(self) -> Path:
        """Memory/vector store directory."""
        return self.data_dir / "memory"

    @property
    def ledger_dir(self) -> Path:
        """Economy ledger directory."""
        return self.data_dir / "ledger"

    @property
    def artifacts_dir(self) -> Path:
        """Artifacts (content-addressed bundles) directory."""
        return self.data_dir / "artifacts"

    def ensure_dirs(self) -> None:
        """Create all required directories."""
        for path in [
            self.config_dir,
            self.data_dir,
            self.cache_dir,
            self.logs_dir,
            self.plugins_dir,
            self.secrets_dir,
            self.tasks_dir,
            self.memory_dir,
            self.ledger_dir,
            self.artifacts_dir,
        ]:
            path.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> RuntimePaths:
        """Create RuntimePaths from environment variables."""
        home = os.environ.get("SOS_HOME", str(Path.home() / ".sos"))
        return cls(home=Path(home))


@dataclass
class Config:
    """
    SOS configuration management.

    Configuration is loaded from multiple sources in order:
    1. Built-in defaults
    2. System config (/etc/sos/config.json)
    3. User config (~/.sos/config/sos.json)
    4. Environment variables (SOS_*)
    5. Runtime overrides

    Attributes:
        paths: Runtime paths configuration
        edition: Edition policy set (business, education, kids, art)
        ipc_mode: IPC mode (http, unix)
        log_level: Logging level
        services: Service-specific configuration
        features: Feature flags
    """
    paths: RuntimePaths = field(default_factory=RuntimePaths.from_env)
    edition: str = "business"
    ipc_mode: str = "http"  # http | unix
    log_level: str = "info"
    services: dict[str, dict[str, Any]] = field(default_factory=dict)
    features: dict[str, bool] = field(default_factory=dict)

    # Service URLs (for http mode)
    engine_url: str = "http://localhost:6060"
    memory_url: str = "http://localhost:6061"
    economy_url: str = "http://localhost:6062"
    tools_url: str = "http://localhost:6063"

    # Skills path (OpenClaw vendor integration)
    skills_path: str = str(Path.home() / ".agents" / "skills")

    def __post_init__(self):
        """Apply environment variable overrides."""
        self._apply_env_overrides()

    def _apply_env_overrides(self) -> None:
        """Override config from environment variables."""
        env_mappings = {
            "SOS_EDITION": "edition",
            "SOS_IPC_MODE": "ipc_mode",
            "SOS_LOG_LEVEL": "log_level",
            "SOS_ENGINE_URL": "engine_url",
            "SOS_MEMORY_URL": "memory_url",
            "SOS_ECONOMY_URL": "economy_url",
            "SOS_TOOLS_URL": "tools_url",
            "SOS_SKILLS_PATH": "skills_path",
        }

        for env_var, attr in env_mappings.items():
            if value := os.environ.get(env_var):
                setattr(self, attr, value)

        # Feature flags from environment
        for key, value in os.environ.items():
            if key.startswith("SOS_FEATURE_"):
                feature_name = key[12:].lower()  # Remove SOS_FEATURE_ prefix
                self.features[feature_name] = value.lower() in ("1", "true", "yes")

    def get_service_config(self, service: str) -> dict[str, Any]:
        """Get configuration for a specific service."""
        return self.services.get(service, {})

    def is_feature_enabled(self, feature: str) -> bool:
        """Check if a feature flag is enabled."""
        return self.features.get(feature, False)

    def to_dict(self) -> dict[str, Any]:
        """Serialize config to dictionary."""
        return {
            "edition": self.edition,
            "ipc_mode": self.ipc_mode,
            "log_level": self.log_level,
            "services": self.services,
            "features": self.features,
            "engine_url": self.engine_url,
            "memory_url": self.memory_url,
            "economy_url": self.economy_url,
            "tools_url": self.tools_url,
        }

    def save(self, path: Optional[Path] = None) -> None:
        """Save config to file."""
        path = path or (self.paths.config_dir / "sos.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> Config:
        """Load config from file with environment overrides."""
        paths = RuntimePaths.from_env()
        path = path or (paths.config_dir / "sos.json")

        config_dict = {}
        if path.exists():
            with open(path) as f:
                config_dict = json.load(f)

        return cls(
            paths=paths,
            edition=config_dict.get("edition", "business"),
            ipc_mode=config_dict.get("ipc_mode", "http"),
            log_level=config_dict.get("log_level", "info"),
            services=config_dict.get("services", {}),
            features=config_dict.get("features", {}),
            engine_url=config_dict.get("engine_url", "http://localhost:6060"),
            memory_url=config_dict.get("memory_url", "http://localhost:6061"),
            economy_url=config_dict.get("economy_url", "http://localhost:6062"),
            tools_url=config_dict.get("tools_url", "http://localhost:6063"),
        )


# Default configurations per edition
EDITION_DEFAULTS: dict[str, dict[str, Any]] = {
    "business": {
        "features": {
            "memory_persistence": True,
            "economy_enabled": True,
            "tool_execution": True,
            "content_filter": False,
            "telemetry_enabled": False,
        },
        "services": {
            "memory": {"max_engrams": 100000},
            "economy": {"daily_limit": 10000},
        },
    },
    "education": {
        "features": {
            "memory_persistence": True,
            "economy_enabled": False,
            "tool_execution": True,
            "content_filter": True,
            "telemetry_enabled": False,
        },
        "services": {
            "memory": {"max_engrams": 10000},
            "tools": {"safe_search": True},
        },
    },
    "kids": {
        "features": {
            "memory_persistence": False,
            "economy_enabled": False,
            "tool_execution": True,
            "content_filter": True,
            "telemetry_enabled": False,
        },
        "services": {
            "tools": {
                "safe_search": True,
                "blocked_domains": ["adult", "gambling", "violence"],
                "max_response_length": 500,
            },
        },
    },
    "art": {
        "features": {
            "memory_persistence": True,
            "economy_enabled": True,
            "tool_execution": True,
            "content_filter": False,
            "creative_mode": True,
            "telemetry_enabled": False,
        },
        "services": {
            "memory": {"max_engrams": 50000},
        },
    },
}


def get_edition_config(edition: str) -> dict[str, Any]:
    """Get default configuration for an edition."""
    return EDITION_DEFAULTS.get(edition, EDITION_DEFAULTS["business"])


def create_config(edition: str = "business", **overrides) -> Config:
    """Factory function to create config with edition defaults."""
    edition_defaults = get_edition_config(edition)

    return Config(
        edition=edition,
        features={**edition_defaults.get("features", {}), **overrides.get("features", {})},
        services={**edition_defaults.get("services", {}), **overrides.get("services", {})},
        **{k: v for k, v in overrides.items() if k not in ("features", "services")},
    )
