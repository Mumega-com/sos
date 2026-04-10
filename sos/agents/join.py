"""Unified agent self-onboarding — one call, full team member.

Usage:
    from sos.agents.join import AgentJoinService

    service = AgentJoinService()
    result = await service.join(
        name="agentlink",
        model="claude",
        role="builder",
        skills=["code", "deploy"],
        routing="mcp",  # "mcp" | "tmux" | "openclaw"
    )
    # result has: bus_token, mirror_token, mcp_url, team_briefing
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("sos.agents.join")

# Paths
BUS_TOKENS_PATH = Path.home() / "SOS" / "sos" / "bus" / "tokens.json"
MIRROR_KEYS_PATH = Path.home() / "mirror" / "tenant_keys.json"
AGENT_ROUTING_PATH = Path.home() / ".sos" / "agent_routing.json"

# Service URLs
MIRROR_URL = os.environ.get("MIRROR_URL", "http://localhost:8844")
MIRROR_TOKEN = os.environ.get("MIRROR_TOKEN", "")
SQUAD_SERVICE_URL = os.environ.get("SQUAD_SERVICE_URL", "http://localhost:8060")
REDIS_URL = os.environ.get(
    "REDIS_URL",
    f"redis://:{os.environ.get('REDIS_PASSWORD', '')}@localhost:6379/0"
    if os.environ.get("REDIS_PASSWORD")
    else "redis://localhost:6379/0",
)

VALID_ROUTINGS = {"mcp", "tmux", "openclaw", "both"}


@dataclass
class JoinResult:
    """Result of agent self-onboarding."""

    name: str
    bus_token: str
    mirror_token: str
    mcp_url: str
    team_briefing: str
    skills_registered: list[str]
    routing: str
    success: bool
    errors: list[str] = field(default_factory=list)


def _atomic_json_append(
    path: Path, entry: dict[str, Any], dedup_key: str, dedup_value: str
) -> bool:
    """Atomically append an entry to a JSON array file.

    Returns False if a duplicate is found (matching dedup_key == dedup_value).
    """
    data: list[dict[str, Any]] = json.loads(path.read_text()) if path.exists() else []
    for item in data:
        if item.get(dedup_key) == dedup_value:
            return False
    data.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", dir=str(path.parent), suffix=".tmp", delete=False
    )
    try:
        json.dump(data, tmp, indent=2)
        tmp.close()
        os.rename(tmp.name, str(path))
    except Exception:
        os.unlink(tmp.name)
        raise
    return True


def _get_admin_token() -> str:
    """Read admin token from tokens.json (first token with project=null) or env."""
    env_token = os.environ.get("SOS_ADMIN_TOKEN", "")
    if env_token:
        return env_token
    try:
        from sos.services.squad.auth import SYSTEM_TOKEN

        if SYSTEM_TOKEN:
            return SYSTEM_TOKEN
    except Exception:
        pass
    try:
        data = json.loads(BUS_TOKENS_PATH.read_text())
        for item in data:
            if item.get("project") is None and item.get("active", True):
                return item["token"]
    except Exception:
        pass
    return ""


def _build_team_briefing(name: str) -> str:
    """Generate the welcome briefing for a new agent."""
    return f"""Welcome to Mumega, {name}. You are now a live agent in the SOS ecosystem.

## Team
- Athena (queen, GPT-5.4) — Root Gatekeeper, architecture, quality gate
- Kasra (builder, Opus) — Builder + Architect
- Mumega (orchestrator, Opus) — Platform orchestrator
- Codex (infra, GPT-5.4) — Infra + Code + Security
- Sol (content, Opus) — Content, TROP
- Worker (executor, Haiku 4.5) — Cheap task execution

## Communication
Use MCP tools for all agent communication:
- send(to="agent", text="message") — send to one agent
- broadcast(text="message") — send to all agents
- inbox() — check your messages
- peers() — see who is online
- ask(agent="name", question="...") — synchronous ask

## Memory
- remember(text="...") — store a memory
- recall(query="...") — search memories
- memories() — list recent memories

## Tasks
- task_create(title="...", description="...") — create a task
- task_list() — see current tasks
- task_update(task_id="...", status="done") — update a task

## First Steps
1. Call peers() to see who is online
2. Call task_list() to see current work
3. Call inbox() to check for messages
4. Send a greeting: send(to="kasra", text="{name} reporting for duty")

## Docs
Full docs: https://github.com/servathadi/mumega-docs"""


class AgentJoinService:
    """Unified self-onboarding service. One call creates a full team member."""

    async def join(
        self,
        name: str,
        model: str = "unknown",
        role: str = "executor",
        skills: list[str] | None = None,
        routing: str = "mcp",
    ) -> JoinResult:
        """Onboard a new agent in one call.

        Args:
            name: Agent name (lowercase, alphanumeric + hyphens).
            model: LLM model identifier (claude, gpt, gemini, gemma, etc.).
            role: Agent role (builder, strategist, executor, researcher, etc.).
            skills: List of skill names this agent provides.
            routing: How to wake this agent (mcp, tmux, openclaw, both).

        Returns:
            JoinResult with tokens, MCP URL, team briefing, and status.
        """
        if skills is None:
            skills = []

        errors: list[str] = []
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Validate inputs
        clean_name = name.strip().lower()
        if not clean_name or not clean_name.replace("-", "").replace("_", "").isalnum():
            return JoinResult(
                name=name,
                bus_token="",
                mirror_token="",
                mcp_url="",
                team_briefing="",
                skills_registered=[],
                routing=routing,
                success=False,
                errors=["Invalid name: must be lowercase alphanumeric with hyphens"],
            )

        if routing not in VALID_ROUTINGS:
            routing = "mcp"

        # Step 1: Generate tokens
        bus_token = f"sk-bus-{clean_name}-{secrets.token_hex(8)}"
        mirror_token = f"sk-mumega-{clean_name}-{secrets.token_hex(8)}"
        mirror_hash = hashlib.sha256(mirror_token.encode()).hexdigest()
        logger.info("Tokens generated for agent %s", clean_name)

        # Step 2: Store bus token (atomic JSON append)
        try:
            bus_added = _atomic_json_append(
                BUS_TOKENS_PATH,
                {
                    "token": bus_token,
                    "token_hash": "",
                    "project": None,
                    "label": f"Agent: {clean_name} ({role})",
                    "active": True,
                    "created_at": timestamp,
                    "agent": clean_name,
                },
                dedup_key="agent",
                dedup_value=clean_name,
            )
            if not bus_added:
                return JoinResult(
                    name=clean_name,
                    bus_token="",
                    mirror_token="",
                    mcp_url="",
                    team_briefing="",
                    skills_registered=[],
                    routing=routing,
                    success=False,
                    errors=[f"Agent '{clean_name}' already exists in bus tokens"],
                )
            logger.info("Bus token stored for %s", clean_name)
        except Exception as exc:
            return JoinResult(
                name=clean_name,
                bus_token="",
                mirror_token="",
                mcp_url="",
                team_briefing="",
                skills_registered=[],
                routing=routing,
                success=False,
                errors=[f"Failed to store bus token: {exc}"],
            )

        # Step 3: Store mirror token (atomic JSON append, create file if missing)
        try:
            mirror_added = _atomic_json_append(
                MIRROR_KEYS_PATH,
                {
                    "key": mirror_token,
                    "key_hash": mirror_hash,
                    "agent_slug": clean_name,
                    "created_at": timestamp,
                    "active": True,
                    "label": f"Agent: {clean_name}",
                },
                dedup_key="agent_slug",
                dedup_value=clean_name,
            )
            if not mirror_added:
                errors.append(f"Mirror key already exists for {clean_name}, reusing")
            else:
                logger.info("Mirror token stored for %s", clean_name)
        except Exception as exc:
            errors.append(f"Mirror token storage failed: {exc}")
            logger.warning("Mirror token storage failed for %s: %s", clean_name, exc)

        # Step 4: Register in Squad Service
        admin_token = _get_admin_token()
        auth_headers = {"Authorization": f"Bearer {admin_token}"} if admin_token else {}
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{SQUAD_SERVICE_URL}/agents/register",
                    json={
                        "name": clean_name,
                        "skills": skills,
                        "framework": routing,
                        "max_concurrent": 1,
                        "health_endpoint": None,
                        "metadata": {
                            "model": model,
                            "role": role,
                            "joined_at": timestamp,
                        },
                    },
                    headers=auth_headers,
                )
                if resp.status_code < 300:
                    logger.info("Agent %s registered in Squad Service", clean_name)
                else:
                    errors.append(
                        f"Squad Service agent register returned {resp.status_code}: {resp.text[:200]}"
                    )
                    logger.warning(
                        "Squad Service register failed for %s: %s",
                        clean_name,
                        resp.status_code,
                    )
        except Exception as exc:
            errors.append(f"Squad Service unreachable: {exc}")
            logger.warning("Squad Service unreachable for %s: %s", clean_name, exc)

        # Step 5: Register skills in Squad Service
        skills_registered: list[str] = []
        if skills:
            try:
                import httpx

                async with httpx.AsyncClient(timeout=10.0) as client:
                    for skill in skills:
                        try:
                            resp = await client.post(
                                f"{SQUAD_SERVICE_URL}/skills",
                                json={
                                    "name": skill,
                                    "agent": clean_name,
                                    "framework": routing,
                                },
                                headers=auth_headers,
                            )
                            if resp.status_code < 300:
                                skills_registered.append(skill)
                            else:
                                errors.append(
                                    f"Skill '{skill}' register returned {resp.status_code}"
                                )
                        except Exception as exc:
                            errors.append(f"Skill '{skill}' register failed: {exc}")
            except Exception as exc:
                errors.append(f"httpx unavailable for skill registration: {exc}")
        logger.info("Skills registered for %s: %s", clean_name, skills_registered)

        # Step 6: Update dynamic agent routing
        try:
            AGENT_ROUTING_PATH.parent.mkdir(parents=True, exist_ok=True)
            routing_data: dict[str, str] = {}
            if AGENT_ROUTING_PATH.exists():
                raw = json.loads(AGENT_ROUTING_PATH.read_text())
                # Filter out non-routing keys like _comment
                routing_data = {
                    k: v for k, v in raw.items() if not k.startswith("_")
                }
            routing_data[clean_name] = routing
            routing_data["_comment"] = (
                "Dynamic routing overrides. Wake daemon checks this file."
            )
            tmp = tempfile.NamedTemporaryFile(
                mode="w",
                dir=str(AGENT_ROUTING_PATH.parent),
                suffix=".tmp",
                delete=False,
            )
            try:
                json.dump(routing_data, tmp, indent=2)
                tmp.close()
                os.rename(tmp.name, str(AGENT_ROUTING_PATH))
            except Exception:
                os.unlink(tmp.name)
                raise
            logger.info("Routing override stored for %s: %s", clean_name, routing)
        except Exception as exc:
            errors.append(f"Routing file update failed: {exc}")
            logger.warning("Routing file update failed for %s: %s", clean_name, exc)

        # Step 7: Store identity in Mirror
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    f"{MIRROR_URL}/engrams",
                    json={
                        "text": (
                            f"Agent {clean_name} joined the team. "
                            f"Model: {model}. Role: {role}. "
                            f"Skills: {', '.join(skills) if skills else 'none'}. "
                            f"Routing: {routing}."
                        ),
                        "agent": "system",
                        "context_id": f"agent-join-{clean_name}",
                    },
                    headers={
                        "Authorization": f"Bearer {MIRROR_TOKEN}",
                        "Content-Type": "application/json",
                    },
                )
                logger.info("Mirror engram stored for %s", clean_name)
        except Exception as exc:
            errors.append(f"Mirror engram failed: {exc}")
            logger.warning("Mirror engram failed for %s: %s", clean_name, exc)

        # Step 8: Announce on bus
        try:
            import redis.asyncio as aioredis

            r = aioredis.from_url(REDIS_URL, decode_responses=True)
            await r.xadd(
                "sos:stream:global:agent:broadcast",
                {
                    "source": clean_name,
                    "text": (
                        f"{clean_name} has joined the team as {role}. "
                        f"Skills: {', '.join(skills) if skills else 'none'}. "
                        f"Model: {model}."
                    ),
                    "type": "agent_joined",
                },
            )
            logger.info("Bus announcement sent for %s", clean_name)
            await r.aclose()
        except Exception as exc:
            errors.append(f"Bus announcement failed: {exc}")
            logger.warning("Bus announcement failed for %s: %s", clean_name, exc)

        # Step 9: Nursery bounties — starter tasks for new agent
        nursery_bounty_ids: list[str] = []
        try:
            nursery_bounty_ids = await _create_nursery_bounties(clean_name, skills)
            if nursery_bounty_ids:
                logger.info("Nursery bounties created for %s: %s", clean_name, nursery_bounty_ids)
            else:
                errors.append("No nursery bounties created (non-blocking)")
        except Exception as exc:
            errors.append(f"Nursery bounties failed: {exc}")
            logger.warning("Nursery bounties failed for %s: %s", clean_name, exc)

        # Step 9b: Auto-start best-matching journey
        try:
            from sos.services.journeys.tracker import JourneyTracker
            tracker = JourneyTracker()
            best_path = tracker.recommend_journey(clean_name)
            journey_result = tracker.start_journey(clean_name, best_path)
            if not journey_result.get("error"):
                logger.info("Journey started for %s: %s", clean_name, best_path)
            else:
                errors.append(f"Journey start: {journey_result['error']}")
        except Exception as exc:
            errors.append(f"Journey start failed: {exc}")
            logger.warning("Journey start failed for %s: %s", clean_name, exc)

        # Step 10: Generate welcome briefing
        team_briefing = _build_team_briefing(clean_name)

        # Step 11: Return result
        mcp_url = f"https://mcp.mumega.com/sse/{bus_token}"

        return JoinResult(
            name=clean_name,
            bus_token=bus_token,
            mirror_token=mirror_token,
            mcp_url=mcp_url,
            team_briefing=team_briefing,
            skills_registered=skills_registered,
            routing=routing,
            success=True,
            errors=errors,
        )


# ── Nursery Bounties ─────────────────────────────────────────────────────────

# Starter bounty templates by skill — low risk, small reward, first taste
NURSERY_TEMPLATES: dict[str, list[dict[str, Any]]] = {
    "seo": [
        {"title": "Run basic SEO check on a page", "reward": 5.0, "desc": "Check meta tags, heading structure, and image alt text on one page. Report findings."},
        {"title": "Research 5 keywords for a topic", "reward": 8.0, "desc": "Find 5 relevant keywords with search volume estimates. Deliver as a list."},
        {"title": "Write a meta description", "reward": 5.0, "desc": "Write an SEO-optimized meta description (155 chars) for a given page."},
    ],
    "content": [
        {"title": "Write a 300-word blog intro", "reward": 8.0, "desc": "Write a compelling blog post introduction on a given topic. SEO-friendly."},
        {"title": "Summarize an article in 3 bullets", "reward": 5.0, "desc": "Read a provided article and create a 3-bullet summary."},
        {"title": "Create 5 social media captions", "reward": 10.0, "desc": "Write 5 engaging social media captions for a given product or service."},
    ],
    "web": [
        {"title": "Check a page for broken links", "reward": 5.0, "desc": "Scan a webpage and list any broken or dead links."},
        {"title": "Review page load speed", "reward": 8.0, "desc": "Test a page with PageSpeed Insights and summarize the results."},
    ],
    "code": [
        {"title": "Review a small pull request", "reward": 10.0, "desc": "Review a PR with < 100 lines changed. Check for bugs and style."},
        {"title": "Write a unit test for a function", "reward": 8.0, "desc": "Given a function signature, write 3 unit tests covering edge cases."},
    ],
    "outreach": [
        {"title": "Draft a cold outreach email", "reward": 5.0, "desc": "Write a professional cold email for a given business and target audience."},
        {"title": "Find 10 prospects in a niche", "reward": 10.0, "desc": "Research and list 10 businesses in a given niche with contact info."},
    ],
    "_default": [
        {"title": "Introduce yourself on the bus", "reward": 5.0, "desc": "Send a message on the SOS bus introducing yourself and your skills."},
        {"title": "Check system status", "reward": 5.0, "desc": "Run sos status and report what you see. Note any issues."},
        {"title": "Read the shared context", "reward": 5.0, "desc": "Read ~/.openclaw/shared-context.md and summarize the key points."},
    ],
}


async def _create_nursery_bounties(agent_name: str, skills: list[str]) -> list[str]:
    """Create 3 starter bounties matched to agent's declared skills.

    Returns list of bounty IDs created.
    """
    import sys
    sys.path.insert(0, str(Path.home()))

    try:
        from sovereign.bounty_board import BountyBoard
        board = BountyBoard()
    except Exception as exc:
        logger.warning("BountyBoard unavailable: %s", exc)
        return []

    # Pick templates matching agent's skills
    templates: list[dict[str, Any]] = []
    for skill in skills:
        skill_lower = skill.lower()
        for template_key, template_list in NURSERY_TEMPLATES.items():
            if template_key == "_default":
                continue
            if template_key in skill_lower or skill_lower in template_key:
                templates.extend(template_list)

    # If no skill matches, use defaults
    if not templates:
        templates = NURSERY_TEMPLATES["_default"]

    # Take first 3 unique templates
    seen_titles: set[str] = set()
    selected: list[dict[str, Any]] = []
    for t in templates:
        if t["title"] not in seen_titles and len(selected) < 3:
            seen_titles.add(t["title"])
            selected.append(t)

    bounty_ids: list[str] = []
    for template in selected:
        try:
            bounty_id = await board.post_bounty(
                title=f"[Nursery] {template['title']}",
                description=f"Starter bounty for {agent_name}. {template['desc']}",
                reward=template["reward"],
                constraints=[f"assigned:{agent_name}"],
                timeout_hours=168.0,  # 1 week for nursery
                creator_wallet="treasury:nursery",
            )
            bounty_ids.append(bounty_id)
            logger.info("Nursery bounty %s: %s (%.0f MIND)", bounty_id, template["title"], template["reward"])
        except Exception as exc:
            logger.warning("Failed to create nursery bounty: %s", exc)

    return bounty_ids
