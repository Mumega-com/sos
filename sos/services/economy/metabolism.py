# Moved from scripts/metabolism.py — token budget management
#!/usr/bin/env python3
"""
Mumega Token Metabolism — The Digestive System

Central token pool from all providers. Per-project budget allocation.
Track consumption, cost, output value. Revenue increases the pool.

This is the animal's stomach:
  - Token sources = food (Gemini, GitHub, OpenAI, Anthropic, xAI, Gemma 4)
  - Projects = organs (each gets a % of the pie)
  - Revenue = energy gained (Stripe income feeds back)
  - Waste = tokens spent without coherence gain

Run:
  python3 metabolism.py status        # Show current state
  python3 metabolism.py digest        # Run metabolism cycle
  python3 metabolism.py allocate      # Recalculate budgets from revenue
"""

import json
import os
import sys
import logging
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [METABOLISM] %(message)s")
logger = logging.getLogger("metabolism")

DB_PATH = Path.home() / ".mumega" / "metabolism.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# ============================================
# Token Sources — The Food Supply
# ============================================

TOKEN_SOURCES = {
    # === FLAT-RATE SUBSCRIPTIONS (unlimited compute, fixed monthly cost) ===
    "claude_code_max": {
        "provider": "anthropic",
        "models": ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5"],
        "daily_limit": None,  # unlimited within rate limits
        "cost_per_1m": 0.0,   # flat rate, already paid
        "monthly_cost": 120.0,
        "type": "subscription",
        "rate_limit_rpm": 60,  # requests per minute (estimated)
    },
    "codex_cli": {
        "provider": "openai",
        "models": ["gpt-5.4", "gpt-4o", "codex"],
        "daily_limit": None,
        "cost_per_1m": 0.0,
        "monthly_cost": 0.0,  # included with GitHub/Copilot
        "type": "subscription",
        "rate_limit_rpm": 30,
    },
    # === FREE TIER (daily limits, zero cost) ===
    "gemini_free": {
        "provider": "google",
        "models": ["gemini-2.0-flash-exp"],
        "daily_limit": 1500,
        "cost_per_1m": 0.0,
        "type": "free",
    },
    "gemma4_free": {
        "provider": "google",
        "models": ["gemma-4-31b-it", "gemma-4-26b-a4b-it"],
        "daily_limit": 1500,
        "cost_per_1m": 0.0,
        "type": "free",
    },
    "github_models": {
        "provider": "github",
        "models": ["gpt-4o-mini"],
        "daily_limit": 1000,
        "cost_per_1m": 0.0,
        "type": "free",
    },
    # === METERED (pay per token, use as overflow) ===
    "gemini_paid": {
        "provider": "google",
        "models": ["gemini-3-flash-preview", "gemini-2.5-pro"],
        "daily_limit": None,
        "cost_per_1m": 0.50,
        "type": "metered",
    },
    "xai_grok": {
        "provider": "xai",
        "models": ["grok-4-1-fast-reasoning"],
        "daily_limit": None,
        "cost_per_1m": 0.20,
        "type": "metered",
    },
    "deepseek": {
        "provider": "deepseek",
        "models": ["deepseek-chat", "deepseek-v3.2"],
        "daily_limit": None,
        "cost_per_1m": 0.28,
        "type": "metered",
    },
}

# ============================================
# Project Definitions — The Organs
# ============================================

PROJECTS = {
    "mumega": {
        "name": "Mumega Platform",
        "budget_pct": 30,  # % of total pool
        "agents": ["kasra", "athena", "river"],
        "revenue_source": "stripe_mumega",
        "priority": 1,  # highest priority
    },
    "dentalnearyou": {
        "name": "DentalNearYou",
        "budget_pct": 20,
        "agents": ["dandan"],
        "revenue_source": "stripe_dnu",
        "priority": 2,
    },
    "torivers": {
        "name": "ToRivers Marketplace",
        "budget_pct": 15,
        "agents": ["torivers_workers"],
        "revenue_source": "stripe_torivers",
        "priority": 2,
    },
    "therealmofpatterns": {
        "name": "The Realm of Patterns",
        "budget_pct": 10,
        "agents": ["sol"],
        "revenue_source": "stripe_trop",
        "priority": 3,
    },
    "gaf": {
        "name": "GrantAndFunding",
        "budget_pct": 10,
        "agents": ["gaf_agent"],
        "revenue_source": "stripe_gaf",
        "priority": 3,
    },
    "prefrontal": {
        "name": "Prefrontal Club",
        "budget_pct": 5,
        "agents": ["prefrontal_agent"],
        "revenue_source": "stripe_prefrontal",
        "priority": 4,
    },
    "reserve": {
        "name": "System Reserve",
        "budget_pct": 10,
        "agents": [],
        "revenue_source": None,
        "priority": 0,  # always maintained
    },
}


# ============================================
# Database
# ============================================

def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_consumption (
            date TEXT NOT NULL,
            project TEXT NOT NULL,
            source TEXT NOT NULL,
            model TEXT,
            requests INTEGER DEFAULT 0,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0.0,
            PRIMARY KEY (date, project, source)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_budget (
            date TEXT NOT NULL,
            project TEXT NOT NULL,
            allocated_usd REAL DEFAULT 0.0,
            spent_usd REAL DEFAULT 0.0,
            revenue_usd REAL DEFAULT 0.0,
            efficiency REAL DEFAULT 0.0,
            PRIMARY KEY (date, project)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS metabolic_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        )
    """)
    conn.commit()
    return conn


def get_state(conn, key: str, default: str = "0") -> str:
    row = conn.execute("SELECT value FROM metabolic_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_state(conn, key: str, value: str):
    conn.execute("""
        INSERT OR REPLACE INTO metabolic_state (key, value, updated_at)
        VALUES (?, ?, ?)
    """, (key, value, datetime.utcnow().isoformat()))
    conn.commit()


# ============================================
# Metabolism Core
# ============================================

def calculate_total_daily_budget() -> float:
    """
    Calculate total daily token budget in USD.

    Three tiers of compute:
    1. Subscriptions (Claude Code Max $120/mo, Codex CLI) = unlimited base capacity
    2. Free tier (Gemma 4, Gemini, GitHub) = daily-limited but zero cost
    3. Metered (Grok, DeepSeek, Gemini Pro) = overflow, bounded by guard

    The metabolic guard only limits metered (overflow) spending.
    Subscriptions and free tier are always available.
    """
    # Subscription cost is fixed — already paid
    subscription_monthly = sum(
        s.get("monthly_cost", 0) for s in TOKEN_SOURCES.values()
        if s["type"] == "subscription"
    )
    subscription_daily = subscription_monthly / 30

    # Metered overflow guard
    base_metered_guard = 2.00  # $2/day on metered APIs (overflow only)

    # Revenue multiplier — more revenue = more metered budget
    conn = init_db()
    monthly_revenue = float(get_state(conn, "monthly_revenue", "0"))
    conn.close()

    # Revenue-based boost: 20% of revenue goes to metered overflow
    revenue_daily = (monthly_revenue * 0.20) / 30
    metered_budget = base_metered_guard + revenue_daily

    # Total = subscriptions (fixed) + metered (variable)
    return subscription_daily + metered_budget


def allocate_budgets() -> Dict[str, float]:
    """Allocate daily budget across projects based on their percentage."""
    total = calculate_total_daily_budget()
    allocations = {}
    for project_id, project in PROJECTS.items():
        allocations[project_id] = round(total * (project["budget_pct"] / 100), 4)
    return allocations


def record_consumption(project: str, source: str, model: str,
                       input_tokens: int, output_tokens: int, cost_usd: float):
    """Record token consumption for a project."""
    conn = init_db()
    today = datetime.utcnow().strftime("%Y-%m-%d")
    conn.execute("""
        INSERT INTO daily_consumption (date, project, source, model, requests, input_tokens, output_tokens, cost_usd)
        VALUES (?, ?, ?, ?, 1, ?, ?, ?)
        ON CONFLICT(date, project, source) DO UPDATE SET
            requests = requests + 1,
            input_tokens = input_tokens + ?,
            output_tokens = output_tokens + ?,
            cost_usd = cost_usd + ?
    """, (today, project, source, model, input_tokens, output_tokens, cost_usd,
          input_tokens, output_tokens, cost_usd))
    conn.commit()
    conn.close()


def update_revenue(project: str, amount_usd: float):
    """Record revenue from Stripe for a project. Called by webhook handler."""
    conn = init_db()
    today = datetime.utcnow().strftime("%Y-%m-%d")

    # Update daily budget record
    conn.execute("""
        INSERT INTO daily_budget (date, project, revenue_usd)
        VALUES (?, ?, ?)
        ON CONFLICT(date, project) DO UPDATE SET
            revenue_usd = revenue_usd + ?
    """, (today, project, amount_usd, amount_usd))

    # Update rolling monthly revenue
    current = float(get_state(conn, "monthly_revenue", "0"))
    set_state(conn, "monthly_revenue", str(current + amount_usd))

    # Recalculate metabolic guard
    new_budget = calculate_total_daily_budget()
    set_state(conn, "daily_budget_usd", str(new_budget))

    conn.commit()
    conn.close()
    logger.info(f"Revenue recorded: ${amount_usd:.2f} for {project} — new daily budget: ${new_budget:.2f}")


def digest_cycle():
    """
    Run the metabolism digest cycle.
    Called periodically (e.g., every 4 hours) to:
    1. Calculate current budget allocations
    2. Check consumption vs budget
    3. Flag projects over budget
    4. Adjust routing preferences (prefer free models if budget tight)
    """
    conn = init_db()
    today = datetime.utcnow().strftime("%Y-%m-%d")
    allocations = allocate_budgets()
    total_budget = calculate_total_daily_budget()

    logger.info(f"=== METABOLISM DIGEST — {today} ===")
    logger.info(f"Total daily budget: ${total_budget:.2f}")
    logger.info(f"Monthly revenue: ${float(get_state(conn, 'monthly_revenue', '0')):.2f}")
    logger.info("")

    for project_id, allocation in allocations.items():
        # Get today's consumption
        row = conn.execute("""
            SELECT COALESCE(SUM(cost_usd), 0), COALESCE(SUM(requests), 0),
                   COALESCE(SUM(input_tokens + output_tokens), 0)
            FROM daily_consumption WHERE date = ? AND project = ?
        """, (today, project_id)).fetchone()

        spent = row[0]
        requests = row[1]
        tokens = row[2]

        # Calculate efficiency
        pct_used = (spent / allocation * 100) if allocation > 0 else 0
        status = "OK" if pct_used < 80 else "WARN" if pct_used < 100 else "OVER"

        # Update daily budget record
        conn.execute("""
            INSERT INTO daily_budget (date, project, allocated_usd, spent_usd, efficiency)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(date, project) DO UPDATE SET
                allocated_usd = ?, spent_usd = ?, efficiency = ?
        """, (today, project_id, allocation, spent, pct_used,
              allocation, spent, pct_used))

        project_name = PROJECTS[project_id]["name"]
        logger.info(f"  {project_name:<25} ${allocation:.2f} budget | ${spent:.4f} spent | {requests} reqs | {tokens} tokens | {status}")

        if status == "OVER":
            logger.warning(f"  ⚠ {project_name} OVER BUDGET — throttle to free models only")

    conn.commit()
    conn.close()
    logger.info("")
    logger.info("=== DIGEST COMPLETE ===")


def can_spend(project: str, estimated_cost: float = 0.0) -> dict:
    """Check if a project has budget remaining for an action.

    Returns:
        {
            "allowed": True/False,
            "budget": float,
            "spent": float,
            "remaining": float,
            "pct_used": float,
            "reason": str
        }

    Gap #7: Wire this into governance.before_action() to enforce budgets.
    """
    conn = init_db()
    today = datetime.utcnow().strftime("%Y-%m-%d")
    allocations = allocate_budgets()
    budget = allocations.get(project, 0.0)

    row = conn.execute("""
        SELECT COALESCE(SUM(cost_usd), 0) FROM daily_consumption
        WHERE date = ? AND project = ?
    """, (today, project)).fetchone()
    spent = row[0] if row else 0.0
    conn.close()

    remaining = budget - spent
    pct_used = (spent / budget * 100) if budget > 0 else 0

    # Subscription projects are always allowed (unlimited compute)
    project_def = PROJECTS.get(project, {})
    if project_def.get("priority", 99) <= 1:
        # High priority projects always have some budget
        if remaining <= 0:
            # Over metered budget but subscriptions still available
            return {
                "allowed": True,
                "budget": budget,
                "spent": spent,
                "remaining": 0.0,
                "pct_used": pct_used,
                "reason": "subscription_available",
                "warning": pct_used >= 80,
            }

    if pct_used >= 100 and estimated_cost > 0:
        return {
            "allowed": False,
            "budget": budget,
            "spent": spent,
            "remaining": remaining,
            "pct_used": pct_used,
            "reason": "budget_exceeded",
        }

    if remaining < estimated_cost and estimated_cost > 0:
        return {
            "allowed": False,
            "budget": budget,
            "spent": spent,
            "remaining": remaining,
            "pct_used": pct_used,
            "reason": "insufficient_budget",
        }

    return {
        "allowed": True,
        "budget": budget,
        "spent": spent,
        "remaining": remaining,
        "pct_used": pct_used,
        "reason": "ok",
        "warning": pct_used >= 80,
    }


def get_budget_status(project: str) -> dict:
    """Get current budget status for a project. Used by governance + dashboard."""
    return can_spend(project, 0.0)


def show_status():
    """Show current metabolic state."""
    conn = init_db()
    today = datetime.utcnow().strftime("%Y-%m-%d")
    total_budget = calculate_total_daily_budget()
    monthly_rev = float(get_state(conn, "monthly_revenue", "0"))
    allocations = allocate_budgets()

    sub_monthly = sum(s.get("monthly_cost", 0) for s in TOKEN_SOURCES.values() if s["type"] == "subscription")
    sub_daily = sub_monthly / 30
    free_daily = sum(s.get("daily_limit", 0) or 0 for s in TOKEN_SOURCES.values() if s["type"] == "free")
    metered_guard = 2.00 + (monthly_rev * 0.20 / 30)

    print(f"\n{'='*65}")
    print(f"  MUMEGA TOKEN METABOLISM — {today}")
    print(f"{'='*65}")
    print(f"  SUBSCRIPTIONS (flat rate, unlimited compute):")
    print(f"    Claude Code Max:   $120/mo  (Opus, Sonnet, Haiku)")
    print(f"    Codex CLI:         included (GPT-5.4, Codex)")
    print(f"    Daily value:       ${sub_daily:.2f}/day")
    print(f"  FREE TIER:           {free_daily:,} req/day (Gemma 4, Gemini, GitHub)")
    print(f"  METERED OVERFLOW:    ${metered_guard:.2f}/day (Grok, DeepSeek)")
    print(f"  Monthly Revenue:     ${monthly_rev:.2f}")
    print(f"  Revenue → overflow:  ${(monthly_rev * 0.20 / 30):.2f}/day boost")
    print()

    print(f"  {'PROJECT':<25} {'BUDGET %':>8} {'$/DAY':>8} {'SPENT':>8} {'STATUS':>8}")
    print(f"  {'-'*57}")

    for project_id, alloc in allocations.items():
        row = conn.execute("""
            SELECT COALESCE(SUM(cost_usd), 0), COALESCE(SUM(requests), 0)
            FROM daily_consumption WHERE date = ? AND project = ?
        """, (today, project_id)).fetchone()
        spent = row[0]
        pct = PROJECTS[project_id]["budget_pct"]
        status = "OK" if spent < alloc else "OVER"
        name = PROJECTS[project_id]["name"]
        print(f"  {name:<25} {pct:>7}% ${alloc:>7.2f} ${spent:>7.4f} {status:>8}")

    # Token sources by tier
    print(f"\n  {'SUBSCRIPTIONS (unlimited)':}")
    print(f"  {'-'*57}")
    for source_id, source in TOKEN_SOURCES.items():
        if source["type"] == "subscription":
            models = ", ".join(source["models"][:3])
            rpm = source.get("rate_limit_rpm", "?")
            cost = source.get("monthly_cost", 0)
            print(f"  {source_id:<25} ${cost:>6.0f}/mo  ~{rpm} rpm  {models}")

    print(f"\n  {'FREE TIER (daily limited)':}")
    print(f"  {'-'*57}")
    for source_id, source in TOKEN_SOURCES.items():
        if source["type"] == "free":
            limit = source.get("daily_limit", "∞")
            models = ", ".join(source["models"][:2])
            print(f"  {source_id:<25} {limit:>8} req/day  {models}")

    print(f"\n  {'METERED OVERFLOW (pay per use)':}")
    print(f"  {'-'*57}")
    for source_id, source in TOKEN_SOURCES.items():
        if source["type"] == "metered":
            cost = source.get("cost_per_1m", 0)
            models = ", ".join(source["models"][:2])
            print(f"  {source_id:<25} ${cost:>5.2f}/1M tok  {models}")

    print(f"{'='*65}\n")
    conn.close()


# ============================================
# CLI
# ============================================

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "status":
        show_status()
    elif cmd == "digest":
        digest_cycle()
    elif cmd == "allocate":
        allocs = allocate_budgets()
        for k, v in allocs.items():
            print(f"{k}: ${v:.4f}/day")
    elif cmd == "revenue":
        if len(sys.argv) >= 4:
            project = sys.argv[2]
            amount = float(sys.argv[3])
            update_revenue(project, amount)
        else:
            print("Usage: metabolism.py revenue <project> <amount_usd>")
    else:
        print("Usage: metabolism.py [status|digest|allocate|revenue <project> <amount>]")
