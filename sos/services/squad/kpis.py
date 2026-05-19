"""Squad KPI calculation — Phase 2 squad intelligence.

Computes a KPISnapshot for a given squad over a rolling 7-day window.
All queries hit SQLite directly via SquadDB (same pattern as service.py).
"""
from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx

from sos.observability.logging import get_logger
from sos.services.squad.service import SquadDB

log = get_logger("squad_kpis")

_PERIOD_DAYS = 7


@dataclass
class KPISnapshot:
    squad_id: str
    velocity: float          # tasks completed per day (7d window)
    success_rate: float      # done / (done + failed), 0–1
    bounty_score: float      # normalised 0–100
    wallet_growth: float     # % change in balance over 7d, can be negative
    utilization: float       # assigned_tasks / member_count, 0+
    tokens_used: int         # sum of token_budget on completed tasks (7d)
    tokens_by_grade: dict[str, int]  # {"diesel": N, "regular": N, ...}
    total_earned_cents: int  # lifetime from squad_wallets
    balance_cents: int       # current balance
    kpi_score: float         # 0–100 weighted composite
    period_days: int = _PERIOD_DAYS


def _grade_from_budget(token_budget: int) -> str:
    """Classify a token_budget into a fuel grade bucket."""
    if token_budget < 5_000:
        return "diesel"
    if token_budget < 20_000:
        return "regular"
    if token_budget < 50_000:
        return "premium"
    return "aviation"


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


async def _fetch_bounty_score(squad_id: str) -> float:
    """Call the Inkwell Worker bounty stats endpoint. Returns 0.0 on any error."""
    site_url = os.environ.get("SITE_URL", "")
    token = os.environ.get("MUMEGA_TOKEN", "")
    if not site_url:
        return 0.0
    try:
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{site_url}/api/bounties/stats",
                params={"squad_id": squad_id},
                headers=headers,
            )
            if resp.is_success:
                data = resp.json()
                return float(data.get("bounty_score", 0.0))
    except Exception as exc:
        log.warning("bounty_score fetch failed", squad_id=squad_id, error=str(exc))
    return 0.0


async def calculate_kpis(squad_id: str, db: SquadDB | None = None) -> KPISnapshot:
    """Compute a KPISnapshot for *squad_id* over the last 7 days."""
    if db is None:
        db = SquadDB()

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=_PERIOD_DAYS)
    window_start_iso = window_start.isoformat()

    with db.connect() as conn:
        # ── member count ────────────────────────────────────────────────────────
        import json as _json

        squad_row = conn.execute(
            "SELECT members_json FROM squads WHERE id = ?", (squad_id,)
        ).fetchone()
        member_count: int = 1  # guard against division-by-zero
        if squad_row:
            members = _json.loads(squad_row["members_json"] or "[]")
            member_count = max(1, len(members))

        # ── completed tasks in window ────────────────────────────────────────────
        done_rows = conn.execute(
            """
            SELECT token_budget
            FROM squad_tasks
            WHERE squad_id = ?
              AND status = 'done'
              AND completed_at >= ?
            """,
            (squad_id, window_start_iso),
        ).fetchall()

        done_count = len(done_rows)
        tokens_used = sum(int(r["token_budget"] or 0) for r in done_rows)
        tokens_by_grade: dict[str, int] = {
            "diesel": 0, "regular": 0, "premium": 0, "aviation": 0
        }
        for r in done_rows:
            grade = _grade_from_budget(int(r["token_budget"] or 0))
            tokens_by_grade[grade] += int(r["token_budget"] or 0)

        # ── failed tasks in window ───────────────────────────────────────────────
        failed_count: int = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM squad_tasks
            WHERE squad_id = ?
              AND status = 'failed'
              AND updated_at >= ?
            """,
            (squad_id, window_start_iso),
        ).fetchone()["n"]

        # ── assigned tasks right now ─────────────────────────────────────────────
        assigned_count: int = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM squad_tasks
            WHERE squad_id = ?
              AND status IN ('claimed', 'in_progress')
            """,
            (squad_id,),
        ).fetchone()["n"]

        # ── wallet data ──────────────────────────────────────────────────────────
        wallet_row = conn.execute(
            "SELECT balance_cents, total_earned_cents FROM squad_wallets WHERE squad_id = ?",
            (squad_id,),
        ).fetchone()
        balance_cents = int(wallet_row["balance_cents"]) if wallet_row else 0
        total_earned_cents = int(wallet_row["total_earned_cents"]) if wallet_row else 0

        # ── wallet growth: compare current balance against balance 7 days ago ────
        # balance_7d_ago ≈ current_balance − net_transactions_in_window
        # net = sum(earn) − sum(spend) in window
        tx_rows = conn.execute(
            """
            SELECT type, amount_cents
            FROM squad_transactions
            WHERE squad_id = ?
              AND created_at >= ?
            """,
            (squad_id, window_start_iso),
        ).fetchall()

        net_7d = 0
        for tx in tx_rows:
            if tx["type"] in ("earn", "mint"):
                net_7d += int(tx["amount_cents"])
            elif tx["type"] in ("spend", "transfer"):
                net_7d -= int(tx["amount_cents"])

        balance_7d_ago = balance_cents - net_7d
        if balance_7d_ago > 0:
            wallet_growth = net_7d / balance_7d_ago
        elif net_7d > 0:
            wallet_growth = 1.0  # grew from zero
        else:
            wallet_growth = 0.0

    # ── derived metrics ──────────────────────────────────────────────────────────
    velocity = done_count / _PERIOD_DAYS

    terminal = done_count + failed_count
    success_rate = (done_count / terminal) if terminal > 0 else 1.0

    utilization = assigned_count / member_count

    bounty_score = await _fetch_bounty_score(squad_id)

    # ── KPI formula ──────────────────────────────────────────────────────────────
    velocity_score = _clamp(velocity / 5.0, 0.0, 1.0) * 20.0
    success_score = _clamp(success_rate, 0.0, 1.0) * 20.0
    bounty_score_pts = _clamp(bounty_score / 100.0, 0.0, 1.0) * 15.0
    wallet_score = (_clamp(wallet_growth, -1.0, 1.0) + 1.0) / 2.0 * 15.0
    util_score = _clamp(utilization / 3.0, 0.0, 1.0) * 15.0
    token_score = 15.0 - _clamp(tokens_used / 100_000.0, 0.0, 1.0) * 15.0

    kpi_score = _clamp(
        velocity_score + success_score + bounty_score_pts + wallet_score + util_score + token_score,
        0.0,
        100.0,
    )

    return KPISnapshot(
        squad_id=squad_id,
        velocity=round(velocity, 4),
        success_rate=round(success_rate, 4),
        bounty_score=round(bounty_score, 4),
        wallet_growth=round(wallet_growth, 4),
        utilization=round(utilization, 4),
        tokens_used=tokens_used,
        tokens_by_grade=tokens_by_grade,
        total_earned_cents=total_earned_cents,
        balance_cents=balance_cents,
        kpi_score=round(kpi_score, 4),
        period_days=_PERIOD_DAYS,
    )
