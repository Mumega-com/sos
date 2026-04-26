"""Knight protocols — Sprint 008 S008-E / G80.

4 V1 protocols covering the deal lifecycle:
1. stale-deal-nudge (past)
2. hot-opportunity-flag (present)
3. missing-action-alert (future, precision-locked per Athena WARN-4)
4. daily-priority-summary (reset)

All protocols read from the gtm schema and emit Discord messages via
a provided send function. Idempotency tracked via gtm.actions table.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Callable

log = logging.getLogger("sos.gtm.knight_protocols")

_HOT_KEYWORDS_RE = re.compile(
    r"\b(buying|budget|timeline|when can|how much|sign|contract|proposal)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Protocol 1: stale-deal-nudge
# ---------------------------------------------------------------------------


def check_stale_deals(
    conn: Any,
    knight_id: str,
    stale_days: int = 7,
) -> list[dict[str, Any]]:
    """Find deals owned by this knight that are stale (no action in N days).

    Returns list of stale deal dicts. Does NOT fire alerts — caller decides.
    Checks gtm.actions to avoid re-alerting within the stale window.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.id, d.stage, d.value_cents, d.last_action_at,
                       p.name AS contact_name, c.name AS company_name
                FROM gtm.deals d
                LEFT JOIN gtm.people p ON d.person_id = p.id
                LEFT JOIN gtm.companies c ON d.company_id = c.id
                WHERE d.owner_knight_id = %s
                  AND d.stage NOT IN ('closed-won', 'closed-lost')
                  AND d.last_action_at < now() - interval '%s days'
                  AND d.deleted_at IS NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM gtm.actions a
                      WHERE a.target_id = d.id AND a.action_type = 'stale_deal_nudge'
                        AND a.created_at > now() - interval '%s days'
                  )
                """,
                (knight_id, stale_days, stale_days),
            )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in rows]
    except Exception as exc:
        log.error("check_stale_deals failed: %s", exc)
        return []


def fire_stale_deal_nudge(
    conn: Any,
    knight_id: str,
    deal: dict[str, Any],
) -> dict[str, Any]:
    """Record a stale-deal-nudge action. Returns the action dict."""
    from sos.services.gtm.graph import create_action

    days_ago = 0
    if deal.get("last_action_at"):
        days_ago = (datetime.now(timezone.utc) - deal["last_action_at"]).days

    return create_action(
        conn,
        knight_id=knight_id,
        action_type="stale_deal_nudge",
        target_id=str(deal["id"]),
        target_type="deal",
        payload={
            "contact_name": deal.get("contact_name", "Unknown"),
            "company_name": deal.get("company_name", "Unknown"),
            "stage": deal.get("stage", "unknown"),
            "days_stale": days_ago,
        },
    )


# ---------------------------------------------------------------------------
# Protocol 2: hot-opportunity-flag
# ---------------------------------------------------------------------------


def check_hot_conversations(
    conn: Any,
    knight_id: str,
    hours_back: int = 24,
) -> list[dict[str, Any]]:
    """Find recent conversations with hot-opportunity keywords.

    Returns conversations not yet flagged via gtm.actions.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT cv.id, cv.summary, cv.participants, cv.occurred_at, cv.discord_message_id
                FROM gtm.conversations cv
                WHERE cv.occurred_at > now() - interval '%s hours'
                  AND NOT EXISTS (
                      SELECT 1 FROM gtm.actions a
                      WHERE a.target_id = cv.id AND a.action_type = 'hot_opportunity_flag'
                  )
                """,
                (hours_back,),
            )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            results = []
            for row in rows:
                conv = dict(zip(cols, row))
                summary = conv.get("summary", "") or ""
                match = _HOT_KEYWORDS_RE.search(summary)
                if match:
                    conv["matched_keyword"] = match.group(0)
                    results.append(conv)
            return results
    except Exception as exc:
        log.error("check_hot_conversations failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Protocol 3: missing-action-alert (precision-locked per WARN-4)
# ---------------------------------------------------------------------------


def check_missing_actions(
    conn: Any,
    knight_id: str,
    grace_minutes: int = 30,
) -> list[dict[str, Any]]:
    """Find overdue follow-up actions (past due + grace window).

    Only returns actions with status='pending' and due_at + grace < now().
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.id, a.action_type, a.target_id, a.target_type,
                       a.due_at, a.payload, a.status
                FROM gtm.actions a
                WHERE a.knight_id = %s
                  AND a.action_type = 'followup'
                  AND a.completed_at IS NULL
                  AND a.status = 'pending'
                  AND now() > a.due_at + interval '%s minutes'
                """,
                (knight_id, grace_minutes),
            )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in rows]
    except Exception as exc:
        log.error("check_missing_actions failed: %s", exc)
        return []


def mark_action_alerted(conn: Any, action_id: str) -> None:
    """Mark an action as 'alerted' (status transition from 'pending')."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE gtm.actions SET status = 'alerted' WHERE id = %s AND status = 'pending'",
                (action_id,),
            )
            conn.commit()
    except Exception as exc:
        conn.rollback()
        log.error("mark_action_alerted failed for %s: %s", action_id, exc)


# ---------------------------------------------------------------------------
# Protocol 4: daily-priority-summary
# ---------------------------------------------------------------------------


def generate_priority_summary(
    conn: Any,
    knight_id: str,
    top_n: int = 3,
) -> list[dict[str, Any]]:
    """Generate top-N priority actions for today's summary.

    Priority order:
    1. status='alerted' (missing actions) FIRST
    2. stale deals (last_action_at > 7 days)
    3. hot conversations from past 24h
    """
    priorities: list[dict[str, Any]] = []

    # 1. Alerted (missing) actions
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.id, a.action_type, a.target_id, a.payload, a.due_at
                FROM gtm.actions a
                WHERE a.knight_id = %s AND a.status = 'alerted'
                ORDER BY a.due_at ASC
                LIMIT %s
                """,
                (knight_id, top_n),
            )
            for row in cur.fetchall():
                cols = [d[0] for d in cur.description]
                item = dict(zip(cols, row))
                item["priority_reason"] = "missing_action"
                priorities.append(item)
    except Exception as exc:
        log.warning("priority_summary: alerted actions query failed: %s", exc)

    remaining = top_n - len(priorities)
    if remaining <= 0:
        return priorities[:top_n]

    # 2. Stale deals
    stale = check_stale_deals(conn, knight_id)
    for deal in stale[:remaining]:
        priorities.append({
            "id": deal["id"],
            "action_type": "stale_deal",
            "target_id": str(deal["id"]),
            "payload": {
                "contact_name": deal.get("contact_name"),
                "company_name": deal.get("company_name"),
            },
            "priority_reason": "stale_deal",
        })

    remaining = top_n - len(priorities)
    if remaining <= 0:
        return priorities[:top_n]

    # 3. Hot conversations
    hot = check_hot_conversations(conn, knight_id)
    for conv in hot[:remaining]:
        priorities.append({
            "id": conv["id"],
            "action_type": "hot_opportunity",
            "target_id": str(conv["id"]),
            "payload": {"keyword": conv.get("matched_keyword")},
            "priority_reason": "hot_opportunity",
        })

    return priorities[:top_n]
