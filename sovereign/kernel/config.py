"""
Sovereign Kernel Config — centralized config loaded from env vars with sensible defaults.

All service URLs, tokens, and paths are defined here.
Import from here instead of hardcoding in individual modules.
"""

import os
from pathlib import Path

# Load secrets from ~/.env.secrets if env vars are not already set.
# This mirrors how bus scripts source the file at runtime.
_secrets_file = Path.home() / ".env.secrets"
if _secrets_file.exists():
    for _line in _secrets_file.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _key, _, _val = _line.partition("=")
        _key = _key.strip()
        if _key and _key not in os.environ:
            os.environ[_key] = _val.strip()

MIRROR_URL = os.getenv("MIRROR_URL", "http://localhost:8844")
MIRROR_TOKEN = os.getenv("MIRROR_TOKEN", "sk-mumega-internal-001")

SQUAD_URL = os.getenv("SQUAD_URL", "http://localhost:8060")

SOS_ENGINE_URL = os.getenv("SOS_ENGINE_URL", "http://localhost:6060")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

DISCORD_SCRIPT = os.getenv("DISCORD_SCRIPT", "/home/mumega/scripts/discord-reply.sh")

SOVEREIGN_DATA_DIR = os.getenv("SOVEREIGN_DATA_DIR", str(Path.home() / ".mumega"))
SOVEREIGN_SQUADS_DIR = os.getenv("SOVEREIGN_SQUADS_DIR", "/home/mumega/SOS/sovereign/.squads")
SOVEREIGN_PLANS_DIR = os.getenv("SOVEREIGN_PLANS_DIR", "/home/mumega/SOS/sovereign/.plans")

# ── Project pause gate ───────────────────────────────────────────────────────
# Comma-separated list of project slugs cortex won't score and loop won't claim.
# Set via env: PAUSED_PROJECTS=dnu,trop
# Remove a project from the list to resume it.
_paused_raw = os.getenv("PAUSED_PROJECTS", "")
PAUSED_PROJECTS: frozenset[str] = frozenset(
    p.strip().lower() for p in _paused_raw.split(",") if p.strip()
)

# ── Brain cache knobs (see kernel/brain_cache.py) ────────────────────────────
BRAIN_CACHE_ENABLED = os.getenv("BRAIN_CACHE_ENABLED", "auto")
BRAIN_CACHE_TTL = int(os.getenv("BRAIN_CACHE_TTL", "3600"))
BRAIN_CACHE_PATH = Path(os.getenv("BRAIN_CACHE_PATH", str(Path.home() / ".mumega" / "brain_cache.json")))
BRAIN_CACHE_SOURCES = os.getenv("BRAIN_CACHE_SOURCES", "system_md,agents,cycles")
