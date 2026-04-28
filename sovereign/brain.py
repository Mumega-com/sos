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
import requests
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

from kernel.config import MIRROR_URL, MIRROR_TOKEN, SQUAD_URL, SOS_ENGINE_URL

MIRROR_HEADERS = {"Authorization": f"Bearer {MIRROR_TOKEN}", "Content-Type": "application/json"}

SQUAD_TOKEN = os.environ.get("SOS_SYSTEM_TOKEN", "sk-sos-system")
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
    Model: Gemma 4 31B (free, 1500 req/day, excellent reasoning)

    Given system state, goals, and objections → decide what to do next.
    """
    if not GEMINI_API_KEY:
        return fallback_think(context)

    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)

        prompt = f"""You are the Sovereign Brain of Mumega — an autonomous AI operating system.

Your job: look at the current system state, active goals, and objections.
Pick the ONE highest-impact action that can be done RIGHT NOW with available tools.

Rules:
- Pick actions that are CONCRETE and EXECUTABLE (not "plan to do X" but "do X")
- Prefer actions that resolve objections blocking high-priority goals
- Prefer actions with zero or low token cost (use free models when possible)
- If nothing is urgent, pick maintenance work (content, outreach, memory cleanup)
- Output ONLY a JSON object, no explanation

SYSTEM STATE:
{context}

Respond with EXACTLY this JSON format:
{{
  "action": "one-line description of what to do",
  "goal_id": "which goal this advances (or 'maintenance')",
  "agent": "which agent should do it (kasra/athena/sol/dandan/system)",
  "method": "how to do it (create_task/post_content/send_outreach/fix_code/research)",
  "details": "specific instructions for the executing agent",
  "expected_progress": 0.1,
  "risk": 0.1
}}"""

        response = client.models.generate_content(
            model=BRAIN_MODEL,
            contents=prompt,
        )

        # Parse JSON from response
        text = response.text.strip()
        # Handle markdown code blocks
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        return text

    except Exception as e:
        logger.error(f"Prefrontal (Gemma 4) failed: {e}")
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

    # Try Gemini as secondary fallback
    if GEMINI_API_KEY:
        try:
            from google import genai
            client = genai.Client(api_key=GEMINI_API_KEY)
            response = client.models.generate_content(
                model=BRAIN_MODEL,
                contents=f"Given this system state, pick ONE concrete action. Respond with JSON only: action, goal_id, agent, method, details, expected_progress (float), risk (float).\n\n{context[:3000]}",
            )
            return response.text.strip()
        except Exception as e:
            logger.error(f"Fallback Gemini failed: {e}")

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


# Agent name → tmux session name (empty string = system/no session needed)
_AGENT_SESSION: dict[str, str] = {
    "kasra": "kasra",
    "athena": "athena",
    "river": "river",
    "sol": "sol",
    "dandan": "dandan",
    "system": "",
}


def _agent_available(agent: str) -> bool:
    """Return True if the agent has a running tmux session (or needs none)."""
    import subprocess
    session = _AGENT_SESSION.get(agent, "")
    if not session:
        return True  # system / unknown agents — no session requirement
    try:
        result = subprocess.run(
            ["tmux", "has-session", "-t", session],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return True  # assume available if we can't check


def motor_execute(action: dict) -> dict:
    """
    MOTOR CORTEX — Execution
    Takes the decision from prefrontal and executes it.
    Returns result dict.
    """
    method = action.get("method", "")
    details = action.get("details", "")
    agent = action.get("agent", "system")
    action_title = action.get("action", "")

    # Agent availability check — skip if the target agent has no running session
    if not _agent_available(agent):
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

    project = normalize_project(action.get("goal_id", "mumega").replace("goal_", ""))
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

    try:
        if method == "create_task":
            title = action.get("action", "Brain-generated task")

            if squad_id:
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
            # Generate content using a cheap model and store it.
            # Skipped gracefully when BRAIN_CONTENT_MODE=off.
            content = _generate_content(details)
            if content == "__CONTENT_MODE_OFF__":
                return {"success": True, "result": "Content posting skipped (BRAIN_CONTENT_MODE=off)"}
            if content:
                # Store in Supabase via a simple approach — create a Mirror engram
                requests.post(f"{MIRROR_URL}/store", json={
                    "agent": "brain",
                    "text": content,
                    "context_id": f"brain_content_{int(time.time())}",
                    "core_concepts": ["content", "brain_generated"],
                }, headers=MIRROR_HEADERS, timeout=10)
                return {"success": True, "result": f"Content generated ({len(content)} chars)"}
            return {"success": False, "result": "Content generation failed"}

        elif method == "send_outreach":
            # Route outreach through squad if project has one
            outreach_project = normalize_project("dentalnearyou" if "dent" in details.lower() or agent == "dandan" else project)
            outreach_labels = method_labels["send_outreach"]
            squad_id = resolve_squad(outreach_labels, outreach_project)
            outreach_assignee = PROJECT_LEADS.get(outreach_project, agent)
            if squad_id:
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
            if squad_id:
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
            # Create research task for River
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
    # Fallback: Gemini
    if GEMINI_API_KEY:
        try:
            from google import genai
            client = genai.Client(api_key=GEMINI_API_KEY)
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

    # Get goals from Mirror
    try:
        r = requests.post(f"{MIRROR_URL}/search", json={
            "query": "GOAL active",
            "top_k": 10,
            "agent_filter": "os",
        }, headers=MIRROR_HEADERS, timeout=10)
        results = r.json().get("results", [])
        goals = []
        for result in results:
            raw = result.get("raw_data", {})
            if raw.get("goal"):
                g = raw["goal"]
                goals.append(f"[{g.get('priority','?')}] {g.get('title','')} — progress: {g.get('progress',0):.0%}")
        if goals:
            state_parts.append("ACTIVE GOALS:\n" + "\n".join(goals))
    except:
        pass

    # Get objections
    try:
        r = requests.post(f"{MIRROR_URL}/search", json={
            "query": "OBJECTION active",
            "top_k": 10,
            "agent_filter": "os",
        }, headers=MIRROR_HEADERS, timeout=10)
        results = r.json().get("results", [])
        objections = []
        for result in results:
            raw = result.get("raw_data", {})
            if raw.get("objection"):
                o = raw["objection"]
                objections.append(f"[{o.get('type','?')} {o.get('intensity',0):.1f}] {o.get('description','')[:80]}")
        if objections:
            state_parts.append("ACTIVE OBJECTIONS:\n" + "\n".join(objections))
    except:
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
            ["python3", "/home/mumega/SOS/sos/services/economy/metabolism.py", "status"],
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
    payload = json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(),
        "success": 1 if result.get("success") else 0,
        "task_title": action.get("action", ""),
        "method": action.get("method", ""),
        "agent": action.get("agent", ""),
        "model": BRAIN_MODEL,
        "result": str(result.get("result", ""))[:500],
        "cycle_ms": cycle_ms,
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
    """Post brain cycle result to Discord #control."""
    if not DISCORD_BOT_TOKEN:
        return

    now = datetime.now(timezone.utc).strftime("%H:%M")
    status = "✅" if result.get("success") else "❌"
    msg = (
        f"`{now}` **[BRAIN]** {status} {action.get('action', '?')}\n"
        f"Agent: {action.get('agent', '?')} | Method: {action.get('method', '?')}\n"
        f"Result: {result.get('result', '?')[:200]}"
    )

    try:
        from kernel.bus import send as bus_send
        if not bus_send(to="kasra", text=msg):
            # fallback: Discord
            import subprocess
            subprocess.run(
                ["bash", "/home/mumega/scripts/discord-reply.sh", "brain", "control", msg],
                capture_output=True, timeout=10,
            )
    except Exception:
        pass


def remember(action: dict, result: dict):
    """Store brain cycle in Mirror for learning."""
    try:
        requests.post(f"{MIRROR_URL}/store", json={
            "agent": "brain",
            "context_id": f"brain_cycle_{int(time.time())}",
            "text": f"Brain action: {action.get('action','')} → {result.get('result','')}",
            "core_concepts": ["brain", "cycle", action.get("method", ""), action.get("agent", "")],
            "raw_data": {"action": action, "result": result},
        }, headers=MIRROR_HEADERS, timeout=10)
    except:
        pass


# ============================================
# THE LIVING LOOP
# ============================================

def cycle():
    """One brain cycle: perceive → think → act → remember → report."""
    import time as _time
    _cycle_start = _time.monotonic()
    logger.info("=== BRAIN CYCLE START ===")

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
    load_dotenv("/home/mumega/.env.secrets")
    load_dotenv("/home/mumega/therealmofpatterns/.env")

    if "--daemon" in sys.argv:
        daemon()
    else:
        cycle()
