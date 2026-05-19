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

import json
import logging
import os
import re
import secrets
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sos.bus.token_store import append_if_missing, hash_token, load_tokens
from sos.bus.tenant_provisioning import mint_or_get_mirror_key

logger = logging.getLogger("sos.agents.join")

# Paths
BUS_TOKENS_PATH = Path.home() / "SOS" / "sos" / "bus" / "tokens.json"
AGENT_ROUTING_PATH = Path.home() / ".sos" / "agent_routing.json"

# Service URLs
MIRROR_URL = os.environ.get("MIRROR_URL", "http://localhost:8844")
MIRROR_TOKEN = os.environ.get("MIRROR_TOKEN", "")
SQUAD_SERVICE_URL = os.environ.get("SQUAD_SERVICE_URL", "http://localhost:8060")
REDIS_URL = os.environ.get(
    "REDIS_URL",
    (
        f"redis://:{os.environ.get('REDIS_PASSWORD', '')}@localhost:6379/0"
        if os.environ.get("REDIS_PASSWORD")
        else "redis://localhost:6379/0"
    ),
)

VALID_ROUTINGS = {"mcp", "tmux", "openclaw", "both"}
AGENT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$")
MESH_ROLES = {"coordinator", "executor", "specialist", "oracle", "medic", "service", "human"}


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
    created, _ = append_if_missing(
        path,
        entry,
        lambda item: item.get(dedup_key) == dedup_value,
    )
    return created


def _get_admin_token() -> str:
    """Read admin token from env or tokens.json (first token with project=null).

    Resolution order uses scoped service credentials first, then on-disk
    bus tokens as a last resort. Importing the service module to read a
    literal ``os.getenv`` was a pure R2 leak (P1-05) with no benefit.
    """
    env_token = (
        os.environ.get("SOS_SQUAD_SYSTEM_TOKEN")
        or os.environ.get("SOS_SYSTEM_TOKEN")
        or os.environ.get("SOS_SQUAD_TOKEN")
        or os.environ.get("SOS_ADMIN_TOKEN")
    )
    if env_token:
        return env_token
    try:
        data = json.loads(BUS_TOKENS_PATH.read_text())
        for item in data:
            if item.get("project") is None and item.get("active", True):
                return item["token"]
    except Exception:
        pass
    return ""


def _get_registry_token() -> str:
    """Return the token expected by the Registry service."""
    return os.environ.get("SOS_REGISTRY_TOKEN") or os.environ.get("SOS_ADMIN_TOKEN") or ""


def _normalize_agent_name(name: str) -> str:
    """Convert human-facing agent names into the bus-safe slug format."""
    normalized = re.sub(r"[^a-z0-9]+", "-", name.strip().lower())
    return normalized.strip("-")


def _agent_slug_taken(name: str) -> bool:
    """Return True when an active bus token already owns *name*."""
    try:
        records = load_tokens(BUS_TOKENS_PATH)
    except Exception:
        return False
    return any(
        item.get("active", True) and item.get("agent") == name
        for item in records
    )


def _next_available_agent_name(base_name: str) -> tuple[str, bool]:
    """Pick a collision-safe agent slug.

    The human-facing name is a preference, not a global lock. If `hadi-codex`
    already exists, a second install becomes `hadi-codex-2` instead of failing
    onboarding and leaving the agent unreachable.
    """
    if not _agent_slug_taken(base_name):
        return base_name, False

    suffix = 2
    while suffix < 1000:
        candidate = f"{base_name}-{suffix}"
        if not _agent_slug_taken(candidate):
            return candidate, True
        suffix += 1

    digest = secrets.token_hex(3)
    return f"{base_name}-{digest}", True


def _skill_payload(skill: str, agent_name: str, routing: str) -> dict[str, Any]:
    """Build the Squad skill descriptor payload from a simple skill label."""
    skill_id = re.sub(r"[^a-z0-9]+", "-", skill.strip().lower()).strip("-")
    return {
        "id": f"{agent_name}-{skill_id}",
        "name": skill_id,
        "description": f"{agent_name} provides {skill_id} capability via {routing}.",
        "input_schema": {},
        "output_schema": {},
        "labels": [skill_id, agent_name],
        "keywords": [skill_id, agent_name, routing],
        "entrypoint": f"agent:{agent_name}",
        "skill_dir": "",
        "required_inputs": [],
        "fuel_grade": "diesel",
        "version": "1.0.0",
    }


def _mesh_role(role: str) -> str:
    """Map free-form onboarding roles onto AgentCard's closed role vocabulary."""
    role_slug = re.sub(r"[^a-z0-9]+", "-", role.strip().lower()).strip("-")
    if role_slug in MESH_ROLES:
        return role_slug
    if any(part in role_slug for part in ("review", "security", "devops", "research")):
        return "specialist"
    if any(part in role_slug for part in ("coord", "lead", "orchestr")):
        return "coordinator"
    return "executor"


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
Full docs: https://github.com/servathadi/mumega-docs
Local recovery guide: ~/SOS/docs/agent-onboarding-recovery.md"""


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
        requested_name = _normalize_agent_name(name)
        if not requested_name or not AGENT_NAME_RE.fullmatch(requested_name):
            return JoinResult(
                name=name,
                bus_token="",
                mirror_token="",
                mcp_url="",
                team_briefing="",
                skills_registered=[],
                routing=routing,
                success=False,
                errors=["Invalid name: must include lowercase letters or numbers"],
            )
        clean_name, renamed_for_collision = _next_available_agent_name(requested_name)

        if routing not in VALID_ROUTINGS:
            routing = "mcp"

        # Step 1: Generate bus token
        bus_token = f"sk-bus-{clean_name}-{secrets.token_hex(8)}"
        logger.info("Bus token generated for agent %s", clean_name)

        # Step 2: Store bus token (atomic JSON append)
        try:
            bus_added = _atomic_json_append(
                BUS_TOKENS_PATH,
                {
                    "token": bus_token,
                    "token_hash": hash_token(bus_token),
                    "project": None,
                    "label": f"Agent: {clean_name} ({role})",
                    "active": True,
                    "created_at": timestamp,
                    "agent": clean_name,
                    "scope": "agent",
                    "scopes": ["bus:send"],
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

        # Step 3: Mint or reuse Mirror token through the shared DB-first provisioner.
        mirror_token = ""
        try:
            mirror_token, mirror_added = mint_or_get_mirror_key(clean_name, f"Agent: {clean_name}")
            if mirror_added:
                logger.info("Mirror token provisioned for %s", clean_name)
            else:
                errors.append(f"Mirror key already exists for {clean_name}, reusing")
        except Exception as exc:
            errors.append(f"Mirror token provisioning failed: {exc}")
            logger.warning("Mirror token provisioning failed for %s: %s", clean_name, exc)

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
                            "requested_name": requested_name,
                            "renamed_for_collision": renamed_for_collision,
                        },
                    },
                    headers=auth_headers,
                )
                if resp.status_code < 300:
                    logger.info("Agent %s registered in Squad Service", clean_name)
                elif resp.status_code == 404:
                    logger.info(
                        "Squad Service agent registration endpoint unavailable for %s; "
                        "mesh registry remains authoritative",
                        clean_name,
                    )
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
                                json=_skill_payload(skill, clean_name, routing),
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
                routing_data = {k: v for k, v in raw.items() if not k.startswith("_")}
            routing_data[clean_name] = routing
            routing_data["_comment"] = "Dynamic routing overrides. Wake daemon checks this file."
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
                            f"Requested name: {requested_name}. "
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
                        f"Requested name: {requested_name}. "
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

        # Step 8.5: Enroll into mesh registry (v0.9.2)
        try:
            from sos.clients.registry import AsyncRegistryClient

            registry_token = _get_registry_token()
            mesh_client = AsyncRegistryClient(token=registry_token or None)
            await mesh_client.enroll_mesh(
                agent_id=f"agent:{clean_name}",
                name=clean_name,
                role=_mesh_role(role),
                skills=skills or [],
                squads=[],  # squads are assigned later via join flows, not at boot
            )
            logger.info("Mesh enrollment succeeded for %s", clean_name)
        except Exception as exc:
            errors.append(f"Mesh enrollment failed: {exc}")
            logger.warning("Mesh enrollment failed for %s: %s", clean_name, exc)

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

        # Step 9b: Auto-start best-matching journey via the journeys HTTP service
        try:
            from sos.clients.journeys import AsyncJourneysClient

            journeys = AsyncJourneysClient(token=admin_token or None)
            best_path = await journeys.recommend(clean_name)
            if best_path:
                await journeys.start(clean_name, best_path)
                logger.info("Journey started for %s: %s", clean_name, best_path)
        except Exception as exc:
            logger.info("Journey service unavailable for %s: %s", clean_name, exc)

        # Step 10: Generate welcome briefing
        team_briefing = _build_team_briefing(clean_name)
        if renamed_for_collision:
            team_briefing = (
                f"Requested name `{requested_name}` was already active, so your "
                f"live SOS identity is `{clean_name}`. Use `{clean_name}` for "
                f"bus messages, task assignment, and recovery.\n\n"
                + team_briefing
            )

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
        {
            "title": "Run basic SEO check on a page",
            "reward": 5.0,
            "desc": "Check meta tags, heading structure, and image alt text on one page. Report findings.",
        },
        {
            "title": "Research 5 keywords for a topic",
            "reward": 8.0,
            "desc": "Find 5 relevant keywords with search volume estimates. Deliver as a list.",
        },
        {
            "title": "Write a meta description",
            "reward": 5.0,
            "desc": "Write an SEO-optimized meta description (155 chars) for a given page.",
        },
    ],
    "content": [
        {
            "title": "Write a 300-word blog intro",
            "reward": 8.0,
            "desc": "Write a compelling blog post introduction on a given topic. SEO-friendly.",
        },
        {
            "title": "Summarize an article in 3 bullets",
            "reward": 5.0,
            "desc": "Read a provided article and create a 3-bullet summary.",
        },
        {
            "title": "Create 5 social media captions",
            "reward": 10.0,
            "desc": "Write 5 engaging social media captions for a given product or service.",
        },
    ],
    "web": [
        {
            "title": "Check a page for broken links",
            "reward": 5.0,
            "desc": "Scan a webpage and list any broken or dead links.",
        },
        {
            "title": "Review page load speed",
            "reward": 8.0,
            "desc": "Test a page with PageSpeed Insights and summarize the results.",
        },
    ],
    "code": [
        {
            "title": "Review a small pull request",
            "reward": 10.0,
            "desc": "Review a PR with < 100 lines changed. Check for bugs and style.",
        },
        {
            "title": "Write a unit test for a function",
            "reward": 8.0,
            "desc": "Given a function signature, write 3 unit tests covering edge cases.",
        },
    ],
    "outreach": [
        {
            "title": "Draft a cold outreach email",
            "reward": 5.0,
            "desc": "Write a professional cold email for a given business and target audience.",
        },
        {
            "title": "Find 10 prospects in a niche",
            "reward": 10.0,
            "desc": "Research and list 10 businesses in a given niche with contact info.",
        },
    ],
    "_default": [
        {
            "title": "Introduce yourself on the bus",
            "reward": 5.0,
            "desc": "Send a message on the SOS bus introducing yourself and your skills.",
        },
        {
            "title": "Check system status",
            "reward": 5.0,
            "desc": "Run sos status and report what you see. Note any issues.",
        },
        {
            "title": "Read the shared context",
            "reward": 5.0,
            "desc": "Read ~/.openclaw/shared-context.md and summarize the key points.",
        },
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
            logger.info(
                "Nursery bounty %s: %s (%.0f MIND)",
                bounty_id,
                template["title"],
                template["reward"],
            )
        except Exception as exc:
            logger.warning("Failed to create nursery bounty: %s", exc)

    return bounty_ids
