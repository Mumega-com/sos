#!/usr/bin/env python3
"""
Sovereign Loop — The Ralph Pattern for Mumega

Always-on. Uses Gemma 4 31B (free) as the router brain.
Doesn't create tasks for others. DOES the work itself.

Every cycle:
  1. Read goals + tasks from Mirror
  2. Pick ONE task (oldest backlog, highest priority)
  3. Execute it directly (using available tools)
  4. Mark complete or failed
  5. Commit learnings to Mirror
  6. Report to Discord
  7. Loop

Unlike brain.py which creates tasks, this EXECUTES them.
Unlike kasra-loop which waits for tmux, this runs standalone.

The loop uses Gemma 4 to DECIDE and cheap models to DO.
If it needs Claude/Codex (heavy lifting), it escalates to tmux agents.
Otherwise it handles it — content gen, scanning, outreach, research.

Run:
  python3 loop.py              # one cycle
  python3 loop.py --daemon     # continuous (every 30 min)
  python3 loop.py --once       # one cycle then exit
"""

import os
import sys
import json
import time
import logging
import subprocess
import requests
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [LOOP] %(message)s")
logger = logging.getLogger("loop")

import sys, os as _os
_SOVEREIGN_DIR = _os.path.dirname(_os.path.abspath(__file__))
if _SOVEREIGN_DIR not in sys.path:
    sys.path.insert(0, _SOVEREIGN_DIR)

from kernel.config import MIRROR_URL, MIRROR_TOKEN, SQUAD_URL
from model_config import get as _model_cfg

MIRROR_TOKEN = os.environ.get("MIRROR_TOKEN", MIRROR_TOKEN)
MIRROR_HEADERS = {"Authorization": f"Bearer {MIRROR_TOKEN}", "Content-Type": "application/json"}
SQUAD_TOKEN = os.environ.get("SOS_SYSTEM_TOKEN", "sk-sos-system")
SQUAD_HEADERS = {
    "Authorization": f"Bearer {SQUAD_TOKEN}",
    "Content-Type": "application/json",
}
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
PROGRESS_FILE = Path("/home/mumega/.mumega/loop_progress.txt")
REPLY_CURSOR_FILE = Path("/home/mumega/.mumega/loop_reply_cursor.txt")
REPLY_AGENT = "sovereign-loop"
REPLY_STREAM = f"sos:stream:sos:channel:private:agent:{REPLY_AGENT}"
PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)


# ============================================
# TOOLS — what the loop can actually do
# ============================================

AVAILABLE_TOOLS = {
    "scan_google_maps": {
        "description": "Scan Google Maps for businesses in a city",
        "script": "/home/mumega/scripts/scan-google-maps.py",
        "example": "python3 scan-google-maps.py dentist Toronto 10",
    },
    "generate_content": {
        "description": "Generate a blog post or social content",
        "uses": "Gemma 4 31B or GitHub Models",
    },
    "post_discord": {
        "description": "Post a message to a Discord channel",
        "script": "/home/mumega/scripts/discord-reply.sh",
        "example": "bash discord-reply.sh dandan control 'message'",
    },
    "check_services": {
        "description": "Check if Mirror, Engine, and other services are healthy",
    },
    "search_mirror": {
        "description": "Search Mirror memory for context",
    },
    "escalate_to_tmux": {
        "description": "Send a task to a Claude Code agent in tmux (for heavy work)",
        "agents": ["kasra", "athena"],
    },
}

SEO_SKILLS = [
    {
        "id": "site_audit",
        "name": "Site Audit",
        "description": "Run a technical SEO site audit",
        "labels": ["audit", "technical-seo"],
        "keywords": ["audit", "crawl"],
        "entrypoint": "sovereign.skills.seo:site_audit",
    },
    {
        "id": "meta_optimizer",
        "name": "Meta Optimizer",
        "description": "Optimize titles and meta descriptions",
        "labels": ["meta", "meta_optimization"],
        "keywords": ["meta tag", "title tag"],
        "entrypoint": "sovereign.skills.seo:meta_optimizer",
    },
    {
        "id": "link_analyzer",
        "name": "Link Analyzer",
        "description": "Analyze internal links and link maps",
        "labels": ["links", "internal_linking"],
        "keywords": ["internal link", "link map"],
        "entrypoint": "sovereign.skills.seo:internal_link_analyzer",
    },
    {
        "id": "schema_checker",
        "name": "Schema Checker",
        "description": "Check schema markup and JSON-LD",
        "labels": ["schema", "schema_markup"],
        "keywords": ["schema", "json-ld"],
        "entrypoint": "sovereign.skills.seo:schema_checker",
    },
    {
        "id": "full_audit",
        "name": "Full SEO Audit",
        "description": "Run the full SEO audit flow",
        "labels": ["full-audit"],
        "keywords": ["full audit", "seo audit"],
        "entrypoint": "sovereign.skills.seo:run_full_audit",
    },
]


def is_squad_task(task: dict) -> bool:
    return bool(task.get("squad_id"))


def task_agent(task: dict) -> str:
    return str(task.get("agent", "") or "").strip().lower()


def has_explicit_agent(task: dict) -> bool:
    agent = task_agent(task)
    return bool(agent and agent != "system")


def register_squad_skills():
    """Best-effort registration of built-in SEO skills into the squad service."""
    for skill in SEO_SKILLS:
        payload = {
            **skill,
            "required_inputs": [],
            "status": "active",
            "fuel_grade": "diesel",
            "version": "1.0.0",
        }
        try:
            requests.post(f"{SQUAD_URL}/skills", json=payload, headers=SQUAD_HEADERS, timeout=5)
        except Exception as exc:
            logger.warning(f"Skill registration skipped for {skill['id']}: {exc}")


def get_pending_tasks() -> list:
    """Get backlog tasks from Squad Service first, then Mirror fallback for non-squad tasks."""
    tasks = []
    try:
        squad_resp = requests.get(
            f"{SQUAD_URL}/tasks",
            params={"status": "backlog"},
            headers=SQUAD_HEADERS,
            timeout=10,
        )
        if squad_resp.ok:
            tasks.extend(squad_resp.json())
    except Exception:
        pass
    try:
        mirror_resp = requests.get(f"{MIRROR_URL}/tasks", headers=MIRROR_HEADERS, timeout=10)
        data = mirror_resp.json()
        all_tasks = data.get("tasks", []) if isinstance(data, dict) else data
        tasks.extend(
            t for t in all_tasks
            if t.get("status") == "backlog" and not t.get("completed_at") and not is_squad_task(t)
        )
    except Exception:
        pass

    seen = set()
    unique = []
    for task in tasks:
        key = task.get("id") or task.get("title", "")[:50]
        if key not in seen:
            seen.add(key)
            unique.append(task)
    priority_order = {"critical": 0, "urgent": 0, "high": 1, "medium": 2, "low": 3}
    return sorted(
        unique,
        key=lambda t: (
            0 if has_explicit_agent(t) else 1,
            0 if is_squad_task(t) else 1,
            priority_order.get(t.get("priority", "medium"), 2),
        ),
    )


def pick_task(tasks: list) -> dict:
    """Pick highest priority task."""
    priority_order = {"critical": 0, "urgent": 0, "high": 1, "medium": 2, "low": 3}
    sorted_tasks = sorted(
        tasks,
        key=lambda t: (
            0 if has_explicit_agent(t) else 1,
            0 if is_squad_task(t) else 1,
            priority_order.get(t.get("priority", "medium"), 2),
        ),
    )
    return sorted_tasks[0] if sorted_tasks else {}


def mark_task(task_or_id, status: str, note: str = ""):
    """Update task status in Squad Service or Mirror depending on task type."""
    task = task_or_id if isinstance(task_or_id, dict) else {"id": task_or_id}
    task_id = task.get("id", "")
    try:
        if is_squad_task(task):
            if status == "done":
                requests.post(
                    f"{SQUAD_URL}/tasks/{task_id}/complete",
                    json={"result": {"note": note} if note else {}},
                    headers=SQUAD_HEADERS,
                    timeout=10,
                )
            elif status in {"failed", "blocked"}:
                requests.post(
                    f"{SQUAD_URL}/tasks/{task_id}/fail",
                    json={"error": note or status},
                    headers=SQUAD_HEADERS,
                    timeout=10,
                )
        else:
            if status == "done":
                r = requests.post(f"{MIRROR_URL}/tasks/{task_id}/complete", headers=MIRROR_HEADERS, timeout=10)
                if r.status_code == 409:
                    logger.warning(f"Task {task_id} has unresolved dependencies, forcing done via PUT")
                    requests.put(f"{MIRROR_URL}/tasks/{task_id}", json={"status": "done"}, headers=MIRROR_HEADERS, timeout=10)
                elif r.status_code >= 400:
                    requests.put(f"{MIRROR_URL}/tasks/{task_id}", json={"status": "done"}, headers=MIRROR_HEADERS, timeout=10)
            else:
                requests.put(f"{MIRROR_URL}/tasks/{task_id}", json={"status": status}, headers=MIRROR_HEADERS, timeout=10)
        logger.info(f"Task {task_id} → {status}")
    except Exception as e:
        logger.error(f"Failed to mark {task_id} → {status}: {e}")


# ---------------------------------------------------------------------------
# Project session management (sovereign-loop is the authoritative check-in path)
# ---------------------------------------------------------------------------

_open_sessions: dict[str, str] = {}  # project_id → session_id


def _session_checkin(project_id: str) -> str | None:
    """Check in to a project session. Returns session_id or None on failure.

    Called before first task claim in a project. Idempotent — Squad Service
    returns the existing open session if one already exists.
    """
    if project_id in _open_sessions:
        return _open_sessions[project_id]
    try:
        resp = requests.post(
            f"{SQUAD_URL}/projects/{project_id}/checkin",
            json={"agent_id": "sovereign-loop", "context": {"source": "loop"}},
            headers=SQUAD_HEADERS,
            timeout=10,
        )
        if resp.status_code == 200:
            sid = resp.json().get("session_id")
            if sid:
                _open_sessions[project_id] = sid
                logger.info(f"[sessions] checked in to project={project_id} session={sid}")
                return sid
    except Exception as exc:
        logger.warning(f"[sessions] checkin failed for project={project_id}: {exc}")
    return None


def _session_heartbeat(project_id: str) -> None:
    """Send heartbeat for the open session of this project."""
    sid = _open_sessions.get(project_id)
    if not sid:
        return
    try:
        requests.post(
            f"{SQUAD_URL}/sessions/{sid}/heartbeat",
            headers=SQUAD_HEADERS,
            timeout=5,
        )
    except Exception as exc:
        logger.debug(f"[sessions] heartbeat failed for session={sid}: {exc}")


def _session_checkout(project_id: str) -> None:
    """Check out of a project session (project queue is now empty)."""
    sid = _open_sessions.pop(project_id, None)
    if not sid:
        return
    try:
        requests.post(
            f"{SQUAD_URL}/sessions/{sid}/checkout",
            json={"reason": "explicit"},
            headers=SQUAD_HEADERS,
            timeout=10,
        )
        logger.info(f"[sessions] checked out of project={project_id} session={sid}")
    except Exception as exc:
        logger.warning(f"[sessions] checkout failed for session={sid}: {exc}")


def _sweep_idle_sessions(project_id: str) -> None:
    """Trigger idle session cleanup for a project (sovereign loop sweep path)."""
    try:
        from sos.services.squad.sessions import ProjectSessionService
        svc = ProjectSessionService()
        closed = svc.close_idle_sessions(project_id)
        if closed:
            logger.info(f"[sessions] closed {closed} idle session(s) for project={project_id}")
    except Exception as exc:
        logger.debug(f"[sessions] idle sweep failed for project={project_id}: {exc}")


def _apply_governance_caps(task: dict) -> dict:
    """Read sos:policy:governance and apply fuel_grade + token_budget caps.

    Fail-open: if Redis is unavailable or no policy is set, return task unchanged.
    FUEL_GRADE_ORDER and cap logic live in sos.services.engine.policy (single source).
    """
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from sos.services.engine.policy import apply_caps, load_policy
        policy = load_policy(get_redis_client())
        if policy is None:
            return task
        return apply_caps(task, policy)
    except Exception as exc:
        logger.warning("Governance policy cap skipped (fail-open): %s", exc)
        return task


def claim_task(task: dict) -> bool:
    """Claim squad tasks before execution. Non-squad tasks keep the old Mirror path.

    Applies governance policy caps (fuel_grade, token_budget) before claiming
    so the capped values flow into execution and the completion result.
    """
    if not is_squad_task(task):
        mark_task(task, "in_progress")
        return True

    task = _apply_governance_caps(task)

    # Sovereign loop is the authoritative check-in path.
    # Open a session for this project before claiming (idempotent).
    project_id = task.get("project", "")
    if project_id:
        _sweep_idle_sessions(project_id)
        _session_checkin(project_id)

    task_id = task.get("id", "")
    attempt = int(task.get("attempt", 0))
    try:
        response = requests.post(
            f"{SQUAD_URL}/tasks/{task_id}/claim",
            json={"assignee": "sovereign-loop", "attempt": attempt},
            headers=SQUAD_HEADERS,
            timeout=10,
        )
        if response.status_code == 409:
            logger.info(f"Task {task_id} already claimed elsewhere")
            return False
        response.raise_for_status()
        return True
    except Exception as exc:
        logger.error(f"Failed to claim squad task {task_id}: {exc}")
        return False


def get_redis_client():
    import redis as redis_lib

    password = os.environ.get("REDIS_PASSWORD", "")
    return redis_lib.from_url(
        f"redis://:{password}@localhost:6379/0" if password else "redis://localhost:6379/0",
        decode_responses=True,
        socket_timeout=3,
    )


def task_like_payload(task: dict) -> dict:
    """Shape a task for squad service APIs that expect the contract fields."""
    return {
        "id": task.get("id", ""),
        "squad_id": task.get("squad_id", ""),
        "title": task.get("title", ""),
        "description": task.get("description", ""),
        "status": task.get("status", "backlog"),
        "priority": task.get("priority", "medium"),
        "assignee": task.get("assignee"),
        "skill_id": task.get("skill_id"),
        "project": task.get("project", ""),
        "labels": task.get("labels", []),
        "blocked_by": task.get("blocked_by", []),
        "blocks": task.get("blocks", []),
        "inputs": task.get("inputs", {}),
        "result": task.get("result", {}),
        "token_budget": task.get("token_budget", 0),
        "bounty": task.get("bounty", {}),
        "external_ref": task.get("external_ref"),
        "attempt": task.get("attempt", 0),
    }


def get_skill_matches(task: dict) -> list[dict]:
    if not is_squad_task(task):
        return []
    try:
        response = requests.post(
            f"{SQUAD_URL}/skills/match",
            params={"min_trust_tier": 1},
            json=task_like_payload(task),
            headers=SQUAD_HEADERS,
            timeout=10,
        )
        response.raise_for_status()
        matches = response.json()
        return matches if isinstance(matches, list) else []
    except Exception as exc:
        logger.warning(f"Skill match failed for {task.get('id', '')}: {exc}")
        return []


def get_squad_state(task: dict) -> dict:
    squad_id = task.get("squad_id", "")
    if not squad_id:
        return {}
    try:
        response = requests.get(f"{SQUAD_URL}/state/{squad_id}", headers=SQUAD_HEADERS, timeout=10)
        if response.status_code == 404:
            return {}
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:
        logger.warning(f"State load failed for squad {squad_id}: {exc}")
        return {}


def route_squad_task(task: dict, assignee: str, skill_matches: list[dict]) -> None:
    if not is_squad_task(task):
        return
    payload = {
        "assignee": assignee,
        "skill_id": skill_matches[0]["skill_id"] if skill_matches else None,
        "reason": f"sovereign-loop dispatch to {assignee}",
    }
    try:
        response = requests.post(
            f"{SQUAD_URL}/tasks/{task.get('id', '')}/route",
            json=payload,
            headers=SQUAD_HEADERS,
            timeout=10,
        )
        response.raise_for_status()
    except Exception as exc:
        logger.warning(f"Task routing update failed for {task.get('id', '')}: {exc}")


def extract_dispatch_target(task: dict, skill_matches: list[dict]) -> str | None:
    labels = {str(label).lower() for label in task.get("labels", [])}
    # If task has an explicit agent, don't override — execute_task handles it
    if has_explicit_agent(task):
        return None
    if labels & {"code", "fix", "bug"}:
        return "codex"
    if labels & {"seo", "audit", "meta", "schema"}:
        return "worker"
    if labels & {"content", "blog", "social"}:
        return "worker"
    if labels & {"outreach", "email", "leads"}:
        return "dandan"
    if skill_matches or "squad" in labels:
        return "worker"
    return None


def read_reply_cursor() -> str:
    if REPLY_CURSOR_FILE.exists():
        return REPLY_CURSOR_FILE.read_text().strip() or "0-0"
    return "0-0"


def write_reply_cursor(message_id: str) -> None:
    REPLY_CURSOR_FILE.write_text(message_id)


def parse_stream_payload(fields: dict[str, str]) -> tuple[str, dict]:
    if "data" in fields:
        try:
            envelope = json.loads(fields["data"])
            if isinstance(envelope, dict):
                payload = envelope.get("payload", {})
                return str(envelope.get("type", "")), payload if isinstance(payload, dict) else {}
        except Exception:
            pass

    payload = {}
    if "payload" in fields:
        try:
            loaded = json.loads(fields["payload"])
            payload = loaded if isinstance(loaded, dict) else {}
        except Exception:
            payload = {"raw": fields["payload"]}
    return str(fields.get("type", "")), payload


def complete_squad_task_from_message(task_id: str, payload: dict, source: str) -> None:
    result = payload.get("result")
    if not isinstance(result, dict):
        result = {
            "summary": payload.get("summary", ""),
            "output": payload.get("output", {}),
            "payload": payload,
        }

    try:
        requests.post(
            f"{SQUAD_URL}/tasks/{task_id}/complete",
            json={"result": result},
            headers=SQUAD_HEADERS,
            timeout=10,
        ).raise_for_status()
        logger.info(f"Completed squad task {task_id} from {source}")
    except Exception as exc:
        logger.error(f"Failed to complete squad task {task_id} from {source}: {exc}")


def fail_squad_task_from_message(task_id: str, payload: dict, source: str) -> None:
    error = payload.get("error") or payload.get("result") or payload.get("summary") or "remote failure"
    try:
        requests.post(
            f"{SQUAD_URL}/tasks/{task_id}/fail",
            json={"error": str(error)},
            headers=SQUAD_HEADERS,
            timeout=10,
        ).raise_for_status()
        logger.info(f"Failed squad task {task_id} from {source}")
    except Exception as exc:
        logger.error(f"Failed to mark squad task {task_id} failed from {source}: {exc}")


def drain_async_completions(limit: int = 25) -> int:
    """Process worker/codex replies sent back to sovereign-loop via Redis."""
    try:
        r = get_redis_client()
        messages = r.xread({REPLY_STREAM: read_reply_cursor()}, count=limit, block=1)
    except Exception as exc:
        logger.warning(f"Reply stream read failed: {exc}")
        return 0

    processed = 0
    for _, entries in messages:
        for message_id, fields in entries:
            message_type, payload = parse_stream_payload(fields)
            payload = payload if isinstance(payload, dict) else {}
            task_id = payload.get("task_id") or fields.get("task_id")
            status = str(payload.get("status", "")).lower()
            source = str(payload.get("source") or fields.get("source") or "redis")

            if task_id and (
                message_type in {"task_complete", "task_completed", "task_result", "task_done"}
                or status in {"done", "completed", "success"}
            ):
                complete_squad_task_from_message(task_id, payload, source)
                processed += 1
            elif task_id and (
                message_type in {"task_failed", "task_error"}
                or status in {"failed", "error", "blocked"}
            ):
                fail_squad_task_from_message(task_id, payload, source)
                processed += 1

            write_reply_cursor(message_id)
    return processed


def execute_task(task: dict) -> dict:
    """
    Execute a task directly. The core of the loop.
    Returns {"success": bool, "result": str}
    """
    title = task.get("title", "")
    description = task.get("description", "")
    agent = task_agent(task)
    labels = task.get("labels", [])

    logger.info(f"Executing: {title}")

    if not claim_task(task):
        return {"success": False, "result": "Task claim failed or already claimed", "skipped": True}

    skill_matches = get_skill_matches(task) if is_squad_task(task) else []
    squad_state = get_squad_state(task) if is_squad_task(task) else {}

    # Explicit assignment always wins over keyword routing.
    # Known tmux agents get tmux send-keys, everything else gets bus/OpenClaw.
    TMUX_AGENTS = {"kasra", "mumega", "codex", "spai"}
    OPENCLAW_AGENTS = {"athena", "worker", "sol", "dandan", "gemma", "river"}

    if has_explicit_agent(task):
        if is_squad_task(task):
            route_squad_task(task, agent, skill_matches)

        if agent in TMUX_AGENTS:
            return escalate_to_tmux(task, agent)
        else:
            # All other named agents go through bus/OpenClaw
            return route_to_remote_agent(task, agent, skill_matches, squad_state)

    # Route based on task content
    title_lower = title.lower()
    desc_lower = description.lower()
    combined = f"{title_lower} {desc_lower}"
    dispatch_target = extract_dispatch_target(task, skill_matches)

    if dispatch_target and is_squad_task(task):
        route_squad_task(task, dispatch_target, skill_matches)

    if dispatch_target and is_squad_task(task):
        squad_state = get_squad_state(task)
        return route_to_remote_agent(task, dispatch_target, skill_matches, squad_state)

    # === SQUAD TASKS (agent=worker) → route to OpenClaw worker, not tmux ===
    if agent == "worker" or "squad" in labels:
        return route_to_remote_agent(task, "worker", skill_matches, {})

    # === SCAN / LEADS ===
    if any(kw in combined for kw in ["google maps", "scan", "find dentist", "find businesses", "leads"]):
        return execute_scan(task)

    # === CONTENT / BLOG / SOCIAL ===
    if any(kw in combined for kw in ["content", "blog", "post", "write", "generate"]):
        return execute_content(task)

    # === OUTREACH / EMAIL / FOLLOW UP ===
    if any(kw in combined for kw in ["outreach", "follow up", "email", "reach out", "contact"]):
        return execute_outreach(task)

    # === CODE / FIX / DEPLOY ===
    if any(kw in combined for kw in ["code", "fix", "deploy", "build", "implement", "refactor"]):
        return escalate_to_tmux(task, "kasra")

    # === ARCHITECTURE / PLAN / DESIGN ===
    if any(kw in combined for kw in ["architect", "plan", "design", "decompose", "strategy"]):
        return escalate_to_tmux(task, "athena")

    # === RESEARCH ===
    if any(kw in combined for kw in ["research", "investigate", "analyze", "find out", "search", "look up"]):
        return escalate_to_hermes(task)

    # === DEFAULT: try to do it with LLM ===
    return execute_generic(task)


def execute_scan(task: dict) -> dict:
    """Run Google Maps scanner."""
    desc = task.get("description", "")
    # Extract city from description or default to Toronto
    city = "Toronto"
    for c in ["Toronto", "Vancouver", "Calgary", "Montreal", "Ottawa", "Mississauga", "Brampton"]:
        if c.lower() in desc.lower():
            city = c
            break

    query = "dentist"  # default for DNU
    if "restaurant" in desc.lower(): query = "restaurant"
    if "clinic" in desc.lower(): query = "clinic"

    try:
        result = subprocess.run(
            ["python3", "/home/mumega/scripts/scan-google-maps.py", query, city, "10"],
            capture_output=True, text=True, timeout=120,
            env={**os.environ, "GEMINI_API_KEY": GEMINI_API_KEY},
        )
        output = result.stdout[-500:] if result.stdout else result.stderr[-200:]
        return {"success": result.returncode == 0, "result": output}
    except Exception as e:
        return {"success": False, "result": str(e)}


def execute_content(task: dict) -> dict:
    """Generate content using Gemma 4."""
    title = task.get("title", "")
    desc = task.get("description", title)

    text = call_gemma4(f"Write a short, professional blog post or social media post about: {desc}. Keep it under 300 words. Be concrete, not generic.")
    if text:
        # Store in Mirror
        requests.post(f"{MIRROR_URL}/store", json={
            "agent": "loop",
            "text": text,
            "context_id": f"content_{int(time.time())}",
            "core_concepts": ["content", "loop-generated"],
        }, headers=MIRROR_HEADERS, timeout=10)
        return {"success": True, "result": f"Content generated ({len(text)} chars)"}
    return {"success": False, "result": "Content generation failed"}


def execute_outreach(task: dict) -> dict:
    """Draft outreach messages. Can't send emails yet — creates drafts and reports."""
    desc = task.get("description", task.get("title", ""))

    text = call_gemma4(f"""Draft a short, professional outreach message for: {desc}

The message should:
- Be under 100 words
- Be warm but professional
- Include a clear value proposition
- End with a simple call to action

Return ONLY the message text.""")

    if text:
        # Post draft to Discord for human review
        agent = task.get("agent", "dandan")
        subprocess.run(
            ["bash", "/home/mumega/scripts/discord-reply.sh", agent, "control",
             f"**📧 Outreach Draft** (needs review):\n\n{text}"],
            capture_output=True, timeout=10,
        )
        return {"success": True, "result": f"Outreach draft posted to Discord for review"}
    return {"success": False, "result": "Outreach draft generation failed"}


def execute_research(task: dict) -> dict:
    """Research using Gemma 4."""
    desc = task.get("description", task.get("title", ""))
    text = call_gemma4(f"Research this topic concisely: {desc}. Provide key findings in bullet points.")
    if text:
        requests.post(f"{MIRROR_URL}/store", json={
            "agent": "loop",
            "text": text,
            "context_id": f"research_{int(time.time())}",
            "core_concepts": ["research", "loop-generated"],
        }, headers=MIRROR_HEADERS, timeout=10)
        return {"success": True, "result": f"Research stored in Mirror ({len(text)} chars)"}
    return {"success": False, "result": "Research failed"}


def execute_generic(task: dict) -> dict:
    """Try to handle any task with Gemma 4."""
    desc = task.get("description", task.get("title", ""))
    text = call_gemma4(f"Complete this task: {desc}. Provide the result directly.")
    if text:
        return {"success": True, "result": text[:500]}
    return {"success": False, "result": "Could not execute"}


def route_to_remote_agent(task: dict, agent: str, skill_matches: list[dict], squad_state: dict) -> dict:
    """Route squad tasks to worker/codex via Redis with reply metadata."""
    task_id = task.get("id", "")
    title = task.get("title", "")
    desc = task.get("description", "")

    try:
        r = get_redis_client()
        payload_obj = {
            "task_id": task_id,
            "title": title,
            "description": desc,
            "priority": task.get("priority", "medium"),
            "source": "sovereign-loop",
            "project": task.get("project", ""),
            "squad_id": task.get("squad_id", ""),
            "labels": task.get("labels", []),
            "skill_matches": skill_matches,
            "squad_state": squad_state,
            "reply_to_agent": REPLY_AGENT,
            "reply_stream": REPLY_STREAM,
            "completion_endpoint": f"{SQUAD_URL}/tasks/{task_id}/complete",
        }
        data_obj = {
            "type": "task_dispatch",
            "source": "sovereign-loop",
            "target": f"agent:{agent}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": payload_obj,
        }
        r.xadd(f"sos:stream:sos:channel:private:agent:{agent}", {
            "type": "task_dispatch",
            "source": "sovereign-loop",
            "payload": json.dumps(payload_obj),
            "data": json.dumps(data_obj),
        })
        r.publish(f"sos:wake:{agent}", json.dumps({
            "text": f"New task: {title}",
            "task_id": task_id,
            "reply_to": REPLY_AGENT,
        }))
        logger.info(f"Routed to remote agent: {agent}")
        return {"success": True, "result": f"Dispatched to {agent}", "deferred": True}
    except Exception as e:
        logger.error(f"Remote dispatch failed: {e}, falling back to generic")
        return execute_generic(task)


def escalate_to_hermes(task: dict) -> dict:
    """
    Web search via Hermes (primary) → Claude Code bus dispatch (backup).

    Hermes has native web_search + web_extract tools.
    If Hermes binary is not available, falls back to dispatching the research
    task to the kasra bus agent (which has WebSearch/WebFetch natively).

    Config knobs:
      HERMES_BIN   path to hermes binary (default: ~/.hermes/bin/hermes)
      HERMES_MODEL model override (default: config.yaml default)
    """
    title = task.get("title", "")
    desc = task.get("description", "")
    task_id = task.get("id", "")
    query = desc or title

    hermes_bin = Path(os.environ.get("HERMES_BIN", Path.home() / ".hermes" / "bin" / "hermes"))

    # ── Primary: Hermes ───────────────────────────────────────────────────────
    if hermes_bin.exists():
        try:
            prompt = (
                f"Research task: {title}\n\n{desc}\n\n"
                "Use web_search and web_extract to gather current information. "
                "Return a concise summary of key findings (bullet points, under 500 words)."
            )
            model_flag = os.environ.get("HERMES_MODEL", "")
            cmd = [str(hermes_bin), "--yes"]
            if model_flag:
                cmd += ["--model", model_flag]
            cmd += ["--prompt", prompt]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            output = result.stdout.strip()
            if result.returncode == 0 and output:
                logger.info(f"Hermes research complete for task {task_id}")
                return {"success": True, "result": output, "method": "hermes"}
            logger.warning(f"Hermes exited {result.returncode}: {result.stderr[:200]}")
        except subprocess.TimeoutExpired:
            logger.warning("Hermes timed out after 120s — falling back to Claude Code bus")
        except Exception as e:
            logger.warning(f"Hermes failed: {e} — falling back to Claude Code bus")

    # ── Backup: Claude Code agent via SOS bus ────────────────────────────────
    try:
        r = get_redis_client()
        payload = {
            "type": "task_dispatch",
            "source": "sovereign-loop",
            "task_id": task_id,
            "title": title,
            "description": desc,
            "method": "research",
            "text": (
                f"Research task (web search needed): {title}\n\n{desc}\n\n"
                "Use WebSearch + WebFetch tools. Store findings in Mirror and reply with summary."
            ),
        }
        r.publish("sos:stream:global:agent:kasra", json.dumps(payload))
        logger.info(f"Research task dispatched to kasra bus agent (task {task_id})")
        return {"success": True, "result": "Research dispatched to kasra agent", "method": "claude-code-bus", "deferred": True}
    except Exception as e:
        logger.error(f"Bus dispatch failed: {e}")
        return {"success": False, "result": "Both Hermes and bus dispatch failed"}


def escalate_to_tmux(task: dict, agent: str) -> dict:
    """Send task to a Claude Code agent in tmux. For heavy work."""
    task_id = task.get("id", "")
    title = task.get("title", "")
    desc = task.get("description", "")

    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", agent, "-p"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and "❯" in result.stdout:
            msg = f"Mirror task {task_id}: {title}. {desc}. When done, mark complete."
            subprocess.run(
                ["tmux", "send-keys", "-t", agent, msg, "Enter"],
                capture_output=True, timeout=5,
            )
            return {"success": True, "result": f"Escalated to tmux:{agent}"}
        else:
            return {"success": False, "result": f"tmux:{agent} is busy or not available"}
    except:
        return {"success": False, "result": f"tmux:{agent} not reachable"}


_gemini_rpm_blocked_until: float = 0.0  # epoch seconds when Gemini RPM cooldown expires
_openrouter_blocked_until: float = 0.0  # epoch seconds; set on 402 — skipped for 1 hour


def _is_gemini_rate_limit(exc: Exception) -> bool:
    """True when the exception is a Gemini per-minute rate limit (not a daily quota)."""
    msg = str(exc).lower()
    return "resource_exhausted" in msg or "429" in msg or "quota" in msg


def call_gemma4(prompt: str) -> str:
    """
    Call LLM with 5-tier failover. 99.9% availability.
    Vertex ADC → Gemini 2.5 Flash → GitHub Models → Gemini 2.5 Flash (key) → OpenRouter → Local Ollama gemma2:2b

    Tier 1 and Tier 3 share the same Gemini API key. If Tier 1 hits a per-minute
    rate limit, Tier 3 is skipped for 60s to avoid burning another call on the
    same capped key. GitHub (Tier 2) and OpenRouter (Tier 4) bridge the gap.
    """
    global _gemini_rpm_blocked_until
    import time as _time

    gemini_rpm_ok = _time.time() >= _gemini_rpm_blocked_until

    # Tier 0: Vertex AI ADC — Gemini 2.5 Flash, no free-tier quota limits
    # Stream I (S015): pay-per-token via GCP Free Trial credit, ADC auto-refreshes
    try:
        import os as _os
        import google.auth
        import google.auth.transport.requests
        import urllib.request as _urllib_request
        import json as _json
        _os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "mumega-com")
        _creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        _creds.refresh(google.auth.transport.requests.Request())
        _payload = _json.dumps({
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 1024},
        }).encode()
        _req = _urllib_request.Request(
            f"https://us-central1-aiplatform.googleapis.com/v1/projects/mumega-com/locations/us-central1/publishers/google/models/{_model_cfg()['tier0_vertex_model']}:generateContent",
            data=_payload,
            headers={"Authorization": f"Bearer {_creds.token}", "Content-Type": "application/json"},
        )
        with _urllib_request.urlopen(_req, timeout=30) as _r:
            _data = _json.loads(_r.read())
            return _data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        logger.warning(f"Tier 0 (Vertex ADC) failed: {e}")

    # Tier 1: Gemma 4 31B (free, best quality)
    if GEMINI_API_KEY and gemini_rpm_ok:
        try:
            from google import genai
            client = genai.Client(api_key=GEMINI_API_KEY)
            response = client.models.generate_content(model=_model_cfg()["tier1_primary"], contents=prompt)
            return response.text.strip()
        except Exception as e:
            logger.warning(f"Tier 1 (Gemma 4) failed: {e}")
            if _is_gemini_rate_limit(e):
                _gemini_rpm_blocked_until = _time.time() + 60
                gemini_rpm_ok = False
                logger.info("Gemini RPM limit hit — skipping Tier 3 (Gemini Flash) for 60s")

    # Tier 2: GitHub Models gpt-4o-mini (free)
    if GITHUB_TOKEN:
        try:
            from openai import OpenAI
            client = OpenAI(base_url="https://models.inference.ai.azure.com", api_key=GITHUB_TOKEN)
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.warning(f"Tier 2 (GitHub Models) failed: {e}")

    # Tier 3: Gemini Flash (free, fast) — skipped if Tier 1 hit RPM limit (same key)
    if GEMINI_API_KEY and gemini_rpm_ok:
        try:
            from google import genai
            client = genai.Client(api_key=GEMINI_API_KEY)
            response = client.models.generate_content(model=_model_cfg()["tier3_fallback"], contents=prompt)
            return response.text.strip()
        except Exception as e:
            logger.warning(f"Tier 3 (Gemini Flash) failed: {e}")
    elif not gemini_rpm_ok:
        logger.info("Tier 3 (Gemini Flash) skipped — RPM cooldown active")

    # Tier 4: OpenRouter free (28 free models, auto-routes)
    global _openrouter_blocked_until
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    openrouter_ok = _time.time() >= _openrouter_blocked_until
    if openrouter_key and openrouter_ok:
        try:
            from openai import OpenAI
            client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=openrouter_key)
            response = client.chat.completions.create(
                model="openrouter/free",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            err_str = str(e)
            if "402" in err_str or "payment" in err_str.lower():
                _openrouter_blocked_until = _time.time() + 3600  # 1-hour cooldown on quota exhaustion
                logger.warning("Tier 4 (OpenRouter) 402 — free quota exhausted; skipping for 1 hour")
            else:
                logger.warning(f"Tier 4 (OpenRouter) failed: {e}")
    elif openrouter_key and not openrouter_ok:
        logger.info("Tier 4 (OpenRouter) skipped — 402 cooldown active")

    # Tier 5: Local Ollama — gemma2:2b, always on, zero cost, CPU-only
    ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    try:
        from openai import OpenAI
        client = OpenAI(base_url=f"{ollama_url}/v1", api_key="ollama")
        response = client.chat.completions.create(
            model="gemma2:2b",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            timeout=30,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"Tier 5 (Ollama gemma2:2b) failed: {e}")

    logger.error("ALL TIERS FAILED — no LLM available")
    return ""


def record_progress(task: dict, result: dict):
    """Append to progress file (Ralph pattern)."""
    now = datetime.now(timezone.utc).isoformat()[:19]
    line = f"[{now}] {'✅' if result['success'] else '❌'} {task.get('title','?')[:60]} → {result['result'][:100]}\n"
    with open(PROGRESS_FILE, "a") as f:
        f.write(line)


def report_discord(task: dict, result: dict):
    """Report to Discord."""
    status = "✅" if result["success"] else "❌"
    msg = f"**[LOOP]** {status} {task.get('title','?')[:50]}\nResult: {result['result'][:200]}"
    subprocess.run(
        ["bash", "/home/mumega/scripts/discord-reply.sh", "brain", "control", msg],
        capture_output=True, timeout=10,
    )


def clean_duplicates():
    """Remove duplicate backlog tasks."""
    try:
        r = requests.get(f"{MIRROR_URL}/tasks?status=backlog", headers=MIRROR_HEADERS, timeout=10)
        tasks = r.json().get("tasks", [])
        seen = {}
        for t in tasks:
            key = t.get("title", "")[:50]
            if key in seen:
                requests.put(f"{MIRROR_URL}/tasks/{t['id']}", json={"status": "canceled"},
                             headers=MIRROR_HEADERS, timeout=5)
                logger.info(f"Deduped: {t['id']}")
            else:
                seen[key] = t["id"]
    except:
        pass


# ============================================
# THE LOOP
# ============================================

def cycle() -> bool:
    """One loop cycle. Returns True if work was done."""
    logger.info("=== LOOP CYCLE ===")
    completed_from_replies = drain_async_completions()

    # Clean duplicates first
    clean_duplicates()

    # Get tasks
    tasks = get_pending_tasks()
    if not tasks:
        logger.info("No pending tasks. Idle.")
        # Check out of any open sessions — project queues are empty
        for project_id in list(_open_sessions.keys()):
            _session_checkout(project_id)
        return completed_from_replies > 0

    logger.info(f"{len(tasks)} pending tasks")

    # Pick one — try up to 3 candidates in priority order to skip stale claims.
    # Without this, a single stuck "critical" task starves the entire backlog.
    remaining = list(tasks)
    result = None
    task = None
    for _attempt in range(10):
        if not remaining:
            break
        candidate = pick_task(remaining)
        remaining = [t for t in remaining if (t.get("id") or t.get("title","")) != (candidate.get("id") or candidate.get("title",""))]
        logger.info(f"Picked: [{candidate.get('priority','?')}] {candidate.get('title','?')[:60]}")

        # Heartbeat for the project session if open
        project_id = candidate.get("project", "")
        if project_id:
            _session_heartbeat(project_id)

        # Execute
        result = execute_task(candidate)
        logger.info(f"Result: {result['success']} — {result['result'][:100]}")

        if result.get("skipped"):
            logger.info("Skipped claimed task — trying next candidate.")
            continue  # try next task instead of giving up

        task = candidate
        break

    if task is None:
        logger.info("All candidate tasks were already claimed. Idle this cycle.")
        return completed_from_replies > 0

    # Mark done or failed
    if result.get("deferred"):
        logger.info("Task dispatched asynchronously; waiting for Redis completion.")
    elif result["success"]:
        mark_task(task, "done")
    else:
        mark_task(task, "blocked", result["result"])

    completed_from_replies += drain_async_completions()

    # Record + report
    record_progress(task, result)
    report_discord(task, result)

    logger.info("=== CYCLE COMPLETE ===")
    return True


def daemon(interval_minutes: int = 10):
    """
    Run continuously with adaptive speed.
    - Work available: cycle every 2 min (burst mode)
    - No work: cycle every 10 min (idle mode)
    - After 5 consecutive idle cycles: slow to 30 min
    """
    logger.info(f"Loop daemon starting — adaptive speed")
    register_squad_skills()

    subprocess.run(
        ["bash", "/home/mumega/scripts/discord-reply.sh", "brain", "control",
         "**🔄 Sovereign Loop v2 started.** Adaptive speed: burst when busy, slow when idle."],
        capture_output=True, timeout=10,
    )

    idle_count = 0
    cycle_count = 0

    while True:
        try:
            did_work = cycle()
            cycle_count += 1

            if did_work:
                idle_count = 0
                tasks = get_pending_tasks()
                if tasks:
                    logger.info(f"BURST: {len(tasks)} more tasks. Next cycle in 2min.")
                    time.sleep(120)
                    continue
                else:
                    time.sleep(300)  # 5 min after completing last task
                    continue
            else:
                idle_count += 1

        except Exception as e:
            logger.error(f"Cycle crashed: {e}")
            idle_count += 1

        # Adaptive sleep
        if idle_count >= 5:
            time.sleep(1800)  # 30 min — deep idle
        elif idle_count >= 2:
            time.sleep(600)  # 10 min — light idle
        else:
            time.sleep(300)  # 5 min — recently active


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv("/home/mumega/.env.secrets")
    load_dotenv("/home/mumega/therealmofpatterns/.env")
    load_dotenv("/home/mumega/dentalnearyou/.env", override=False)
    register_squad_skills()

    if "--daemon" in sys.argv:
        daemon()
    elif "--once" in sys.argv:
        cycle()
    else:
        cycle()
