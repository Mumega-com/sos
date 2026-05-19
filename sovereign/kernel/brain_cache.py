"""
Sovereign Brain Cache — Gemini Context Caching Kernel Primitive

Keeps a large, stable "world model" blob alive in Gemini's server-side cache
so each brain cycle only sends the delta (current task queue + recent outcomes),
not the full context every time.

Config knobs (all settable via env / systemd unit):
  BRAIN_CACHE_ENABLED    on/off (default: on when GEMINI_API_KEY present)
  BRAIN_CACHE_TTL        cache TTL in seconds (default: 3600)
  BRAIN_CACHE_PATH       path to persist cache_name on disk
                         (default: ~/.mumega/brain_cache.json)
  BRAIN_CACHE_SOURCES    comma-separated blob sections to include:
                         system_md,goals,tasks,cycles,agents,kb
                         (default: system_md,agents,cycles)

Blob sections:
  system_md — ~/SYSTEM.md (infrastructure + agent map)
  agents    — agent definitions from CLAUDE.md team table
  goals     — last 20 Mirror goals
  tasks     — current Squad Service task queue (pending/in_progress)
  cycles    — last 50 brain_cycles from D1 via Inkwell API
  kb        — core KB articles (first 5, summary only)
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("brain.cache")

# ── Config knobs ────────────────────────────────────────────────────────────
_ENABLED_RAW = os.environ.get("BRAIN_CACHE_ENABLED", "auto")  # auto/on/off
BRAIN_CACHE_TTL = int(os.environ.get("BRAIN_CACHE_TTL", "3600"))
BRAIN_CACHE_PATH = Path(
    os.environ.get("BRAIN_CACHE_PATH", str(Path.home() / ".mumega" / "brain_cache.json"))
)
_SOURCES_RAW = os.environ.get("BRAIN_CACHE_SOURCES", "system_md,agents,cycles")
BRAIN_CACHE_SOURCES: list[str] = [s.strip() for s in _SOURCES_RAW.split(",") if s.strip()]

# ── Gemini minimum token floor for caching (API requirement) ─────────────────
_MIN_CACHE_TOKENS = 1024  # Gemini rejects caches smaller than this


def is_enabled() -> bool:
    """Return True if brain cache is enabled."""
    raw = _ENABLED_RAW.lower()
    if raw == "off":
        return False
    if raw == "on":
        return True
    # "auto" — enable if GEMINI_API_KEY is present
    return bool(os.environ.get("GEMINI_API_KEY", ""))


# ── Disk persistence ─────────────────────────────────────────────────────────

def _load_disk() -> dict:
    """Load persisted cache metadata from disk. Returns {} on any error."""
    try:
        if BRAIN_CACHE_PATH.exists():
            return json.loads(BRAIN_CACHE_PATH.read_text())
    except Exception as e:
        logger.debug(f"brain_cache: disk read failed: {e}")
    return {}


def _save_disk(data: dict) -> None:
    """Persist cache metadata to disk."""
    try:
        BRAIN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BRAIN_CACHE_PATH.write_text(json.dumps(data, indent=2))
    except Exception as e:
        logger.warning(f"brain_cache: disk write failed: {e}")


def _clear_disk() -> None:
    """Remove stale cache metadata from disk."""
    try:
        if BRAIN_CACHE_PATH.exists():
            BRAIN_CACHE_PATH.unlink()
    except Exception:
        pass


# ── Blob assembly ─────────────────────────────────────────────────────────────

def _read_system_md() -> str:
    system_md = Path.home() / "SYSTEM.md"
    if system_md.exists():
        text = system_md.read_text()
        # Truncate at 30k chars — stable structural content, not full history
        return text[:30_000]
    return ""


def _read_agent_table() -> str:
    """Extract Team table from ~/CLAUDE.md."""
    claude_md = Path.home() / "CLAUDE.md"
    if not claude_md.exists():
        return ""
    text = claude_md.read_text()
    # Find the Team section
    start = text.find("## Team")
    if start == -1:
        return ""
    end = text.find("\n## ", start + 1)
    return text[start:end] if end != -1 else text[start:start + 3000]


def _read_recent_cycles() -> str:
    """Read last 50 brain cycles from Inkwell API (if configured)."""
    api_url = os.environ.get("INKWELL_API_URL", "").rstrip("/")
    secret = os.environ.get("INKWELL_INTERNAL_SECRET", "")
    if not api_url or not secret:
        return ""
    try:
        import urllib.request
        req = urllib.request.Request(
            f"{api_url}/api/brain",
            headers={"Authorization": f"Bearer {secret}"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        cycles = data.get("cycles", [])[:50]
        if not cycles:
            return ""
        lines = ["## Recent Brain Cycles (last 50)"]
        for c in cycles:
            ts = c.get("ts", "")[:16]
            ok = "✅" if c.get("success") else "❌"
            method = c.get("method", "")
            result = (c.get("result") or "")[:80]
            lines.append(f"{ok} {ts} [{method}] {result}")
        return "\n".join(lines)
    except Exception as e:
        logger.debug(f"brain_cache: cycles fetch failed: {e}")
        return ""


def build_blob() -> str:
    """
    Assemble the frozen world-model blob from configured sources.
    Only includes stable content that rarely changes between cycles.
    """
    parts: list[str] = [
        "# Mumega Brain World Model\n"
        "This context is stable infrastructure knowledge — "
        "use it alongside the delta context provided per cycle."
    ]

    source_fns = {
        "system_md": ("## Infrastructure (SYSTEM.md)", _read_system_md),
        "agents":    ("## Agent Team", _read_agent_table),
        "cycles":    ("## Brain Cycle History", _read_recent_cycles),
    }

    for source in BRAIN_CACHE_SOURCES:
        if source not in source_fns:
            logger.debug(f"brain_cache: unknown source '{source}', skipping")
            continue
        heading, fn = source_fns[source]
        try:
            content = fn()
            if content.strip():
                parts.append(f"{heading}\n\n{content}")
        except Exception as e:
            logger.warning(f"brain_cache: source '{source}' failed: {e}")

    return "\n\n---\n\n".join(parts)


# ── Cache lifecycle ───────────────────────────────────────────────────────────

def _validate_cache_name(cache_name: str) -> bool:
    """
    Check with Gemini API that cache_name still exists and is not expired.
    Returns True if valid.
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return False
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        cache = client.caches.get(name=cache_name)
        return cache is not None
    except Exception as e:
        logger.debug(f"brain_cache: validate failed ({cache_name[:20]}...): {e}")
        return False


def _create_gemini_cache(blob: str) -> Optional[str]:
    """Upload blob to Gemini Context Cache. Returns cache_name or None."""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    model = os.environ.get("BRAIN_MODEL", "gemini-2.5-flash")
    if not api_key:
        return None
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)
        actual_model = model if model.startswith("models/") else f"models/{model}"
        cache = client.caches.create(
            model=actual_model,
            config=types.CreateCachedContentConfig(
                systemInstruction=types.Content(
                    role="system",
                    parts=[types.Part(text=blob)],
                ),
                displayName="mumega_brain_world_model",
                ttl=f"{BRAIN_CACHE_TTL}s",
            ),
        )
        logger.info(f"brain_cache: created {cache.name[:30]}... (TTL {BRAIN_CACHE_TTL}s)")
        return cache.name
    except Exception as e:
        msg = str(e).lower()
        if "too small" in msg or "minimum" in msg:
            logger.debug("brain_cache: blob too small for Gemini caching (< 1024 tokens)")
        else:
            logger.warning(f"brain_cache: create failed: {e}")
        return None


def get_cache_name() -> Optional[str]:
    """
    Return a live Gemini cache_name for the brain world model.

    Flow:
      1. Load from disk
      2. If found + not expired → validate with API
      3. If valid → return
      4. Otherwise → build blob → create new cache → persist → return
      5. If caching disabled or Gemini unavailable → return None
    """
    if not is_enabled():
        return None

    disk = _load_disk()
    cache_name = disk.get("cache_name")
    created_at = disk.get("created_at", 0)
    age = time.time() - created_at
    # Renew 5 min before TTL expires
    if cache_name and age < (BRAIN_CACHE_TTL - 300):
        if _validate_cache_name(cache_name):
            logger.debug(f"brain_cache: hit (age {age:.0f}s / {BRAIN_CACHE_TTL}s TTL)")
            return cache_name
        logger.info("brain_cache: disk entry invalid — rebuilding")
        _clear_disk()

    # Build + create new cache
    logger.info(f"brain_cache: building blob (sources: {BRAIN_CACHE_SOURCES})")
    blob = build_blob()
    if len(blob) < 100:
        logger.warning("brain_cache: blob too small, skipping cache")
        return None

    new_name = _create_gemini_cache(blob)
    if new_name:
        _save_disk({"cache_name": new_name, "created_at": time.time(), "sources": BRAIN_CACHE_SOURCES})
    return new_name


def invalidate() -> None:
    """Force-invalidate the cache (clears disk; next cycle rebuilds)."""
    _clear_disk()
    logger.info("brain_cache: invalidated")
