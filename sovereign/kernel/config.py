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

# ── Brain tenant scope ───────────────────────────────────────────────────────
# Controls which project slugs this brain instance perceives, scores, and may
# create tasks for. Used for per-customer and per-department brain isolation.
#
# BRAIN_TENANT_SCOPE:
#   "*"             → all projects (default, legacy flat-scope behaviour)
#   "gaf"           → GAF only (SaaS customer brain)
#   "gaf,pecb"      → multiple exact slugs (multi-project scope)
#   "mkt-biz-*"     → prefix wildcard: matches mkt-biz-12345, mkt-biz-99999, etc.
#   "mumega,mkt-biz-*" → exact + prefix combined
#
# Entries ending in "*" are prefix patterns — any project starting with the
# prefix (before the "*") matches. Useful for namespaced outreach lead slugs.
#
# None sentinel → all projects allowed (no scope filter). frozenset → scoped.
_scope_raw = os.getenv("BRAIN_TENANT_SCOPE", "*").strip()
if _scope_raw == "*":
    BRAIN_TENANT_SCOPE: frozenset[str] | None = None
    _SCOPE_EXACT: frozenset[str] = frozenset()
    _SCOPE_PREFIXES: tuple[str, ...] = ()
else:
    _scope_entries = [s.strip().lower() for s in _scope_raw.split(",") if s.strip()]
    _SCOPE_EXACT = frozenset(e for e in _scope_entries if not e.endswith("*"))
    _SCOPE_PREFIXES = tuple(e[:-1] for e in _scope_entries if e.endswith("*"))
    BRAIN_TENANT_SCOPE = frozenset(_scope_entries)


def project_in_brain_scope(project: str) -> bool:
    """Return True if the project is within this brain instance's tenant scope.

    Checks exact matches first (O(1)), then prefix patterns (O(k) where k is
    the number of prefix patterns — typically very small). PAUSED_PROJECTS is
    checked separately by callers; this function only checks BRAIN_TENANT_SCOPE.
    """
    if BRAIN_TENANT_SCOPE is None:
        return True  # global scope — all projects visible
    p = project.strip().lower()
    if p in _SCOPE_EXACT:
        return True
    return any(p.startswith(prefix) for prefix in _SCOPE_PREFIXES)


# BRAIN_SCOPE_TYPE:
#   "global"        → no tenant isolation (default, legacy cortex-events.service)
#   "saas_customer" → single SaaS customer's isolated brain (auto-assigned at knight mint)
#   "department"    → internal department brain (mumega infra, content ops, etc.)
BRAIN_SCOPE_TYPE: str = os.getenv("BRAIN_SCOPE_TYPE", "global")

# BRAIN_TOKEN_BUDGET:
#   Maximum LLM tokens consumed per brain cycle (perceive+think+act combined).
#   0 = unlimited. When the budget is reached, the cycle falls back to the
#   hardcoded safe-default action (no further LLM calls that cycle).
#   SaaS customers default to 4000 (set in cortex-events@.service template).
#   Internal departments and global brain default to 0 (unlimited).
BRAIN_TOKEN_BUDGET: int = int(os.getenv("BRAIN_TOKEN_BUDGET", "0"))

# BRAIN_DELEGATED_PROJECTS:
#   Projects that have dedicated brain instances and must be EXCLUDED from the
#   global brain (cortex-events.service). Comma-separated slugs.
#   brain-assign.py updates this automatically when a new customer brain is assigned.
#   The global brain reads this to avoid double-scoring delegated projects.
_delegated_raw = os.getenv("BRAIN_DELEGATED_PROJECTS", "")
BRAIN_DELEGATED_PROJECTS: frozenset[str] = frozenset(
    p.strip().lower() for p in _delegated_raw.split(",") if p.strip()
)

# ── Brain cache knobs (see kernel/brain_cache.py) ────────────────────────────
BRAIN_CACHE_ENABLED = os.getenv("BRAIN_CACHE_ENABLED", "auto")
BRAIN_CACHE_TTL = int(os.getenv("BRAIN_CACHE_TTL", "3600"))
BRAIN_CACHE_PATH = Path(os.getenv("BRAIN_CACHE_PATH", str(Path.home() / ".mumega" / "brain_cache.json")))
BRAIN_CACHE_SOURCES = os.getenv("BRAIN_CACHE_SOURCES", "system_md,agents,cycles")
