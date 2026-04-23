"""
Sovereign Config Shim — replaces mumega.core.config dependencies.
Provides path resolution using ~/.mumega/ defaults.

URL/token config is sourced from kernel/config.py (env-var driven).
"""

from pathlib import Path
from kernel.config import MIRROR_URL, MIRROR_TOKEN, REDIS_URL, REDIS_PASSWORD  # noqa: F401

MUMEGA_DIR = Path.home() / ".mumega"
DATA_DIR = MUMEGA_DIR / "data"
TASKS_DIR = MUMEGA_DIR / "tasks"
ORGANISMS_DIR = MUMEGA_DIR / "organisms"
LOGS_DIR = MUMEGA_DIR / "logs"
BOUNTIES_DIR = MUMEGA_DIR / "bounties"

# Legacy aliases kept for backwards compatibility
REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379

# Ensure dirs exist
for d in [MUMEGA_DIR, DATA_DIR, TASKS_DIR, LOGS_DIR, BOUNTIES_DIR]:
    d.mkdir(parents=True, exist_ok=True)


def get_default_data_dir() -> Path:
    return DATA_DIR


def get_path_config():
    """Compatibility shim for modules that import get_path_config."""
    class PathConfig:
        data_dir = DATA_DIR
        tasks_dir = TASKS_DIR
        logs_dir = LOGS_DIR
        bounties_dir = BOUNTIES_DIR
        mind_token_path = DATA_DIR / "mind_token.json"
        saga_path = DATA_DIR / "saga.json"
    return PathConfig()
