#!/usr/bin/env python3
"""
Squad Scheduler — Set-and-forget squad automation with budget control.

Hadi says "SEO for DNU, 5% weekly budget" once.
System runs it weekly on Gemma 4 (diesel/free) forever.
Kasra only gets pulled in if diesel can't handle the task.

Usage:
    python squad_scheduler.py register seo dentalnearyou --budget 5 --fuel diesel --cadence weekly
    python squad_scheduler.py status dentalnearyou
    python squad_scheduler.py run dentalnearyou seo
    python squad_scheduler.py list
    python squad_scheduler.py crontab dentalnearyou seo
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeAlias

import httpx

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MIRROR_BASE = "http://localhost:8844"
MIRROR_AUTH = "Bearer sk-mumega-internal-001"
ORGANISMS_DIR = Path("/home/mumega/.mumega/organisms")
BUDGETS_DIR = Path("/home/mumega/SOS/sovereign/.budgets")
ENGAGEMENTS_FILE = Path("/home/mumega/SOS/sovereign/.budgets/engagements.json")
THIS_FILE = Path(__file__).resolve()

log = logging.getLogger("squad_scheduler")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

Cadence: TypeAlias = str  # "weekly" | "daily" | "monthly"
FuelGrade: TypeAlias = str  # "diesel" | "regular" | "premium" | "aviation"

# Diesel = free models: effectively unlimited compute, but cap per-task to keep things sane
DIESEL_TOKEN_CAP_PER_TASK = 40_000

# Cost per 1M tokens (USD) per fuel grade — diesel is $0 but metered logically
FUEL_COST_PER_1M: dict[FuelGrade, float] = {
    "diesel": 0.0,
    "regular": 0.35,   # avg DeepSeek / Grok
    "premium": 5.0,
    "aviation": 15.0,
}

# Primary models per grade (first = preferred)
FUEL_MODELS: dict[FuelGrade, list[str]] = {
    "diesel": ["gemma-4-31b-it", "gemma-4-26b-a4b-it", "gemini-2.0-flash-exp", "gpt-4o-mini"],
    "regular": ["grok-4-1-fast-reasoning", "deepseek-chat"],
    "premium": ["gpt-4o", "gemini-2.5-pro"],
    "aviation": ["claude-opus-4-6", "claude-sonnet-4-6"],
}

# Weekly batch structure: week_number (1-4) → role focus
WEEKLY_ROLES: dict[int, str] = {
    1: "analyst",
    2: "optimizer",
    3: "writer",
    4: "reporter",
}

# Agent name that receives diesel tasks (OpenClaw worker using Gemma 4)
DIESEL_AGENT = "worker"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class Engagement:
    """A registered squad-on-project engagement with budget."""
    id: str
    squad: str
    project: str
    budget_pct: float            # % of monthly budget to spend per cadence period
    fuel_grade: FuelGrade
    cadence: Cadence
    auto_approve: bool
    approved: bool               # First run requires human approval; after that auto
    status: str                  # "active" | "paused" | "pending_approval"
    created_at: str
    last_run_at: str | None = None
    next_run_week: int = 1       # 1–4, cycles through WEEKLY_ROLES

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Engagement":
        return Engagement(**d)


@dataclass
class TaskExecution:
    """Record of a single task dispatched by the scheduler."""
    task_id: str
    engagement_id: str
    project: str
    squad: str
    role: str
    title: str
    model: str
    fuel_grade: FuelGrade
    tokens_budgeted: int
    tokens_used: int = 0
    cost_usd: float = 0.0
    score: float = 0.0
    status: str = "pending"      # pending | done | failed
    dispatched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BudgetLedger:
    """Tracks spend for one project+squad pair within the current cycle."""
    project: str
    squad: str
    cycle_start: str             # ISO timestamp of cycle start
    budget_cents: float          # Allocated budget in USD cents
    spent_cents: float = 0.0
    tasks_dispatched: int = 0
    tasks_done: int = 0
    executions: list[dict] = field(default_factory=list)

    @property
    def remaining_cents(self) -> float:
        return self.budget_cents - self.spent_cents

    @property
    def is_over_budget(self) -> bool:
        # Diesel is free, so only block on actual paid grades
        if self.budget_cents == 0:
            return False
        return self.spent_cents >= self.budget_cents

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "BudgetLedger":
        return BudgetLedger(
            project=d["project"],
            squad=d["squad"],
            cycle_start=d["cycle_start"],
            budget_cents=d["budget_cents"],
            spent_cents=d.get("spent_cents", 0.0),
            tasks_dispatched=d.get("tasks_dispatched", 0),
            tasks_done=d.get("tasks_done", 0),
            executions=d.get("executions", []),
        )


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _ensure_dirs() -> None:
    BUDGETS_DIR.mkdir(parents=True, exist_ok=True)


def _load_engagements() -> dict[str, Engagement]:
    _ensure_dirs()
    if not ENGAGEMENTS_FILE.exists():
        return {}
    raw: dict = json.loads(ENGAGEMENTS_FILE.read_text())
    return {k: Engagement.from_dict(v) for k, v in raw.items()}


def _save_engagements(engagements: dict[str, Engagement]) -> None:
    _ensure_dirs()
    ENGAGEMENTS_FILE.write_text(
        json.dumps({k: v.to_dict() for k, v in engagements.items()}, indent=2)
    )


def _ledger_path(project: str, squad: str) -> Path:
    return BUDGETS_DIR / f"{project}_{squad}.json"


def _load_ledger(project: str, squad: str) -> BudgetLedger | None:
    path = _ledger_path(project, squad)
    if not path.exists():
        return None
    return BudgetLedger.from_dict(json.loads(path.read_text()))


def _save_ledger(ledger: BudgetLedger) -> None:
    _ensure_dirs()
    path = _ledger_path(ledger.project, ledger.squad)
    path.write_text(json.dumps(ledger.to_dict(), indent=2))


# ---------------------------------------------------------------------------
# Budget calculation
# ---------------------------------------------------------------------------

def _read_organism_budget_cents(project: str) -> float:
    """Read budget_cents_monthly from the project's organism YAML."""
    # Try common filename variants
    candidates = [
        ORGANISMS_DIR / f"{project}.yaml",
        ORGANISMS_DIR / f"{project.replace('_', '-')}.yaml",
        ORGANISMS_DIR / f"{project.replace('-', '_')}.yaml",
    ]
    for path in candidates:
        if path.exists():
            try:
                import re
                text = path.read_text()
                match = re.search(r"budget_cents_monthly:\s*(\d+(?:\.\d+)?)", text)
                if match:
                    return float(match.group(1))
            except OSError as exc:
                log.warning("Could not read organism YAML %s: %s", path, exc)
    log.warning("No organism YAML found for project '%s' — defaulting to $10 budget", project)
    return 1000.0  # $10.00 default


def _calculate_weekly_budget(project: str, budget_pct: float, fuel_grade: FuelGrade) -> float:
    """
    Return the weekly budget allocation in USD cents.
    Diesel = free, so this is a logical cap, not a payment guard.
    """
    monthly_cents = _read_organism_budget_cents(project)
    # weekly = monthly * (pct/100) / 4
    weekly_cents = (monthly_cents * (budget_pct / 100.0)) / 4.0
    cost_per_1m = FUEL_COST_PER_1M[fuel_grade]

    if cost_per_1m == 0.0:
        # Diesel is free — return a large logical cap (1M tokens worth)
        return 0.0  # 0 means "free/uncapped" in ledger logic

    return weekly_cents


def _tokens_from_budget(budget_cents: float, fuel_grade: FuelGrade) -> int:
    """Convert a cent budget into a token budget for paid grades."""
    cost_per_1m = FUEL_COST_PER_1M[fuel_grade]
    if cost_per_1m == 0.0:
        # Diesel: cap per-task at a sensible limit, not by dollars
        return DIESEL_TOKEN_CAP_PER_TASK
    # budget_cents / 100 = budget_dollars; budget_dollars / cost_per_1m = millions of tokens
    tokens = int((budget_cents / 100.0) / cost_per_1m * 1_000_000)
    return max(tokens, 5_000)  # floor at 5k tokens


# ---------------------------------------------------------------------------
# Mirror API client
# ---------------------------------------------------------------------------

class MirrorClient:
    _headers = {
        "Authorization": MIRROR_AUTH,
        "Content-Type": "application/json",
    }

    async def create_task(
        self,
        title: str,
        description: str,
        agent: str,
        labels: list[str],
        metadata: dict | None = None,
    ) -> str | None:
        payload = {
            "title": title,
            "description": description,
            "agent": agent,
            "labels": labels,
            "status": "approved",  # ready for calcifer dispatch
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
                    # Mirror wraps the task under a "task" key
                    task_obj = data.get("task") or data
                    task_id: str = task_obj.get("id") or data.get("task_id", "")
                    log.info("Mirror task created %s: %s", task_id, title)
                    return task_id
                log.warning("Mirror task creation failed %s: %s", resp.status_code, resp.text[:200])
        except httpx.ConnectError:
            log.warning("Mirror API unreachable — task not created: %s", title)
        return None

    async def list_tasks(self, project: str, labels: list[str] | None = None) -> list[dict]:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                params: dict = {"project": project}
                if labels:
                    params["labels"] = ",".join(labels)
                resp = await client.get(
                    f"{MIRROR_BASE}/tasks",
                    headers=self._headers,
                    params=params,
                )
                if resp.status_code == 200:
                    return resp.json().get("tasks", [])
        except httpx.ConnectError:
            log.warning("Mirror API unreachable — cannot list tasks")
        return []


# ---------------------------------------------------------------------------
# Redis dispatch
# ---------------------------------------------------------------------------

def _get_redis():
    try:
        import redis as redis_lib
    except ImportError:
        log.warning("redis-py not installed — dispatch via Mirror only")
        return None

    # Load password from .env.secrets
    password = os.environ.get("REDIS_PASSWORD", "")
    if not password:
        env_path = Path("/home/mumega/.env.secrets")
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("REDIS_PASSWORD="):
                    password = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    if password and "localhost" in redis_url:
        redis_url = f"redis://:{password}@localhost:6379/0"

    try:
        r = redis_lib.from_url(redis_url, decode_responses=True, socket_timeout=3)
        r.ping()
        return r
    except Exception as exc:
        log.warning("Redis unavailable: %s", exc)
        return None


def _dispatch_via_redis(task_id: str, agent: str, payload: dict) -> bool:
    """Push task to agent stream so calcifer/worker picks it up."""
    r = _get_redis()
    if not r:
        return False
    try:
        stream_key = f"sos:stream:{agent}"
        message = {
            "type": "squad_task",
            "task_id": task_id,
            "data": json.dumps(payload),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        r.xadd(stream_key, message, maxlen=500)
        # Wake signal
        r.publish(f"agent:{agent}:wake", json.dumps({"task_id": task_id, "source": "squad_scheduler"}))
        log.info("Dispatched task %s to Redis stream %s", task_id, stream_key)
        return True
    except Exception as exc:
        log.warning("Redis dispatch failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Squad role resolution (from registry.py)
# ---------------------------------------------------------------------------

def _get_squad_roles(squad: str) -> dict:
    """Import squad definitions from registry without circular deps."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from registry import SQUADS  # type: ignore[import]
        return SQUADS.get(squad, {}).get("roles", {})
    except ImportError as exc:
        log.warning("Could not import registry.py: %s", exc)
        return {}


def _roles_for_week(squad: str, week: int) -> list[tuple[str, dict]]:
    """Return the roles to run this week based on the 4-week rotation."""
    target_role = WEEKLY_ROLES.get(week, "analyst")
    all_roles = _get_squad_roles(squad)
    if not all_roles:
        return []
    # Primary: exact role match. Fallback: all roles if unknown squad structure
    matched = [(name, cfg) for name, cfg in all_roles.items() if name == target_role]
    return matched if matched else list(all_roles.items())[:1]


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

def register_engagement(
    squad: str,
    project: str,
    budget_pct: float,
    fuel_grade: FuelGrade,
    cadence: Cadence,
    auto_approve: bool,
) -> Engagement:
    """Register a new squad engagement. First run requires human approval."""
    engagements = _load_engagements()

    # Check for existing active engagement for same squad+project
    for existing in engagements.values():
        if existing.squad == squad and existing.project == project and existing.status != "paused":
            log.warning(
                "Engagement already exists: %s/%s (id=%s). "
                "Use 'status' to view or pause it first.",
                squad, project, existing.id,
            )
            return existing

    eng = Engagement(
        id=str(uuid.uuid4())[:8],
        squad=squad,
        project=project,
        budget_pct=budget_pct,
        fuel_grade=fuel_grade,
        cadence=cadence,
        auto_approve=auto_approve,
        approved=False,      # requires first-run approval
        status="pending_approval",
        created_at=datetime.now(timezone.utc).isoformat(),
        next_run_week=1,
    )
    engagements[eng.id] = eng
    _save_engagements(engagements)

    weekly_cents = _calculate_weekly_budget(project, budget_pct, fuel_grade)
    token_budget = _tokens_from_budget(weekly_cents, fuel_grade)
    primary_model = FUEL_MODELS[fuel_grade][0]

    print(f"\nEngagement registered: {squad} / {project}")
    print(f"  ID:            {eng.id}")
    print(f"  Fuel grade:    {fuel_grade} → {primary_model}")
    print(f"  Budget:        {budget_pct}% monthly = {weekly_cents/100:.2f} USD/week")
    print(f"  Token cap:     {token_budget:,} tokens/task")
    print(f"  Cadence:       {cadence}")
    print(f"  Status:        pending_approval (run 'approve {eng.id}' to activate)")
    print(f"\nCrontab line:")
    print(f"  {_crontab_line(project, squad)}")
    print(f"\nTo approve:")
    print(f"  python3 {THIS_FILE} approve {eng.id}")

    return eng


def approve_engagement(engagement_id: str) -> None:
    """Approve an engagement for autonomous execution."""
    engagements = _load_engagements()
    if engagement_id not in engagements:
        print(f"Engagement not found: {engagement_id}")
        sys.exit(1)
    eng = engagements[engagement_id]
    eng.approved = True
    eng.status = "active"
    _save_engagements(engagements)
    print(f"Engagement {engagement_id} approved and active.")
    print(f"Run: python3 {THIS_FILE} run {eng.project} {eng.squad}")


async def run_batch(project: str, squad: str) -> None:
    """Execute the next weekly batch for the engagement."""
    engagements = _load_engagements()

    # Find active engagement
    eng: Engagement | None = None
    for e in engagements.values():
        if e.project == project and e.squad == squad and e.status == "active":
            eng = e
            break

    if eng is None:
        # Check if pending approval
        for e in engagements.values():
            if e.project == project and e.squad == squad:
                if e.status == "pending_approval":
                    print(f"Engagement is pending approval. Run: python3 {THIS_FILE} approve {e.id}")
                    return
        print(f"No active engagement for {squad}/{project}. Register first.")
        return

    if not eng.approved:
        print(f"Engagement {eng.id} not yet approved. Run: python3 {THIS_FILE} approve {eng.id}")
        return

    # Load or create ledger
    ledger = _load_ledger(project, squad)
    if ledger is None:
        weekly_cents = _calculate_weekly_budget(project, eng.budget_pct, eng.fuel_grade)
        ledger = BudgetLedger(
            project=project,
            squad=squad,
            cycle_start=datetime.now(timezone.utc).isoformat(),
            budget_cents=weekly_cents,
        )

    if ledger.is_over_budget:
        print(f"Budget exhausted for {squad}/{project}. Spent: ${ledger.spent_cents/100:.4f} / ${ledger.budget_cents/100:.4f}")
        print("Pausing until next cycle.")
        eng.status = "paused"
        _save_engagements(engagements)
        return

    # Determine which roles to run this week
    week = eng.next_run_week
    roles_to_run = _roles_for_week(squad, week)

    if not roles_to_run:
        print(f"No roles found for squad '{squad}' week {week}. Check registry.py.")
        return

    print(f"\nRunning week {week} batch ({WEEKLY_ROLES.get(week, 'analyst')} focus)")
    print(f"Project: {project} | Squad: {squad} | Fuel: {eng.fuel_grade}")
    print(f"Budget: {ledger.remaining_cents/100:.4f} USD remaining ({ledger.budget_cents/100:.4f} allocated)")

    mirror = MirrorClient()
    primary_model = FUEL_MODELS[eng.fuel_grade][0]
    agent = DIESEL_AGENT if eng.fuel_grade == "diesel" else "athena"

    dispatched: list[TaskExecution] = []

    for role_name, role_cfg in roles_to_run:
        token_budget = _tokens_from_budget(ledger.remaining_cents, eng.fuel_grade)
        task_title = f"[SCHED:{squad.upper()}:W{week}] {role_name}: {str(role_cfg.get('does', ''))[:60]}"
        task_desc = (
            f"Squad: {squad} | Project: {project}\n"
            f"Role: {role_name} | Week: {week}/4 ({WEEKLY_ROLES.get(week, 'analyst')} focus)\n"
            f"Fuel: {eng.fuel_grade} → {primary_model}\n"
            f"Token budget: {token_budget:,}\n\n"
            f"Does: {role_cfg.get('does', '')}\n"
            f"Skills: {', '.join(role_cfg.get('skills', []))}\n"
            f"Schedule: {role_cfg.get('schedule', '')}\n\n"
            f"Engagement: {eng.id} | Cadence: {eng.cadence}"
        )

        task_id = await mirror.create_task(
            title=task_title,
            description=task_desc,
            agent=agent,
            labels=["squad", squad, project, eng.fuel_grade, f"week{week}", role_name],
            metadata={
                "engagement_id": eng.id,
                "squad": squad,
                "project": project,
                "role": role_name,
                "week": week,
                "fuel_grade": eng.fuel_grade,
                "model": primary_model,
                "token_budget": token_budget,
                "scheduled": True,
            },
        )

        if task_id:
            execution = TaskExecution(
                task_id=task_id,
                engagement_id=eng.id,
                project=project,
                squad=squad,
                role=role_name,
                title=task_title,
                model=primary_model,
                fuel_grade=eng.fuel_grade,
                tokens_budgeted=token_budget,
            )
            dispatched.append(execution)

            # Also push to Redis for immediate calcifer pickup
            _dispatch_via_redis(task_id, agent, {
                "engagement_id": eng.id,
                "squad": squad,
                "project": project,
                "role": role_name,
                "model": primary_model,
                "fuel_grade": eng.fuel_grade,
                "token_budget": token_budget,
                "title": task_title,
            })

            # Record in ledger
            ledger.tasks_dispatched += 1
            ledger.executions.append(execution.to_dict())

            # For paid grades, charge the logical budget reservation
            cost_per_1m = FUEL_COST_PER_1M[eng.fuel_grade]
            if cost_per_1m > 0:
                estimated_cost_cents = (token_budget / 1_000_000.0) * cost_per_1m * 100
                ledger.spent_cents += estimated_cost_cents

            print(f"  Dispatched: {task_title[:70]}")
            print(f"    task_id={task_id} model={primary_model} tokens={token_budget:,}")
        else:
            print(f"  Failed to create Mirror task for role: {role_name}")

    # Advance week counter (1 → 2 → 3 → 4 → 1)
    eng.next_run_week = (week % 4) + 1
    eng.last_run_at = datetime.now(timezone.utc).isoformat()
    _save_engagements(engagements)
    _save_ledger(ledger)

    print(f"\nBatch complete: {len(dispatched)} tasks dispatched")
    print(f"Next run week: {eng.next_run_week} ({WEEKLY_ROLES.get(eng.next_run_week, '?')} focus)")
    print(f"Ledger: {ledger.spent_cents/100:.4f} USD spent / {ledger.budget_cents/100:.4f} USD allocated")


def show_status(project: str) -> None:
    """Show active engagements and spend for a project."""
    engagements = _load_engagements()
    project_engagements = [e for e in engagements.values() if e.project == project]

    if not project_engagements:
        print(f"No engagements found for project: {project}")
        return

    print(f"\n{'='*60}")
    print(f"  SQUAD SCHEDULER — {project.upper()}")
    print(f"{'='*60}")

    for eng in project_engagements:
        ledger = _load_ledger(eng.project, eng.squad)
        print(f"\n  Squad: {eng.squad} (id={eng.id})")
        print(f"  Status:     {eng.status}")
        print(f"  Fuel:       {eng.fuel_grade} → {FUEL_MODELS[eng.fuel_grade][0]}")
        print(f"  Budget:     {eng.budget_pct}% monthly")
        print(f"  Cadence:    {eng.cadence}")
        print(f"  Next week:  {eng.next_run_week} ({WEEKLY_ROLES.get(eng.next_run_week, '?')})")
        print(f"  Last run:   {eng.last_run_at or 'never'}")
        if ledger:
            if ledger.budget_cents == 0:
                print(f"  Spend:      FREE (diesel) — {ledger.tasks_dispatched} tasks dispatched")
            else:
                pct = (ledger.spent_cents / ledger.budget_cents * 100) if ledger.budget_cents > 0 else 0
                print(
                    f"  Spend:      ${ledger.spent_cents/100:.4f} / ${ledger.budget_cents/100:.4f} "
                    f"({pct:.1f}%) — {ledger.tasks_dispatched} tasks"
                )
        else:
            print(f"  Spend:      No ledger yet (not run)")


def show_list() -> None:
    """Show all engagements across all projects."""
    engagements = _load_engagements()
    if not engagements:
        print("No engagements registered.")
        return

    print(f"\n{'='*70}")
    print(f"  ALL SQUAD ENGAGEMENTS")
    print(f"{'='*70}")
    print(f"  {'ID':<10} {'PROJECT':<20} {'SQUAD':<12} {'FUEL':<10} {'STATUS':<18} {'LAST RUN'}")
    print(f"  {'-'*65}")
    for eng in engagements.values():
        last = (eng.last_run_at or "never")[:16]
        print(f"  {eng.id:<10} {eng.project:<20} {eng.squad:<12} {eng.fuel_grade:<10} {eng.status:<18} {last}")


def _crontab_line(project: str, squad: str) -> str:
    """Return a crontab line for weekly Monday 9am execution."""
    return f"0 9 * * 1 python3 {THIS_FILE} run {project} {squad}"


def show_crontab(project: str, squad: str) -> None:
    """Print the crontab line for this engagement."""
    print("\nAdd to crontab (crontab -e):")
    print(f"  {_crontab_line(project, squad)}")
    print("\nOr add to calcifer's dispatch cycle by importing run_batch().")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="squad_scheduler.py",
        description="Squad Scheduler — automated budget-aware squad execution",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # register
    reg = sub.add_parser("register", help="Register a new squad engagement")
    reg.add_argument("squad", help="Squad name (e.g. seo, content, leadgen)")
    reg.add_argument("project", help="Project slug (e.g. dentalnearyou)")
    reg.add_argument("--budget", type=float, default=5.0, help="Budget %% of monthly (default: 5)")
    reg.add_argument("--fuel", default="diesel", choices=list(FUEL_MODELS.keys()), help="Fuel grade")
    reg.add_argument("--cadence", default="weekly", choices=["weekly", "daily", "monthly"])
    reg.add_argument("--auto-approve", action="store_true", help="Skip human approval after first run")

    # approve
    appr = sub.add_parser("approve", help="Approve a pending engagement")
    appr.add_argument("engagement_id", help="Engagement ID")

    # run
    run_p = sub.add_parser("run", help="Execute next batch for an engagement")
    run_p.add_argument("project")
    run_p.add_argument("squad")

    # status
    stat = sub.add_parser("status", help="Show engagement status for a project")
    stat.add_argument("project")

    # list
    sub.add_parser("list", help="List all engagements")

    # crontab
    cron = sub.add_parser("crontab", help="Print crontab line for an engagement")
    cron.add_argument("project")
    cron.add_argument("squad")

    return parser


def main(argv: list[str] | None = None) -> int:
    # Load .env.secrets before anything
    env_path = Path("/home/mumega/.env.secrets")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "register":
        register_engagement(
            squad=args.squad,
            project=args.project,
            budget_pct=args.budget,
            fuel_grade=args.fuel,
            cadence=args.cadence,
            auto_approve=args.auto_approve,
        )

    elif args.command == "approve":
        approve_engagement(args.engagement_id)

    elif args.command == "run":
        asyncio.run(run_batch(args.project, args.squad))

    elif args.command == "status":
        show_status(args.project)

    elif args.command == "list":
        show_list()

    elif args.command == "crontab":
        show_crontab(args.project, args.squad)

    return 0


if __name__ == "__main__":
    sys.exit(main())
