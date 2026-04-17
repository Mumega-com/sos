"""Tenant-scoped data helpers: agent status, tasks, memory, skills/usage."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from .config import MIRROR_URL, SQUAD_URL
from .redis_helper import _get_redis

logger = logging.getLogger("dashboard")


def _agent_status(project: str | None) -> dict[str, Any]:
    try:
        r = _get_redis()
        # Check registry for agents associated with this project
        keys = r.keys("sos:registry:*")
        agents = []
        for key in keys:
            data = r.hgetall(key)
            if data:
                agents.append({
                    "name": key.split(":")[-1],
                    "status": data.get("status", "unknown"),
                    "last_seen": data.get("last_seen", ""),
                })
        if not agents:
            # Fallback: check bus:peers
            peers_raw = r.get("sos:peers")
            if peers_raw:
                try:
                    peers = json.loads(peers_raw)
                    for name, info in peers.items():
                        agents.append({
                            "name": name,
                            "status": "online",
                            "last_seen": info.get("last_seen", ""),
                        })
                except Exception:
                    pass
        return {"agents": agents, "online": sum(1 for a in agents if a.get("status") == "online")}
    except Exception:
        logger.debug("Redis unreachable", exc_info=True)
    return {"agents": [], "online": 0}


async def _fetch_tasks(project: str | None) -> list[dict[str, Any]]:
    try:
        params: dict[str, Any] = {"limit": 5}
        if project:
            params["squad"] = project
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{SQUAD_URL}/tasks", params=params)
            if resp.status_code == 200:
                data = resp.json()
                # Handle both list and dict-with-tasks responses
                if isinstance(data, list):
                    return data[:5]
                if isinstance(data, dict) and "tasks" in data:
                    return data["tasks"][:5]
    except Exception:
        logger.debug("Squad service unreachable", exc_info=True)
    return []


async def _fetch_memory(project: str | None, bus_token: str | None = None) -> dict[str, Any]:
    # Mirror exposes /stats (global count) and /recent/{agent} (scoped list).
    # /recent requires Bearer auth — customer tokens are auto-scoped to their project.
    try:
        headers = {"Authorization": f"Bearer {bus_token}"} if bus_token else {}
        async with httpx.AsyncClient(timeout=3) as client:
            agent = project or "river"
            recent_resp = await client.get(
                f"{MIRROR_URL}/recent/{agent}",
                params={"limit": 1},
                headers=headers,
            )
            entries: list[dict[str, Any]] = []
            if recent_resp.status_code == 200:
                data = recent_resp.json()
                entries = data.get("engrams", []) if isinstance(data, dict) else []

            count = len(entries)
            if project:
                count_resp = await client.get(
                    f"{MIRROR_URL}/recent/{project}",
                    params={"limit": 1000, "project": project},
                    headers=headers,
                )
                if count_resp.status_code == 200:
                    cdata = count_resp.json()
                    count = cdata.get("count", count) if isinstance(cdata, dict) else count
            else:
                stats_resp = await client.get(f"{MIRROR_URL}/stats")
                if stats_resp.status_code == 200:
                    count = stats_resp.json().get("total_engrams", count)

            return {
                "count": count,
                "latest": entries[0] if entries else None,
            }
    except Exception:
        logger.debug("Mirror unreachable", exc_info=True)
    return {"count": 0, "latest": None}


def _tenant_skills_and_usage(project: str | None) -> dict[str, Any]:
    """Tenant-scoped moat data for the customer dashboard.

    Reads the SkillCard registry + UsageLog and returns three summaries:
      - skills_invoked: skills this tenant has used (from invocations_by_tenant)
      - skills_authored: skills whose author_agent == agent:<slug>, with earnings
      - recent_usage: last 10 UsageLog events tenant-scoped
    Empty-state tolerant: any missing data returns a harmless empty list.
    """
    out: dict[str, Any] = {
        "skills_invoked": [],
        "skills_authored": [],
        "recent_usage": [],
        "total_spent_micros": 0,
        "total_earned_micros": 0,
    }
    if not project:
        return out

    # Skills registry
    try:
        from sos.skills.registry import Registry
        reg = Registry()
        cards = reg.list()
        author_uri = f"agent:{project}"
        for card in cards:
            earnings = card.earnings
            # Did this tenant invoke it?
            if earnings and earnings.invocations_by_tenant:
                if project in earnings.invocations_by_tenant:
                    out["skills_invoked"].append({
                        "id": card.id,
                        "name": card.name,
                        "author": card.author_agent,
                        "invocations": earnings.invocations_by_tenant[project],
                        "verification": card.verification.status if card.verification else "unverified",
                    })
            # Did this tenant author it?
            if card.author_agent == author_uri:
                out["skills_authored"].append({
                    "id": card.id,
                    "name": card.name,
                    "total_invocations": earnings.total_invocations if earnings else 0,
                    "total_earned_micros": earnings.total_earned_micros if earnings else 0,
                    "unique_tenants": len(earnings.invocations_by_tenant or {}) if earnings else 0,
                    "marketplace_listed": bool(card.commerce and card.commerce.marketplace_listed),
                })
                if earnings and earnings.total_earned_micros:
                    out["total_earned_micros"] += earnings.total_earned_micros
    except Exception:
        logger.debug("Registry read failed", exc_info=True)

    # UsageLog — tenant-scoped
    try:
        from sos.services.economy.usage_log import UsageLog
        log = UsageLog()
        events = log.read_all(tenant=project, limit=10)
        for e in events[::-1]:  # newest first
            out["recent_usage"].append({
                "occurred_at": e.occurred_at,
                "model": e.model,
                "endpoint": e.endpoint,
                "cost_micros": e.cost_micros,
            })
            out["total_spent_micros"] += e.cost_micros
    except Exception:
        logger.debug("UsageLog read failed", exc_info=True)

    return out


def _fmt_micros(micros: int) -> str:
    """Format integer micros as $X.XX (1 cent = 10_000 micros)."""
    if micros <= 0:
        return "$0.00"
    cents = micros / 10_000
    return f"${cents / 100:,.2f}"
