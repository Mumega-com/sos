"""
squad_activate.py — Squad activation and approval workflow for Mumega.

Connects the sovereign squad registry to actual task execution with
human-in-the-loop approval via Discord and Mirror API.

Usage:
    python squad_activate.py brief dentalnearyou          # Generate brief
    python squad_activate.py plan dentalnearyou            # Create plan from brief
    python squad_activate.py submit dentalnearyou          # Submit plan for approval
    python squad_activate.py approve <plan_id>             # Approve plan
    python squad_activate.py reject <plan_id> "reason"     # Reject plan
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TypeAlias

import httpx

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

from kernel.config import (
    MIRROR_URL as MIRROR_BASE,
    MIRROR_TOKEN as _MIRROR_TOKEN,
    DISCORD_SCRIPT as _DISCORD_SCRIPT,
    SOVEREIGN_PLANS_DIR,
)

MIRROR_AUTH = f"Bearer {_MIRROR_TOKEN}"
DISCORD_SCRIPT = Path(_DISCORD_SCRIPT)
PLANS_DIR = Path(SOVEREIGN_PLANS_DIR)

log = logging.getLogger("squad_activate")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

Effort: TypeAlias = str  # "low" | "medium" | "high"
Role: TypeAlias = str    # "analyst" | "optimizer" | "writer" | "reporter"

EFFORT_TOKENS: dict[Effort, int] = {
    "low": 5_000,
    "medium": 15_000,
    "high": 40_000,
}


class TaskStatus(str, Enum):
    blocked = "blocked"
    approved = "approved"
    rejected = "rejected"
    done = "done"


# ---------------------------------------------------------------------------
# Project defaults
# ---------------------------------------------------------------------------

DNU_DEFAULT: dict = {
    "name": "dentalnearyou",
    "url": "https://dentalnearyou.ca",
    "target_cities": [
        "Toronto", "Vancouver", "Calgary", "Edmonton", "Ottawa",
        "Mississauga", "Hamilton", "Winnipeg", "Montreal", "Quebec City",
    ],
    "key_pages": [
        "/en",
        "/en/[city]",
        "/en/[city]/[service]",
        "/en/dentist/[slug]",
        "/en/topics",
        "/en/cdcp",
    ],
    "kpis": [
        "organic traffic",
        "keyword rankings",
        "pages indexed",
        "backlinks",
    ],
    "primary_keywords": [
        "dentist [city]",
        "emergency dentist [city]",
        "dentist accepting new patients [city]",
    ],
    "business_model": "Lead generation — connects patients to dentists in Canadian cities",
}

PROJECT_DEFAULTS: dict[str, dict] = {
    "dentalnearyou": DNU_DEFAULT,
}


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class ClientBrief:
    project: str
    url: str
    target_cities: list[str]
    key_pages: list[str]
    kpis: list[str]
    primary_keywords: list[str]
    business_model: str
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return {
            "project": self.project,
            "url": self.url,
            "target_cities": self.target_cities,
            "key_pages": self.key_pages,
            "kpis": self.kpis,
            "primary_keywords": self.primary_keywords,
            "business_model": self.business_model,
            "generated_at": self.generated_at,
        }


@dataclass
class Objective:
    id: str
    title: str
    description: str
    role: Role
    effort: Effort
    token_budget: int
    task_id: str | None = None
    status: TaskStatus = TaskStatus.blocked

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "role": self.role,
            "effort": self.effort,
            "token_budget": self.token_budget,
            "task_id": self.task_id,
            "status": self.status.value,
        }


@dataclass
class SEOPlan:
    id: str
    project: str
    brief: ClientBrief
    objectives: list[Objective]
    status: TaskStatus = TaskStatus.blocked
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def total_token_budget(self) -> int:
        return sum(o.token_budget for o in self.objectives)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project": self.project,
            "brief": self.brief.to_dict(),
            "objectives": [o.to_dict() for o in self.objectives],
            "status": self.status.value,
            "total_token_budget": self.total_token_budget(),
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# Mirror API client
# ---------------------------------------------------------------------------

class MirrorClient:
    def __init__(self) -> None:
        self._headers = {
            "Authorization": MIRROR_AUTH,
            "Content-Type": "application/json",
        }

    async def search_memory(self, query: str, agent: str = "kasra", limit: int = 5) -> list[dict]:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{MIRROR_BASE}/memory/search",
                    headers=self._headers,
                    json={"query": query, "agent": agent, "limit": limit},
                )
                if resp.status_code == 200:
                    return resp.json().get("results", [])
        except httpx.ConnectError:
            log.warning("Mirror API unreachable — skipping memory search")
        return []

    async def create_task(
        self,
        title: str,
        description: str,
        labels: list[str],
        status: str = "blocked",
        metadata: dict | None = None,
    ) -> str | None:
        payload: dict = {
            "title": title,
            "description": description,
            "labels": labels,
            "status": status,
            "metadata": metadata or {},
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{MIRROR_BASE}/tasks",
                    headers=self._headers,
                    json=payload,
                )
                if resp.status_code in (200, 201):
                    data = resp.json()
                    task_id: str = data.get("id") or data.get("task_id", "")
                    log.info("Created Mirror task %s: %s", task_id, title)
                    return task_id
                log.warning("Mirror task creation failed %s: %s", resp.status_code, resp.text)
        except httpx.ConnectError:
            log.warning("Mirror API unreachable — task not created for: %s", title)
        return None

    async def update_task(self, task_id: str, updates: dict) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.put(
                    f"{MIRROR_BASE}/tasks/{task_id}",
                    headers=self._headers,
                    json=updates,
                )
                if resp.status_code in (200, 204):
                    log.info("Updated Mirror task %s → %s", task_id, updates.get("status", "?"))
                    return True
                log.warning("Mirror task update failed %s: %s", resp.status_code, resp.text)
        except httpx.ConnectError:
            log.warning("Mirror API unreachable — task %s not updated", task_id)
        return False


# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------

def post_discord(agent: str, channel: str, message: str) -> None:
    from kernel.bus import send as bus_send
    if bus_send(to=agent, text=message):
        return
    # fallback: Discord
    if not DISCORD_SCRIPT.exists():
        log.warning("discord-reply.sh not found at %s", DISCORD_SCRIPT)
        return
    try:
        result = subprocess.run(
            [str(DISCORD_SCRIPT), agent, channel, message],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            log.warning("discord-reply.sh exited %d: %s", result.returncode, result.stderr)
        else:
            log.info("Discord message sent to #%s as %s", channel, agent)
    except subprocess.TimeoutExpired:
        log.warning("discord-reply.sh timed out")
    except OSError as exc:
        log.warning("discord-reply.sh failed: %s", exc)


# ---------------------------------------------------------------------------
# Plan persistence
# ---------------------------------------------------------------------------

def _plans_dir() -> Path:
    PLANS_DIR.mkdir(parents=True, exist_ok=True)
    return PLANS_DIR


def save_plan(plan: SEOPlan) -> Path:
    path = _plans_dir() / f"{plan.id}.json"
    path.write_text(json.dumps(plan.to_dict(), indent=2))
    log.info("Plan saved to %s", path)
    return path


def load_plan(plan_id: str) -> SEOPlan:
    path = _plans_dir() / f"{plan_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Plan not found: {plan_id}")
    raw = json.loads(path.read_text())
    brief = ClientBrief(**{k: raw["brief"][k] for k in ClientBrief.__dataclass_fields__})
    objectives = [
        Objective(
            id=o["id"],
            title=o["title"],
            description=o["description"],
            role=o["role"],
            effort=o["effort"],
            token_budget=o["token_budget"],
            task_id=o.get("task_id"),
            status=TaskStatus(o["status"]),
        )
        for o in raw["objectives"]
    ]
    return SEOPlan(
        id=raw["id"],
        project=raw["project"],
        brief=brief,
        objectives=objectives,
        status=TaskStatus(raw["status"]),
        created_at=raw["created_at"],
    )


def latest_plan_id(project: str) -> str:
    plans = sorted(_plans_dir().glob(f"*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in plans:
        data = json.loads(p.read_text())
        if data.get("project") == project:
            return data["id"]
    raise FileNotFoundError(f"No saved plan found for project: {project}")


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

async def generate_brief(project: str) -> ClientBrief:
    """Build a ClientBrief from defaults + Mirror memory enrichment."""
    defaults = PROJECT_DEFAULTS.get(project)
    if defaults is None:
        log.warning("No defaults for project %s — using empty scaffold", project)
        defaults = {
            "name": project,
            "url": f"https://{project}.ca",
            "target_cities": [],
            "key_pages": [],
            "kpis": [],
            "primary_keywords": [],
            "business_model": "Unknown",
        }

    mirror = MirrorClient()
    memories = await mirror.search_memory(f"{project} SEO context brief", agent="kasra", limit=3)
    enrichment_notes: list[str] = [m.get("text", "") for m in memories if m.get("text")]
    if enrichment_notes:
        log.info("Enriched brief with %d memory snippets from Mirror", len(enrichment_notes))

    brief = ClientBrief(
        project=defaults["name"],
        url=defaults["url"],
        target_cities=defaults["target_cities"],
        key_pages=defaults["key_pages"],
        kpis=defaults["kpis"],
        primary_keywords=defaults["primary_keywords"],
        business_model=defaults["business_model"],
    )

    path = _plans_dir() / f"brief_{project}.json"
    path.write_text(json.dumps(brief.to_dict(), indent=2))
    log.info("Brief written to %s", path)
    return brief


def build_seo_objectives(brief: ClientBrief) -> list[Objective]:
    """Decompose a brief into concrete SEO objectives with role + effort."""
    city_count = len(brief.target_cities)

    raw: list[tuple[str, str, Role, Effort]] = [
        (
            "Technical SEO audit",
            f"Crawl {brief.url} — identify broken links, missing meta, slow pages, "
            "duplicate content, canonical issues.",
            "analyst",
            "high",
        ),
        (
            f"City page audit ({city_count} cities)",
            f"Review /en/[city] pages for {', '.join(brief.target_cities[:5])} and more. "
            "Check H1, meta title, meta description, and keyword density per primary keyword.",
            "analyst",
            "medium",
        ),
        (
            "Meta tag optimisation",
            "Rewrite title tags and meta descriptions for all city and service pages. "
            f"Target primary keywords: {', '.join(brief.primary_keywords)}.",
            "optimizer",
            "medium",
        ),
        (
            "Internal link map",
            f"Build a complete internal link graph for {brief.url}. "
            "Identify orphan pages, link equity gaps, and opportunities for city-to-service linking.",
            "analyst",
            "medium",
        ),
        (
            "Content gap analysis",
            "Compare existing topic coverage against top 10 SERP competitors. "
            "List missing topics per city and service combination.",
            "writer",
            "high",
        ),
        (
            "CDCP page optimisation",
            "Optimise /en/cdcp for 'Canada Dental Care Plan' queries. "
            "Update content, schema markup, and internal links.",
            "optimizer",
            "low",
        ),
        (
            "Schema markup audit",
            "Check LocalBusiness and Dentist schema on city pages. "
            "Generate schema snippets where missing.",
            "optimizer",
            "low",
        ),
        (
            "Backlink profile review",
            "Export current backlink profile. Identify toxic links, "
            "lost links, and high-value acquisition targets.",
            "analyst",
            "medium",
        ),
        (
            "KPI baseline report",
            f"Pull baseline metrics for: {', '.join(brief.kpis)}. "
            "Set targets per city. Deliver as CSV + summary.",
            "reporter",
            "low",
        ),
        (
            "Quick-win keyword targeting",
            "Find keywords ranking positions 11–20. "
            "Produce a list of 20 pages with highest traffic-on-nudge potential.",
            "optimizer",
            "medium",
        ),
    ]

    objectives: list[Objective] = []
    for title, description, role, effort in raw:
        obj = Objective(
            id=str(uuid.uuid4())[:8],
            title=title,
            description=description,
            role=role,
            effort=effort,
            token_budget=EFFORT_TOKENS[effort],
        )
        objectives.append(obj)

    return objectives


async def create_plan(project: str) -> SEOPlan:
    """Generate brief + build objectives + persist plan."""
    brief = await generate_brief(project)
    objectives = build_seo_objectives(brief)
    plan = SEOPlan(
        id=str(uuid.uuid4()),
        project=project,
        brief=brief,
        objectives=objectives,
    )
    save_plan(plan)
    log.info(
        "Plan %s created — %d objectives, ~%d tokens total",
        plan.id,
        len(objectives),
        plan.total_token_budget(),
    )
    return plan


async def submit_plan(project: str) -> SEOPlan:
    """Load the latest plan, post to Discord, create Mirror tasks, set blocked."""
    plan_id = latest_plan_id(project)
    plan = load_plan(plan_id)

    mirror = MirrorClient()
    labels = ["squad", "seo", project]

    # Create Mirror tasks for each objective
    for obj in plan.objectives:
        if obj.task_id:
            log.info("Objective %s already has task_id %s — skipping", obj.id, obj.task_id)
            continue
        task_id = await mirror.create_task(
            title=f"[{project}] {obj.title}",
            description=obj.description,
            labels=labels,
            status="blocked",
            metadata={
                "plan_id": plan.id,
                "objective_id": obj.id,
                "role": obj.role,
                "effort": obj.effort,
                "token_budget": obj.token_budget,
            },
        )
        if task_id:
            obj.task_id = task_id

    save_plan(plan)

    # Build Discord message
    obj_lines = "\n".join(
        f"  [{o.effort.upper()}] {o.title} → {o.role} (~{o.token_budget:,} tokens)"
        for o in plan.objectives
    )
    discord_msg = (
        f"**SEO Plan submitted for approval** — project: `{project}`\n"
        f"Plan ID: `{plan.id}`\n"
        f"Objectives ({len(plan.objectives)}):\n{obj_lines}\n"
        f"Total token budget: ~{plan.total_token_budget():,}\n\n"
        f"To approve: `python squad_activate.py approve {plan.id}`\n"
        f"To reject:  `python squad_activate.py reject {plan.id} \"<reason>\"`"
    )
    post_discord("kasra", "agent-collab", discord_msg)

    log.info("Plan %s submitted — waiting for human approval", plan.id)
    return plan


async def approve_plan(plan_id: str) -> None:
    """Unblock all tasks in the plan."""
    plan = load_plan(plan_id)
    mirror = MirrorClient()

    approved_count = 0
    for obj in plan.objectives:
        obj.status = TaskStatus.approved
        if obj.task_id:
            ok = await mirror.update_task(obj.task_id, {"status": "approved"})
            if ok:
                approved_count += 1

    plan.status = TaskStatus.approved
    save_plan(plan)

    msg = (
        f"Plan `{plan_id}` APPROVED. "
        f"{approved_count}/{len(plan.objectives)} tasks unblocked. "
        f"Project: `{plan.project}`"
    )
    post_discord("kasra", "agent-collab", msg)
    log.info("Plan %s approved — %d tasks unblocked", plan_id, approved_count)


async def reject_plan(plan_id: str, reason: str) -> None:
    """Cancel all tasks in the plan with a reason."""
    plan = load_plan(plan_id)
    mirror = MirrorClient()

    cancelled_count = 0
    for obj in plan.objectives:
        obj.status = TaskStatus.rejected
        if obj.task_id:
            ok = await mirror.update_task(
                obj.task_id,
                {"status": "rejected", "rejection_reason": reason},
            )
            if ok:
                cancelled_count += 1

    plan.status = TaskStatus.rejected
    save_plan(plan)

    msg = (
        f"Plan `{plan_id}` REJECTED.\n"
        f"Reason: {reason}\n"
        f"{cancelled_count}/{len(plan.objectives)} tasks cancelled. "
        f"Project: `{plan.project}`"
    )
    post_discord("kasra", "agent-collab", msg)
    log.info("Plan %s rejected — reason: %s", plan_id, reason)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_brief(brief: ClientBrief) -> None:
    print(json.dumps(brief.to_dict(), indent=2))


def _print_plan(plan: SEOPlan) -> None:
    d = plan.to_dict()
    # Omit the nested brief for readability in plan view
    d.pop("brief", None)
    print(json.dumps(d, indent=2))
    print(f"\nBrief URL: {plan.brief.url}")
    print(f"Cities ({len(plan.brief.target_cities)}): {', '.join(plan.brief.target_cities)}")
    print(f"Total token budget: {plan.total_token_budget():,}")


async def _main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1

    cmd = argv[1]

    if cmd == "brief":
        if len(argv) < 3:
            print("Usage: squad_activate.py brief <project>")
            return 1
        brief = await generate_brief(argv[2])
        _print_brief(brief)

    elif cmd == "plan":
        if len(argv) < 3:
            print("Usage: squad_activate.py plan <project>")
            return 1
        plan = await create_plan(argv[2])
        _print_plan(plan)

    elif cmd == "submit":
        if len(argv) < 3:
            print("Usage: squad_activate.py submit <project>")
            return 1
        plan = await submit_plan(argv[2])
        print(f"Submitted plan {plan.id} — {len(plan.objectives)} objectives pending approval")

    elif cmd == "approve":
        if len(argv) < 3:
            print("Usage: squad_activate.py approve <plan_id>")
            return 1
        await approve_plan(argv[2])
        print(f"Plan {argv[2]} approved.")

    elif cmd == "reject":
        if len(argv) < 4:
            print('Usage: squad_activate.py reject <plan_id> "reason"')
            return 1
        await reject_plan(argv[2], argv[3])
        print(f"Plan {argv[2]} rejected.")

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv)))
