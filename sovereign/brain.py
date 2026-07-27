#!/usr/bin/env python3
"""
Sovereign Brain — The Living Loop

This is the script that makes Mumega alive.

Every cycle:
  1. PERCEIVE  — read goals, objections, system state (Mirror + metabolism)
  2. THINK     — pick highest-utility action (Gemma 4 31B, free)
  3. ACT       — execute the action (Haiku/Flash, free)
  4. REMEMBER  — store result in Mirror, update goal progress
  5. REPORT    — post to Discord #control
  6. SLEEP     — wait for next cycle

Each cognitive function uses a different model:
  Prefrontal (planning):  Gemma 4 31B (free, excellent reasoning)
  Motor (execution):      gpt-4o-mini via GitHub (free, fast)
  Memory (recall):        Mirror API (free, local embeddings)
  Habits (patterns):      HiveEvolution recipes (free, learned)

Run:
  python3 brain.py              # one cycle
  python3 brain.py --daemon     # continuous (every 2 hours)
"""

import os
import sys
import json
import time
import logging
import unicodedata
import requests
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [BRAIN] %(message)s")
logger = logging.getLogger("brain")

SOVEREIGN_DIR = Path(__file__).resolve().parent
if str(SOVEREIGN_DIR) not in sys.path:
    sys.path.insert(0, str(SOVEREIGN_DIR))

# ============================================
# Model endpoints (the different brain regions)
# ============================================

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GOOGLE_CLOUD_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "mumega-com")
GOOGLE_CLOUD_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")

# ── Brain config knobs (settable via env / systemd unit) ──────────────────────
# BRAIN_MODEL: Gemini model for prefrontal_think + _generate_content fallback.
#   Default: gemini-2.5-flash  (was hardcoded gemma-4-31b-it)
BRAIN_MODEL = os.environ.get("BRAIN_MODEL", "gemini-2.5-flash")

# BRAIN_CONTENT_MODE: controls whether the brain autonomously posts blog content.
#   "on"  — enabled (default legacy behaviour)
#   "off" — disabled; fallback emits health_check instead of post_content
#   "log" — dry-run; logs what would have been posted without actually posting
BRAIN_CONTENT_MODE = os.environ.get("BRAIN_CONTENT_MODE", "on").lower()

_SUPPORTED_BRAIN_METHODS = {
    "create_task",
    "post_content",
    "send_outreach",
    "fix_code",
    "research",
    "health_check",
}
_STALE_TASK_MARKERS = (
    "test quest",
    "global quest",
    "test qv quest",
    "mocked extract quest",
    "tc-g53",
    "fk test quest",
    "delete_tasks_by_pattern",
)

from kernel.config import (
    MIRROR_URL, MIRROR_TOKEN, SQUAD_URL, SOS_ENGINE_URL,
    BRAIN_TENANT_SCOPE, BRAIN_SCOPE_TYPE, BRAIN_TOKEN_BUDGET,
    MUPOT_MCP_URL, MUPOT_BRAIN_TOKEN,
)
# ── MemoryPort — memory I/O routes through this adapter (#267 K1) ──────────
# All four /store and /search call sites in this file are ported:
#   - hippocampus_recall()       — goals + objection searches -> _memory_port.search_sync()
#   - remember()                 — cycle engram write          -> _memory_port.remember_sync()
#   - motor_execute/post_content — content engram write        -> _memory_port.remember_sync()
#
# Invariant: no raw requests.post(MIRROR_URL/store or /search) in this file.
# /tasks calls are a separate task-board concern (a different port) and are
# explicitly out of scope for this K1 slice — left untouched.
from kernel.memory_adapter import memory as _memory_port

# ── Cycle token counter ───────────────────────────────────────────────────────
# Reset at the start of each cycle. Accumulates tokens across all LLM calls
# within one perceive-think-act pass. Enforced against BRAIN_TOKEN_BUDGET.
_cycle_tokens: int = 0


def _record_tokens(count: int) -> None:
    """Add `count` tokens to the current cycle total."""
    global _cycle_tokens
    _cycle_tokens += count


def _budget_exhausted() -> bool:
    """True if the token budget is set and the cycle has exceeded it."""
    return BRAIN_TOKEN_BUDGET > 0 and _cycle_tokens >= BRAIN_TOKEN_BUDGET


def _assert_in_scope(project: str) -> None:
    """Raise if the brain tries to create a task outside its declared tenant scope.

    This is a hard security boundary — not a warning, not a log-and-continue.
    A scope violation means the brain is hallucinating work for a customer it
    does not own. The cycle must stop, not proceed with a wrong project assignment.
    """
    if BRAIN_TENANT_SCOPE is None:
        return  # global scope — all projects allowed
    normalized = project.strip().lower()
    if normalized not in BRAIN_TENANT_SCOPE:
        raise ValueError(
            f"Brain scope violation: project={project!r} is not in "
            f"BRAIN_TENANT_SCOPE={sorted(BRAIN_TENANT_SCOPE)!r}. "
            f"BRAIN_SCOPE_TYPE={BRAIN_SCOPE_TYPE!r}. "
            "This brain instance may not create tasks for this project."
        )


# ── Colony capability gate (agent dimension) — S180-A ─────────────────────────
# Defense-in-depth alongside _assert_in_scope (which is a NO-OP for a GLOBAL
# colony brain). The colony brain legitimately sees all tenants, so its real
# fault mode is cross-tenant CAPABILITY APPLICATION — dispatching a tenant-bound
# agent for another tenant's goal (the observed "use viamar-ceo-strategy for a
# Mumega goal" bleed). The authoritative agent->home-tenant map lives in
# sos.kernel.agent_registry (AgentDef.project), which this brain CANNOT import
# (separate module root), so we resolve it over HTTP from the squad service
# /agents roster. The SKILL-dimension gate is enforced separately by the squad
# service, which owns squad_skills.tenant_id.

# Failsafe map of the known tenant-bound agents — used ONLY when the live
# /agents resolver is unavailable (cold start / outage) so these sensitive
# identities stay GATED even without the live roster. The live roster
# (sos.kernel.agent_registry) is the source of truth whenever reachable; this is
# the deny-side default, not a parallel registry.
# MUST be kept in sync with the tenant-bound agents in sos.kernel.agent_registry
# (AgentDef.project != ""): a NEW tenant-bound agent not listed here goes
# UNGATED during a cold-start resolver outage. (Athena G180-A advisory 2.)
_TENANT_BOUND_FALLBACK: dict[str, str] = {
    "sol": "realm-of-patterns",
    "dandan": "dentalnearyou",
    "gaf": "gaf",
}

_AGENT_HOME_CACHE: dict[str, str] = {}
_AGENT_HOME_CACHE_TS: float = 0.0
_AGENT_HOME_TTL = 300.0  # seconds

# Zero-width / invisible characters `.strip()` does not remove: zero-width
# space, zero-width non-joiner, zero-width joiner, BOM/zero-width no-break
# space. Part of the P2-D fix below.
_ZERO_WIDTH_CHARS = ("​", "‌", "‍", "﻿")


def _normalize_agent_subject(agent: object) -> str | None:
    """Normalize an untrusted `agent` value into a roster lookup key, or
    None if it isn't one.

    P2-D fix (sos-205-47f5f8c2 gate-3): `agent` originates from the LLM
    decision JSON (`action.get("agent", ...)`) and used to reach the
    capability gate through a bare `str(agent).strip().lower()`. That
    defended against nothing: a zero-width space, a Turkish dotless-i
    homoglyph, a trailing dot/slash, an embedded space, or a non-str value
    (None / 0 / a list / a dict) each turned a KNOWN tenant-bound agent name
    into a roster MISS — and `_agent_home_tenant` treats a miss as "no home
    tenant = ungated colony agent". Two non-str cases (list, dict) are worse
    than a silent miss: `_agent_available`'s `agent not in _AGENT_SESSION`
    check on an unhashable value (list/dict) raises TypeError uncaught,
    crashing the whole brain cycle before this gate is even reached.

    NFKC + stripped zero-width chars + case-fold closes the mutation class
    for values that ARE meant to match a roster entry. It does NOT resolve
    genuine Unicode confusables (e.g. dotless-i is a distinct codepoint, not
    NFKC-equivalent to 'i') — those still normalize to a string that simply
    doesn't match anything in the roster, which is the SAME safe outcome an
    honestly-unknown agent name already gets today (unknown → colony/shared,
    per `_agent_home_tenant`'s documented contract). This function only
    upgrades "reachable but silently wrong" to "handled the same way as any
    other unrecognized string" and rejects non-str/empty input outright
    instead of coercing it with `str(...)`.

    IMPORTANT: this normalization does NOT make the capability gate the
    enforcing layer. `_agent_available` (`_AGENT_SESSION`, an exact-match
    whitelist, default-deny) is what actually decides dispatchability — see
    motor_execute, which checks it before any gate call. Treating a gate as
    the enforcer instead of the roster was the exact vacuous-gate mistake
    already made once on this code path (sos-205-a7c2fc44).
    """
    if not isinstance(agent, str):
        return None
    normalized = unicodedata.normalize("NFKC", agent)
    for ch in _ZERO_WIDTH_CHARS:
        normalized = normalized.replace(ch, "")
    normalized = normalized.strip().lower()
    return normalized or None


def _agent_home_tenant(agent: str) -> str | None:
    """Resolve an agent's home tenant (its AgentDef.project) via the squad
    service /agents roster. Returns the normalized home project, or None for
    shared/colony agents (no project binding) and genuinely unknown agents.

    Caching: refreshed every _AGENT_HOME_TTL. On a refresh failure a previously
    fetched (stale) map is RETAINED, so a transient resolver outage does not open
    the gate. On a COLD-START failure (cache never populated) we fall back to the
    static _TENANT_BOUND_FALLBACK set, so the known tenant-bound agents stay
    gated even when the roster cannot be fetched; only genuinely unknown agents
    go ungated. The gate is one of several defense-in-depth layers.
    """
    global _AGENT_HOME_CACHE, _AGENT_HOME_CACHE_TS
    now = time.time()
    if not _AGENT_HOME_CACHE or (now - _AGENT_HOME_CACHE_TS) > _AGENT_HOME_TTL:
        try:
            r = requests.get(f"{SQUAD_URL}/agents", headers=SQUAD_HEADERS, timeout=5)
            r.raise_for_status()
            payload = r.json()
            rows = payload.get("agents", payload) if isinstance(payload, dict) else payload
            mapping = {
                str(row.get("name", "")).strip().lower(): str(row.get("project", "") or "").strip().lower()
                for row in rows
                if str(row.get("name", "")).strip()
            }
            if mapping:
                _AGENT_HOME_CACHE = mapping
                _AGENT_HOME_CACHE_TS = now
        except Exception as exc:
            if not _AGENT_HOME_CACHE:
                logger.error(f"[capability-gate] agent resolver cold-start failed — failing safe to static tenant-bound set: {exc}")
            else:
                logger.warning(f"[capability-gate] agent resolver refresh failed — using stale roster: {exc}")
    # P2-D fix: normalize (see _normalize_agent_subject). A non-str/empty
    # subject has no home tenant to report — the caller-side default-deny
    # roster check is what actually gates dispatch of such a value; this
    # function's contract is "resolve a home tenant or None", not "decide
    # dispatchability".
    key = _normalize_agent_subject(agent) or ""
    if _AGENT_HOME_CACHE:
        home = _AGENT_HOME_CACHE.get(key, "")
    else:
        # Resolver never succeeded — fail SAFE for the known tenant-bound agents.
        home = _TENANT_BOUND_FALLBACK.get(key, "")
    return home or None


def _assert_agent_in_tenant(agent: str, project: str) -> None:
    """Raise if a tenant-bound agent is dispatched for a different tenant.

    Shared/colony agents (no home tenant) may act for any project. A tenant-bound
    agent (e.g. sol -> therealmofpatterns) may act only for its own tenant. This
    is the colony brain's load-bearing cross-tenant guard; scoped per-tenant
    brains cannot reach this situation at all.
    """
    home = _agent_home_tenant(agent)
    if not home:
        return  # shared/colony or unknown agent — allowed for any project
    if normalize_project(home) != normalize_project(project):
        raise ValueError(
            f"Capability scope violation: agent {agent!r} belongs to tenant "
            f"{home!r} but this directive targets project {project!r}. A "
            f"tenant-bound agent may only act for its own tenant."
        )


def _capability_block(assignee: str | None, project: str) -> dict | None:
    """Gate a single dispatch. Returns a failure dict if dispatching `assignee`
    for `project` violates the colony capability gate, else None.

    MUST be called with the FINAL dispatched (assignee, project) at each branch —
    not once up front — because branches recompute the assignee/project after the
    PROJECT_LEADS reroute (e.g. send_outreach flips to dentalnearyou/dandan on a
    'dent' substring). Gating the final pair is the only correct subject.
    """
    try:
        _assert_agent_in_tenant(assignee or "", project)
        return None
    except ValueError as cap_err:
        logger.error(f"[capability-gate] {cap_err}")
        return {"success": False, "result": str(cap_err)}


def _blocked_stale_cleanup_reason(action: dict[str, object]) -> str | None:
    """Return a reason when a brain action targets retired quest-fixture cleanup.

    These directives were valid during old test-fixture cleanup windows but are
    now stale. If the model sees the historical rows and tries to recreate the
    cleanup loop, we fail closed here instead of enqueuing more noise.
    """
    haystack = " ".join(
        str(action.get(key, ""))
        for key in ("action", "goal_id", "agent", "method", "details")
    ).lower()
    if any(marker in haystack for marker in _STALE_TASK_MARKERS):
        return "retired quest-fixture cleanup directive"
    return None

MIRROR_HEADERS = {"Authorization": f"Bearer {MIRROR_TOKEN}", "Content-Type": "application/json"}

SQUAD_TOKEN = os.environ.get("SOS_SQUAD_TOKEN", "")
SQUAD_HEADERS = {
    "Authorization": f"Bearer {SQUAD_TOKEN}",
    "Content-Type": "application/json",
}

ENGINE_URL = SOS_ENGINE_URL

DISCORD_CONTROL = "1489684648564101391"

LABEL_SQUAD_MAP = {
    "seo": "seo",
    "code": "dev",
    "fix": "dev",
    "outreach": "outreach",
    "content": "content",
    "blog": "content",
    "deploy": "ops",
    "infra": "ops",
}

PROJECT_LEADS = {
    "dentalnearyou": "dandan",
    "dnu": "dandan",
    "gaf": "worker",
    "realm-of-patterns": "sol",
    "trop": "sol",
    "viamar": "worker",
    "stemminds": "worker",
    "pecb": "worker",
    "prefrontal": "worker",
    "musicalunicorn": "worker",
    "letsbefrank": "worker",
    "digid": "worker",
}


def normalize_project(project: str) -> str:
    aliases = {
        "dnu": "dentalnearyou",
        "trop": "realm-of-patterns",
        # The agent registry stores TROP's project as "therealmofpatterns"
        # (no hyphens) while goals/squads use "trop"/"realm-of-patterns".
        # Canonicalize all three to one tenant id so the S180-A capability gate
        # does not falsely block sol on legitimate same-tenant work.
        "therealmofpatterns": "realm-of-patterns",
        "dental": "dentalnearyou",
    }
    return aliases.get(project, project)


def resolve_squad(labels: list[str], project: str) -> str | None:
    normalized_project = normalize_project(project)
    normalized_labels = [str(label).strip().lower() for label in labels if str(label).strip()]

    for label in normalized_labels:
        for needle, squad_id in LABEL_SQUAD_MAP.items():
            if needle in label:
                return squad_id

    if normalized_project == "dentalnearyou":
        return "seo"
    return None


def prefrontal_think(context: str) -> str:
    """
    PREFRONTAL CORTEX — Planning & Decision Making
    Model: BRAIN_MODEL (default: gemini-2.5-flash)

    Given system state delta → decide what to do next.
    Uses Gemini Context Cache for the stable world model when available —
    only the current delta (task queue + recent outcomes) is sent per cycle.
    """
    if _budget_exhausted():
        logger.warning(
            f"prefrontal: token budget exhausted ({_cycle_tokens}/{BRAIN_TOKEN_BUDGET}) — "
            "falling back to safe default action"
        )
        return fallback_think(context)

    try:
        from google import genai
        from kernel.brain_cache import get_cache_name

        # Vertex AI ADC path — billed against $400 Vertex budget, no API key needed
        client = genai.Client(
            vertexai=True,
            project=GOOGLE_CLOUD_PROJECT,
            location=GOOGLE_CLOUD_LOCATION,
        )
        cache_name = get_cache_name()

        decision_prompt = f"""You are the Sovereign Brain of Mumega — an autonomous AI operating system.

Your job: look at the current system state delta, active goals, and objections.
Pick the ONE highest-impact action that can be done RIGHT NOW with available tools.

Rules:
- Pick actions that are CONCRETE and EXECUTABLE (not "plan to do X" but "do X")
- Prefer actions that resolve objections blocking high-priority goals
- Prefer actions with zero or low token cost (use free models when possible)
- If nothing is urgent, pick maintenance work (content, outreach, memory cleanup)
- Do NOT create tasks to delete historical quest/test fixtures or prior brain cleanup tasks
- Output ONLY a JSON object, no explanation

CURRENT CYCLE DELTA:
{context}

Respond with EXACTLY this JSON format:
{{
  "action": "one-line description of what to do",
  "goal_id": "which goal this advances (or 'maintenance')",
  "agent": "which agent should do it (kasra/system)",
  "method": "how to do it (create_task/post_content/send_outreach/fix_code/research)",
  "details": "specific instructions for the executing agent",
  "expected_progress": 0.1,
  "risk": 0.1
}}"""

        if cache_name:
            # Stable world model is in the cache — only send the delta
            from google.genai import types
            response = client.models.generate_content(
                model=BRAIN_MODEL,
                contents=decision_prompt,
                config=types.GenerateContentConfig(
                    cached_content=cache_name,
                ),
            )
            logger.info("prefrontal: using Gemini context cache")
        else:
            # No cache — send full context inline (fallback)
            response = client.models.generate_content(
                model=BRAIN_MODEL,
                contents=decision_prompt,
            )

        text = response.text.strip()

        # Track token usage for budget enforcement
        try:
            usage = response.usage_metadata
            tokens = getattr(usage, "total_token_count", 0) or 0
            _record_tokens(tokens)
            if BRAIN_TOKEN_BUDGET > 0:
                logger.info(f"prefrontal: tokens={tokens} cycle_total={_cycle_tokens}/{BRAIN_TOKEN_BUDGET}")
        except Exception:
            pass

        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        return text

    except Exception as e:
        logger.error(f"Prefrontal failed: {e}")
        return fallback_think(context)


def fallback_think(context: str) -> str:
    """Fallback: try GitHub Models, then Gemini, then hardcoded safe default."""
    # Try GitHub Models first
    if GITHUB_TOKEN:
        try:
            from openai import OpenAI
            client = OpenAI(
                base_url="https://models.inference.ai.azure.com",
                api_key=GITHUB_TOKEN,
            )
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{
                    "role": "user",
                    "content": f"Given this system state, pick ONE concrete action to advance the goals. Respond with JSON only: action, goal_id, agent, method, details, expected_progress, risk.\n\n{context[:3000]}"
                }],
                max_tokens=300,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Fallback GitHub failed: {e}")

    # Vertex AI ADC as secondary fallback
    try:
        from google import genai
        client = genai.Client(
            vertexai=True,
            project=GOOGLE_CLOUD_PROJECT,
            location=GOOGLE_CLOUD_LOCATION,
        )
        response = client.models.generate_content(
            model=BRAIN_MODEL,
            contents=f"Given this system state, pick ONE concrete action. Respond with JSON only: action, goal_id, agent, method, details, expected_progress (float), risk (float).\n\n{context[:3000]}",
        )
        return response.text.strip()
    except Exception as e:
        logger.error(f"Fallback Vertex failed: {e}")

    # Hard fallback — safe default action (no token cost)
    # Respects BRAIN_CONTENT_MODE: if "off", emit health_check instead of post_content
    if BRAIN_CONTENT_MODE == "off":
        return json.dumps({
            "action": "System health check and report",
            "goal_id": "maintenance",
            "agent": "system",
            "method": "health_check",
            "details": "Check all services and report status",
            "expected_progress": 0.01,
            "risk": 0.0,
        })
    return json.dumps({
        "action": "Post daily content for Mumega blog",
        "goal_id": "maintenance",
        "agent": "system",
        "method": "post_content",
        "details": "Generate and publish a blog post about AI automation",
        "expected_progress": 0.05,
        "risk": 0.0,
    })




def _task_exists(title: str, agent: str) -> bool:
    """Check if a task with a similar title already exists (not canceled).
    Checks BOTH Mirror and Squad Service to prevent cross-system duplicates."""
    prefix = title[:40].lower()

    # Check Mirror
    try:
        r = requests.get(
            f"{MIRROR_URL}/tasks",
            headers=MIRROR_HEADERS,
            params={"agent": agent, "limit": 50},
            timeout=10,
        )
        data = r.json()
        tasks = data.get("tasks", data) if isinstance(data, dict) else data
        for t in tasks:
            if t.get("status") in ("canceled",):
                continue
            if t.get("title", "")[:40].lower() == prefix:
                logger.info(f"Duplicate task found in Mirror, skipping: {title}")
                return True
    except Exception as e:
        logger.warning(f"Mirror _task_exists check failed: {e}")

    # Check Squad Service
    try:
        r = requests.get(f"{SQUAD_URL}/tasks", headers=SQUAD_HEADERS, timeout=10)
        data = r.json()
        tasks = data if isinstance(data, list) else data.get("tasks", [])
        for t in tasks:
            if t.get("status") in ("canceled",):
                continue
            if t.get("title", "")[:40].lower() == prefix:
                logger.info(f"Duplicate task found in Squad Service, skipping: {title}")
                return True
    except Exception as e:
        logger.warning(f"Squad _task_exists check failed: {e}")

    return False


# ── Layer-C task-creation governor (S-BRAIN-GOV-VPS) ──────────────────────────
# Sliding-window cap on how many tasks the brain may CREATE per window. Bounds
# ANY runaway — including the mutating-title self-recursion that the title-prefix
# dedup in _task_exists() cannot catch (a loop whose title changes every cycle
# never re-matches the prefix, so dedup lets it through; the governor still stops
# it). Caps the side-effecting/expensive part (task creation) WITHOUT stopping
# cognition — the brain keeps deciding, it just can't spawn unbounded work.
# Env-tunable so ops can adjust without a code change.
_TASK_GOVERNOR_WINDOW_SEC = int(os.getenv("BRAIN_TASK_GOVERNOR_WINDOW_SEC", "600"))
_TASK_GOVERNOR_MAX = int(os.getenv("BRAIN_TASK_GOVERNOR_MAX", "12"))
_recent_task_creations: deque = deque()


def _task_governor_allows() -> bool:
    """Return True if a new task may be created under the sliding-window cap.
    Prunes expired entries, records the creation on success. Returns False (and
    records nothing) when the window is saturated, so the caller backs off."""
    now = time.time()
    cutoff = now - _TASK_GOVERNOR_WINDOW_SEC
    while _recent_task_creations and _recent_task_creations[0] < cutoff:
        _recent_task_creations.popleft()
    if len(_recent_task_creations) >= _TASK_GOVERNOR_MAX:
        return False
    _recent_task_creations.append(now)
    return True


# Agent name → tmux session name (empty string = system/no session needed).
# Active roster per Hadi directive 2026-07-27: kasra + system only. Paused
# agents (athena/river/sol/dandan) are intentionally absent — dispatching to
# them produced the "no tmux session" self-investigation loop.
_AGENT_SESSION: dict[str, str] = {
    "kasra": "kasra",
    "system": "",
}


def _agent_available(agent: str) -> bool:
    """Return True if the agent is on the active roster and reachable.

    Default-deny: an agent not in _AGENT_SESSION is NOT dispatchable,
    regardless of what the model proposes. A failed tmux probe also counts
    as unavailable — assuming available on error re-opens the ghost loop.
    """
    import subprocess
    if agent not in _AGENT_SESSION:
        return False
    session = _AGENT_SESSION[agent]
    if not session:
        return True  # system — no session requirement
    try:
        result = subprocess.run(
            ["tmux", "has-session", "-t", session],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def _mupot_dispatch_task(squad_id: str, title: str, description: str, priority: str, labels: list) -> dict:
    """
    Create a task on mupot's REAL, live board (agent-bound token, the
    'sovereign' identity minted 2026-07-22) instead of the legacy SQUAD_URL
    board that mupot's own operator loop never reads. Deliberately supplies
    NO assignee -- mupot's own routeUnassignedWork (src/tasks/effort-route.ts)
    picks the builder from the live, current roster (kasra/cursor/codex/agy/
    kayhermes), not brain's own free-text guess against a stale hardcoded
    hint (the #490 root cause). Returns the same {"success","result","task_id"}
    shape the legacy SQUAD_URL callers already expect, so callers don't change.
    """
    if not MUPOT_MCP_URL or not MUPOT_BRAIN_TOKEN:
        logger.warning("mupot dispatch skipped: MUPOT_MCP_URL/MUPOT_BRAIN_TOKEN not configured")
        return {"success": False, "result": "mupot not configured", "task_id": None}
    done_when = f"Task '{title[:80]}' is completed, with a receipt reflecting success or failure."
    try:
        r = requests.post(
            MUPOT_MCP_URL,
            headers={"Authorization": f"Bearer {MUPOT_BRAIN_TOKEN}", "Content-Type": "application/json"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "task_create",
                    "arguments": {
                        "squad_id": squad_id,
                        "title": title,
                        "body": f"{description}\n\n[brain-generated, priority={priority}, labels={','.join(labels)}]",
                        "done_when": done_when,
                    },
                },
            },
            timeout=10,
        )
        data = r.json()
        content = data.get("result", {}).get("content", [{}])
        text = content[0].get("text", "{}") if content else "{}"
        payload = json.loads(text)
        if not payload.get("ok"):
            logger.error(f"mupot task_create failed: {payload}")
            return {"success": False, "result": f"mupot task_create failed: {payload}", "task_id": None}
        task = payload.get("result", {}).get("task", {})
        task_id = task.get("id", "?")
        return {"success": True, "result": f"mupot task created: {task_id} (squad={squad_id})", "task_id": task_id}
    except Exception as e:
        logger.error(f"mupot dispatch exception: {e}")
        return {"success": False, "result": f"mupot dispatch failed: {e}", "task_id": None}


def motor_execute(action: dict) -> dict:
    """
    MOTOR CORTEX — Execution
    Takes the decision from prefrontal and executes it.
    Returns result dict.
    """
    method = action.get("method", "")
    details = action.get("details", "")
    action_title = action.get("action", "")

    # P2-D fix (sos-205-47f5f8c2 gate-3): `agent` is untrusted LLM-decision
    # JSON and flows into every `_capability_block`/`_agent_home_tenant` call
    # below, plus the `_agent_available` roster check. Normalize it HERE,
    # once, before anything downstream sees it — see
    # `_normalize_agent_subject` for the full mutation-class rationale. A
    # non-str/empty subject (None, 0, a list, a dict — none of these are a
    # legitimate "agent" field) is rejected outright rather than silently
    # coerced via `str(agent)`: unhashable values (list/dict) used to reach
    # `_agent_available`'s `agent not in _AGENT_SESSION` dict check and raise
    # an uncaught TypeError there, crashing the whole cycle. Skipping here
    # returns the same calm, non-error shape every other "nothing to do this
    # cycle" branch in this function uses.
    raw_agent = action.get("agent", "system")
    agent = _normalize_agent_subject(raw_agent)
    if agent is None:
        logger.info(f"Brain proposed a non-string/empty agent subject ({raw_agent!r}) — skipping: {action_title[:60]}")
        return {
            "success": True,
            "skipped": True,
            "result": "Decision-layer proposed an invalid agent subject; task intentionally not dispatched. This is expected when the model's JSON is malformed — do not investigate.",
        }

    blocked_reason = _blocked_stale_cleanup_reason(action)
    if blocked_reason is not None:
        logger.warning(f"Blocked brain action: {blocked_reason}: {action_title[:120]}")
        return {"success": True, "result": f"Skipped stale brain directive: {blocked_reason}"}

    if method not in _SUPPORTED_BRAIN_METHODS:
        # skipped=True + non-error phrasing: a hallucinated method name is a
        # decision-layer miss, not a system fault. Error-shaped text here fed
        # "investigate unsupported method" proposals in following cycles.
        return {"success": True, "skipped": True, "result": f"Method '{method}' is not in the supported set; action intentionally not executed. Pick only from the documented methods — do not investigate."}

    # Agent availability check — skip if the target agent has no running session
    if not _agent_available(agent):
        if agent not in _AGENT_SESSION:
            logger.info(f"Agent '{agent}' not on active roster — skipping task: {action_title[:60]}")
            # Deliberate, not an error: paused agents are expected to be absent.
            # Phrasing avoids "error"/"no tmux session" so the next brain cycle
            # does not propose investigating its own roster policy.
            return {"success": True, "result": f"Agent '{agent}' is paused by roster policy; task intentionally not dispatched. This is expected — do not investigate."}
        logger.info(f"Agent '{agent}' has no active session — skipping task: {action_title[:60]}")
        return {"success": True, "result": f"Agent '{agent}' unavailable (no tmux session). Task skipped."}

    # Dedup check — don't create tasks that already exist (all task-creating methods)
    _task_creating_methods = ("create_task", "send_outreach", "fix_code", "research")
    if method in _task_creating_methods:
        project_for_dedupe = normalize_project(action.get("goal_id", "mumega").replace("goal_", ""))
        # Derive the actual title that would be stored, matching each branch below
        if method == "send_outreach":
            check_title = f"Outreach: {action_title}"
            check_agent = PROJECT_LEADS.get(project_for_dedupe, agent)
        elif method == "fix_code":
            check_title = f"Fix: {action_title}"
            check_agent = PROJECT_LEADS.get(project_for_dedupe, "kasra")
        elif method == "research":
            check_title = f"Research: {action_title}"
            check_agent = "river"
        else:
            check_title = action_title
            check_agent = PROJECT_LEADS.get(project_for_dedupe, agent)
        if _task_exists(check_title, check_agent):
            return {"success": True, "result": f"Duplicate task skipped for {check_agent}: {check_title[:60]}"}
        # Layer-C governor: bound runaway task creation (incl. mutating-title
        # self-recursion that the prefix dedup above misses). Backs off this
        # cycle when the window is saturated; cognition continues next cycle.
        if not _task_governor_allows():
            logger.warning(
                f"[governor] task-creation cap hit "
                f"({_TASK_GOVERNOR_MAX}/{_TASK_GOVERNOR_WINDOW_SEC}s) — backing off: {check_title[:60]}"
            )
            return {"success": True, "result": "Governor: task-creation rate cap reached, backing off this cycle."}

    project = normalize_project(action.get("goal_id", "mumega").replace("goal_", ""))

    # Hard scope boundary — raises if this brain instance is not allowed to
    # create tasks for the resolved project. Non-task methods (health_check,
    # post_content) bypass this check since they don't write to a project queue.
    _task_creating_methods = ("create_task", "send_outreach", "fix_code", "research")
    if method in _task_creating_methods:
        try:
            _assert_in_scope(project)
        except ValueError as scope_err:
            logger.error(f"[scope] {scope_err}")
            return {"success": False, "result": str(scope_err)}

    method_labels = {
        "create_task": ["brain-generated"],
        "send_outreach": ["outreach", "brain-generated"],
        "fix_code": ["code", "brain-generated"],
        "research": ["research", "brain-generated"],
    }
    labels = method_labels.get(method, ["brain-generated"])
    squad_id = resolve_squad(labels, project)
    project_lead = PROJECT_LEADS.get(project)

    if project_lead and project_lead != agent:
        logger.info(f"Rerouting from {agent} to {project_lead} for project {project}")
        agent = project_lead

    # S180-A colony capability gate (agent dimension): gated PER-BRANCH below on
    # the FINAL dispatched (assignee, project) — NOT once here — because several
    # branches recompute the assignee/project after the reroute (e.g. send_outreach
    # flips to dentalnearyou/dandan on a 'dent' substring in free text). Gating the
    # final pair is the only correct subject. See _capability_block.

    try:
        if method == "create_task":
            title = action.get("action", "Brain-generated task")
            # squad path dispatches assignee=project_lead; mirror path dispatches agent.
            ct_assignee = project_lead if squad_id else agent
            if (block := _capability_block(ct_assignee, project)) is not None:
                return block

            if squad_id:
                if normalize_project(project) == "mumega":
                    return _mupot_dispatch_task(squad_id, title, details, "high", labels)
                # Route through Squad Service — project isolation
                import uuid
                task_id = f"brain-{uuid.uuid4().hex[:8]}"
                r = requests.post(f"{SQUAD_URL}/tasks", json={
                    "id": task_id,
                    "squad_id": squad_id,
                    "title": title,
                    "description": details,
                    "priority": "high",
                    "project": project,
                    "labels": labels,
                    "assignee": project_lead,
                }, headers=SQUAD_HEADERS, timeout=10)
                return {"success": True, "result": f"Squad task created: {task_id} (squad={squad_id}, agent={agent})", "task_id": task_id}
            else:
                # Non-squad project — use Mirror
                r = requests.post(f"{MIRROR_URL}/tasks", json={
                    "title": title,
                    "agent": agent,
                    "priority": "high",
                    "project": project,
                    "description": details,
                    "labels": labels,
                }, headers=MIRROR_HEADERS, timeout=10)
                task_data = r.json()
                task_id = task_data.get("task", {}).get("id", task_data.get("id", "?"))
                return {"success": True, "result": f"Task created: {task_id}", "task_id": task_id}

        elif method == "post_content":
            # Dispatches as agent="brain" (colony → always passes); gate kept for
            # the "every dispatch branch is gated" invariant (Athena G180-A adv.1),
            # so a future tenant-bound post_content path can't silently skip it.
            if (block := _capability_block("brain", project)) is not None:
                return block
            # Generate content using a cheap model and store it.
            # Skipped gracefully when BRAIN_CONTENT_MODE=off.
            content = _generate_content(details)
            if content == "__CONTENT_MODE_OFF__":
                return {"success": True, "result": "Content posting skipped (BRAIN_CONTENT_MODE=off)"}
            if content:
                # Store the content engram via MemoryPort (MirrorMemoryAdapter) —
                # no raw requests.post(MIRROR_URL/store).
                _memory_port.remember_sync(
                    content,
                    context_id=f"brain_content_{int(time.time())}",
                    core_concepts=["content", "brain_generated"],
                )
                return {"success": True, "result": f"Content generated ({len(content)} chars)"}
            return {"success": False, "result": "Content generation failed"}

        elif method == "send_outreach":
            # Route outreach through squad if project has one
            outreach_project = normalize_project("dentalnearyou" if "dent" in details.lower() or agent == "dandan" else project)
            outreach_labels = method_labels["send_outreach"]
            squad_id = resolve_squad(outreach_labels, outreach_project)
            outreach_assignee = PROJECT_LEADS.get(outreach_project, agent)
            # P0 guard: outreach recomputes project+assignee above (a 'dent' substring
            # flips to dentalnearyou/dandan), so gate the FINAL pair, not the original.
            if (block := _capability_block(outreach_assignee, outreach_project)) is not None:
                return block
            if squad_id:
                if normalize_project(outreach_project) == "mumega":
                    return _mupot_dispatch_task(squad_id, f"Outreach: {action.get('action', '')}", details, "medium", outreach_labels)
                import uuid
                task_id = f"brain-{uuid.uuid4().hex[:8]}"
                r = requests.post(f"{SQUAD_URL}/tasks", json={
                    "id": task_id,
                    "squad_id": squad_id,
                    "title": f"Outreach: {action.get('action', '')}",
                    "description": details,
                    "priority": "medium",
                    "project": outreach_project,
                    "labels": outreach_labels,
                    "assignee": outreach_assignee,
                }, headers=SQUAD_HEADERS, timeout=10)
                return {"success": True, "result": f"Squad outreach task: {task_id}"}
            else:
                r = requests.post(f"{MIRROR_URL}/tasks", json={
                    "title": f"Outreach: {action.get('action', '')}",
                    "agent": outreach_assignee,
                    "priority": "medium",
                    "project": outreach_project,
                    "description": details,
                    "labels": outreach_labels,
                }, headers=MIRROR_HEADERS, timeout=10)
                return {"success": True, "result": "Outreach task created"}

        elif method == "fix_code":
            # Route code fixes through squad if project has one
            code_labels = method_labels["fix_code"]
            squad_id = resolve_squad(code_labels, project)
            code_assignee = project_lead or "kasra"
            if (block := _capability_block(code_assignee, project)) is not None:
                return block
            if squad_id:
                if normalize_project(project) == "mumega":
                    return _mupot_dispatch_task(squad_id, f"Fix: {action.get('action', '')}", details, "high", code_labels)
                import uuid
                task_id = f"brain-{uuid.uuid4().hex[:8]}"
                r = requests.post(f"{SQUAD_URL}/tasks", json={
                    "id": task_id,
                    "squad_id": squad_id,
                    "title": f"Fix: {action.get('action', '')}",
                    "description": details,
                    "priority": "high",
                    "project": project,
                    "labels": code_labels,
                    "assignee": code_assignee,
                }, headers=SQUAD_HEADERS, timeout=10)
                return {"success": True, "result": f"Squad code task: {task_id}"}
            else:
                r = requests.post(f"{MIRROR_URL}/tasks", json={
                    "title": f"Fix: {action.get('action', '')}",
                    "agent": code_assignee,
                    "priority": "high",
                    "project": project,
                    "description": details,
                    "labels": code_labels,
                }, headers=MIRROR_HEADERS, timeout=10)
                return {"success": True, "result": "Code task created for Kasra"}

        elif method == "research":
            # BLOCK-4 fix (sos-205-a7c2fc44 adversarial gate): the mumega
            # early-return used to sit BEFORE this gate and return first, so
            # for project=="mumega" the colony capability gate
            # (_assert_agent_in_tenant) was skipped entirely — the only
            # branch among create_task/send_outreach/fix_code/research that
            # did. Gate first, unconditionally, before branching on the
            # dispatch target, matching every other branch.
            #
            # Re-gate fix (sos-205-b5307dd7): the gate subject used to be the
            # hardcoded literal "river". _agent_home_tenant('river') is
            # always None (river is a shared/colony agent, not in the
            # tenant-bound roster), so _capability_block("river", project)
            # could NEVER return DENY at any position in this function — it
            # was structurally in the right place but gating a subject that
            # can't fail, i.e. decorative. Gate the real `agent` variable
            # instead: the entity this dispatch believes is acting (already
            # resolved through the PROJECT_LEADS reroute above, same as
            # every other branch's assignee).
            if (block := _capability_block(agent, project)) is not None:
                return block
            if normalize_project(project) == "mumega":
                # Hardcoding a name as the DISPATCH target (not the gate
                # subject, fixed above) was the exact #490 root-cause pattern
                # (a stale roster assumption, not a live check) -- defer to
                # mupot's own effort-router for WHO does the work. "squad-core"
                # is research's fixed mumega squad target: LABEL_SQUAD_MAP has
                # no "research" entry, so the generic `squad_id` resolved
                # above is always None for this method and is deliberately
                # not reused here.
                research_squad_id = "squad-core"
                return _mupot_dispatch_task(research_squad_id, f"Research: {action.get('action', '')}", details, "medium", ["research", "brain-generated"])
            # Create research task for River (shared/colony agent — gate for uniformity)
            r = requests.post(f"{MIRROR_URL}/tasks", json={
                "title": f"Research: {action.get('action', '')}",
                "agent": "river",
                "priority": "medium",
                "description": details,
                "labels": ["research", "brain-generated"],
            }, headers=MIRROR_HEADERS, timeout=10)
            return {"success": True, "result": "Research task created for River"}

        elif method == "health_check":
            # Run health checks
            services = {}
            for name, url in [("mirror", f"{MIRROR_URL}/"), ("engine", f"{ENGINE_URL}/health")]:
                try:
                    r = requests.get(url, timeout=5)
                    services[name] = "UP" if r.status_code == 200 else "DOWN"
                except:
                    services[name] = "DOWN"
            return {"success": True, "result": f"Health: {services}"}

        else:
            # Default: create a generic task
            default_title = action.get("action", "Brain action")
            if (block := _capability_block(agent, project)) is not None:
                return block
            if _task_exists(default_title, agent):
                return {"success": True, "result": f"Duplicate task skipped for {agent}: {default_title[:60]}"}
            r = requests.post(f"{MIRROR_URL}/tasks", json={
                "title": default_title,
                "agent": agent,
                "priority": "medium",
                "description": details,
                "labels": ["brain-generated"],
            }, headers=MIRROR_HEADERS, timeout=10)
            return {"success": True, "result": "Task created"}

    except Exception as e:
        logger.error(f"Motor execution failed: {e}")
        return {"success": False, "result": str(e)}


def _generate_content(prompt: str) -> str:
    """Generate content — respects BRAIN_CONTENT_MODE.
    GitHub Models first (free), Gemini 2.5 Flash as fallback.
    Returns "" if BRAIN_CONTENT_MODE is "off" (caller will mark as no-op success).
    """
    if BRAIN_CONTENT_MODE == "off":
        logger.info("_generate_content: skipped (BRAIN_CONTENT_MODE=off)")
        return "__CONTENT_MODE_OFF__"  # sentinel — caller treats as no-op

    if GITHUB_TOKEN:
        try:
            from openai import OpenAI
            client = OpenAI(
                base_url="https://models.inference.ai.azure.com",
                api_key=GITHUB_TOKEN,
            )
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500,
            )
            return response.choices[0].message.content.strip()
        except Exception:
            pass
    # Fallback: Vertex AI ADC
    try:
        from google import genai
        client = genai.Client(
            vertexai=True,
            project=GOOGLE_CLOUD_PROJECT,
            location=GOOGLE_CLOUD_LOCATION,
        )
        response = client.models.generate_content(
            model=BRAIN_MODEL,
            contents=prompt,
        )
        return response.text.strip()
    except Exception:
        pass
    return ""


def hippocampus_recall() -> str:
    """
    HIPPOCAMPUS — Memory Recall
    Retrieves current state from Mirror + metabolism.
    """
    try:
        from cortex import render_portfolio_context, snapshot_portfolio

        state = snapshot_portfolio()
        context = render_portfolio_context(state)
        if context.strip():
            return context
    except Exception as e:
        logger.warning(f"Cortex snapshot failed, falling back to legacy recall: {e}")

    state_parts = []

    # Get goals from Mirror via MemoryPort
    try:
        goal_hits = _memory_port.search_sync("GOAL active", top_k=10, agent_filter="os")
        goals = []
        for hit in goal_hits:
            raw = hit.metadata if isinstance(hit.metadata, dict) else {}
            if raw.get("goal"):
                g = raw["goal"]
                goals.append(f"[{g.get('priority','?')}] {g.get('title','')} — progress: {g.get('progress',0):.0%}")
        if goals:
            state_parts.append("ACTIVE GOALS:\n" + "\n".join(goals))
    except Exception:
        pass

    # Get objections via MemoryPort
    try:
        obj_hits = _memory_port.search_sync("OBJECTION active", top_k=10, agent_filter="os")
        objections = []
        for hit in obj_hits:
            raw = hit.metadata if isinstance(hit.metadata, dict) else {}
            if raw.get("objection"):
                o = raw["objection"]
                objections.append(f"[{o.get('type','?')} {o.get('intensity',0):.1f}] {o.get('description','')[:80]}")
        if objections:
            state_parts.append("ACTIVE OBJECTIONS:\n" + "\n".join(objections))
    except Exception:
        pass

    # Get pending tasks
    try:
        r = requests.get(f"{MIRROR_URL}/tasks?status=backlog", headers=MIRROR_HEADERS, timeout=10)
        data = r.json()
        tasks = data.get("tasks", []) if isinstance(data, dict) else data
        if tasks:
            task_lines = [f"[{t.get('agent','?')}] {t.get('title','')[:60]}" for t in tasks[:5]]
            state_parts.append("PENDING TASKS:\n" + "\n".join(task_lines))
    except:
        pass

    # Get metabolism status
    try:
        import subprocess
        result = subprocess.run(
            ["python3", "/home/sos/SOS/sos/services/economy/metabolism.py", "status"],
            capture_output=True, text=True, timeout=10,
        )
        if result.stdout:
            state_parts.append("METABOLISM:\n" + result.stdout[:500])
    except:
        pass

    # Get service health
    services = {}
    for name, url in [("mirror", f"{MIRROR_URL}/"), ("engine", f"{ENGINE_URL}/health")]:
        try:
            r = requests.get(url, timeout=3)
            services[name] = "UP"
        except:
            services[name] = "DOWN"
    state_parts.append("SERVICES: " + " | ".join(f"{k}:{v}" for k, v in services.items()))

    # Current time context
    now = datetime.now(timezone.utc)
    state_parts.insert(0, f"TIMESTAMP: {now.isoformat()[:19]} UTC ({now.strftime('%A')})")

    return "\n\n".join(state_parts) if state_parts else "No state available."


# INKWELL_API_URL: base URL for the inkwell-api Worker (e.g. https://api.mumega.com)
# INKWELL_INTERNAL_SECRET: matches INTERNAL_API_SECRET wrangler secret
INKWELL_API_URL = os.environ.get("INKWELL_API_URL", "").rstrip("/")
INKWELL_INTERNAL_SECRET = os.environ.get("INKWELL_INTERNAL_SECRET", "")


def report_to_inkwell(action: dict, result: dict, cycle_ms: int) -> None:
    """POST cycle summary to inkwell-api brain_cycles D1 table."""
    if not INKWELL_API_URL or not INKWELL_INTERNAL_SECRET:
        return
    import urllib.request
    scope_label = (
        ",".join(sorted(BRAIN_TENANT_SCOPE)) if BRAIN_TENANT_SCOPE else "*"
    )
    payload = json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(),
        "success": 1 if result.get("success") else 0,
        "task_title": action.get("action", ""),
        "method": action.get("method", ""),
        "agent": action.get("agent", ""),
        "model": BRAIN_MODEL,
        "result": str(result.get("result", ""))[:500],
        "cycle_ms": cycle_ms,
        "tenant_scope": scope_label,
        "scope_type": BRAIN_SCOPE_TYPE,
        "tokens_used": _cycle_tokens,
        "token_budget": BRAIN_TOKEN_BUDGET,
    }).encode()
    req = urllib.request.Request(
        f"{INKWELL_API_URL}/api/brain/cycle",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {INKWELL_INTERNAL_SECRET}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as _r:
            pass
    except Exception as e:
        logger.warning(f"report_to_inkwell failed (non-critical): {e}")


def report_to_discord(action: dict, result: dict):
    """Post brain cycle result to Discord #control.

    S028 A3 — emit format follows brain-emit-format-canon-2026-05-04.md §2.
    Replaces ambiguous `Agent: <coordinator>` with explicit Decided-by /
    Proposed-action / Routes-through / Status / Reason fields. Disambiguates
    autonomous-decision origin (this daemon) from coordinator-of-record (the
    agent named in `action.agent`) so receivers don't have to guess whether
    a destructive op was already executed by Loom or merely proposed.
    """
    if not DISCORD_BOT_TOKEN:
        return

    now = datetime.now(timezone.utc)
    cycle_id = f"brain.py:cycle_{now.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    success = bool(result.get("success"))
    status_word = "executed" if success else "failed"
    status_emoji = "✅" if success else "❌"
    summary = action.get("action", "?")
    method = action.get("method", "?")
    details = action.get("details", "")
    proposed_action = f"{method}({details[:80]})" if details else method
    routes_through = action.get("agent", "?")
    reason_line = ""
    if not success:
        reason_text = str(result.get("result", "?"))[:200]
        reason_line = f"\nReason: {reason_text}"

    msg = (
        f"`{now.strftime('%H:%M')}` **[BRAIN]** {status_emoji} {summary}\n"
        f"Decided-by: {cycle_id}\n"
        f"Proposed-action: {proposed_action}\n"
        f"Routes-through: {routes_through}\n"
        f"Status: {status_word}"
        f"{reason_line}"
    )

    # Escalation-only emission (Hadi directive 2026-07-27): kasra is the
    # repair escalation path, not the brain's activity feed. Routine cycles
    # (executed housekeeping, dedup/roster/mode-off skips) stay in the journal;
    # only failures — the repairable class — page out. The kasra bus inbox is
    # bridged to Hadi's Telegram, so every message here is a phone ping.
    logger.info(f"brain-cycle {status_word}: {summary[:120]}")
    if status_word != "failed":
        return

    try:
        from kernel.bus import send as bus_send
        if not bus_send(to="kasra", text=msg):
            # fallback: Discord
            import subprocess
            subprocess.run(
                ["bash", "/home/sos/scripts/discord-reply.sh", "brain", "control", msg],
                capture_output=True, timeout=10,
            )
    except Exception:
        pass


def remember(action: dict, result: dict):
    """Store brain cycle in Mirror for learning.

    Routes through _memory_port (MemoryPort / MirrorMemoryAdapter) instead
    of raw requests.post(MIRROR_URL/store). Behavior-identical: same
    payload fields, same fail-open contract.
    """
    try:
        _memory_port.remember_sync(
            f"Brain action: {action.get('action','')} → {result.get('result','')}",
            context_id=f"brain_cycle_{int(time.time())}",
            core_concepts=["brain", "cycle", action.get("method", ""), action.get("agent", "")],
            raw_data={"action": action, "result": result},
        )
    except Exception:
        pass


# ============================================
# THE LIVING LOOP
# ============================================

def cycle():
    """One brain cycle: perceive → think → act → remember → report."""
    global _cycle_tokens
    _cycle_tokens = 0  # reset token counter at start of each cycle

    import time as _time
    _cycle_start = _time.monotonic()
    scope_label = ",".join(sorted(BRAIN_TENANT_SCOPE)) if BRAIN_TENANT_SCOPE else "*"
    logger.info(f"=== BRAIN CYCLE START === scope={scope_label} type={BRAIN_SCOPE_TYPE} budget={BRAIN_TOKEN_BUDGET or 'unlimited'}")

    # 1. PERCEIVE (hippocampus)
    logger.info("Perceiving system state...")
    context = hippocampus_recall()
    logger.info(f"Context gathered ({len(context)} chars)")

    # 2. THINK (prefrontal cortex)
    logger.info("Thinking... (Gemma 4 31B)")
    raw_decision = prefrontal_think(context)
    logger.info(f"Decision: {raw_decision[:200]}")

    try:
        action = json.loads(raw_decision)
    except json.JSONDecodeError:
        # Try to extract JSON from the response
        try:
            start = raw_decision.index("{")
            end = raw_decision.rindex("}") + 1
            action = json.loads(raw_decision[start:end])
        except:
            logger.error("Failed to parse decision as JSON")
            action = {
                "action": "System health check (fallback — decision parsing failed)",
                "goal_id": "maintenance",
                "agent": "system",
                "method": "health_check",
                "details": "Prefrontal output wasn't parseable",
                "expected_progress": 0.01,
                "risk": 0.0,
            }

    logger.info(f"Action: {action.get('action', '?')}")
    logger.info(f"Agent: {action.get('agent', '?')} | Method: {action.get('method', '?')}")

    # 3. ACT (motor cortex)
    logger.info("Executing...")
    result = motor_execute(action)
    logger.info(f"Result: {result}")

    # 4. REMEMBER (hippocampus write)
    remember(action, result)

    # 5. REPORT (to Discord + inkwell-api)
    report_to_discord(action, result)
    cycle_ms = int((_time.monotonic() - _cycle_start) * 1000)
    report_to_inkwell(action, result, cycle_ms)

    logger.info("=== BRAIN CYCLE COMPLETE ===")
    return action, result


def daemon():
    """Run brain continuously — one cycle every 2 hours."""
    logger.info("Brain daemon starting — cycle every 2 hours")
    while True:
        try:
            cycle()
        except Exception as e:
            logger.error(f"Brain cycle crashed: {e}")
        time.sleep(7200)  # 2 hours


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv("/home/sos/.env.secrets")
    load_dotenv("/home/sos/therealmofpatterns/.env")

    if "--daemon" in sys.argv:
        daemon()
    else:
        cycle()
