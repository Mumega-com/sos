"""Universal seed ruliads — the 24 tiny rules that make the seed alive.

Sprint 012 OmniA. Each ruliad is a pure function: takes state, returns action or None.
Together they produce intelligent organizational behavior.

Categories:
  LIFECYCLE (1-4):   seed wakes up, learns, earns trust
  RELATIONSHIP (5-8): connects to the business's people and deals
  HEALTH (9-12):     monitors the business's surfaces
  GROWTH (13-20):    helps the business grow (includes S008-E refactored 4)
  CARE (21-24):      becomes part of the family (requires gtm.principal_state)

Athena constraints baked in:
  - warm-intro: single person's own graph only, never cross-contact
  - upsell-signal: cohort stats N≥20 only, never individual traces
  - burnout-detect: opt-in + principal-only visibility
  - earn-trust: RULIAD_CEILING_CLASSES is substrate constant, not configurable
  - competitor-change: robots.txt + daily rate + no PII
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any

log = logging.getLogger("sos.seeds.ruliads")

# Substrate constant — NOT env-overridable (Athena LOCK)
RULIAD_CEILING_CLASSES: frozenset[str] = frozenset({"cash_flow", "legal", "health"})

_HOT_KEYWORDS = re.compile(
    r"\b(buying|budget|timeline|when can|how much|sign|contract|proposal)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# LIFECYCLE (1-4)
# ---------------------------------------------------------------------------


def ruliad_first_hello(agent_name: str, channel_context: dict) -> dict | None:
    """R1: On first boot, introduce self. Quiet, warm, sets expectations."""
    if channel_context.get("hello_sent"):
        return None
    return {
        "action": "send_message",
        "text": (
            f"Hi, I'm {agent_name}. I'm here to help your team never miss "
            f"a follow-up. I'll be quiet for a few days while I learn your rhythm."
        ),
        "mark": {"hello_sent": True},
    }


def ruliad_learn_rhythm(messages: list[dict], mirror_state: dict) -> dict | None:
    """R2: After 20+ messages, detect busy/quiet hours and communication style."""
    if len(messages) < 20:
        return None
    if mirror_state.get("rhythm_learned"):
        return None

    hours = [m.get("hour", 12) for m in messages if m.get("hour") is not None]
    if not hours:
        return None

    from collections import Counter
    hour_counts = Counter(hours)
    peak_hour = hour_counts.most_common(1)[0][0]
    avg_length = sum(len(m.get("text", "")) for m in messages) / len(messages)

    return {
        "action": "store_rhythm",
        "peak_hour": peak_hour,
        "avg_message_length": round(avg_length),
        "style": "brief" if avg_length < 50 else "detailed",
        "mark": {"rhythm_learned": True},
    }


def ruliad_first_insight(day_count: int, patterns: list[dict]) -> dict | None:
    """R3: Day 3+, share first observation. Earns trust before intervening."""
    if day_count < 3:
        return None
    if not patterns:
        return None
    pattern = patterns[0]
    return {
        "action": "send_message",
        "text": (
            f"I noticed {pattern.get('description', 'a pattern in your workflow')}. "
            f"Want me to flag this going forward?"
        ),
        "awaits_response": True,
    }


def ruliad_earn_trust(
    accepted_count: int,
    rejected_count: int,
    current_frequency: float,
    action_class: str,
) -> dict | None:
    """R4: Accepted insights increase frequency. Rejected decrease.
    CONSTRAINT: ceiling classes ALWAYS stay human regardless of trust."""
    if action_class in RULIAD_CEILING_CLASSES:
        return {"action": "keep_human", "reason": f"{action_class} is ceiling-class"}

    total = accepted_count + rejected_count
    if total == 0:
        return None

    acceptance_rate = accepted_count / total
    if acceptance_rate > 0.7:
        new_freq = min(current_frequency * 1.2, 10.0)
    elif acceptance_rate < 0.3:
        new_freq = max(current_frequency * 0.5, 0.1)
    else:
        new_freq = current_frequency

    return {"action": "adjust_frequency", "new_frequency": round(new_freq, 2)}


# ---------------------------------------------------------------------------
# RELATIONSHIP (5-8)
# ---------------------------------------------------------------------------


def ruliad_new_contact_detected(message_text: str, known_names: set[str]) -> dict | None:
    """R5: Message mentions person not in graph → ask to classify."""
    words = message_text.split()
    capitalized = [w for w in words if w[0:1].isupper() and len(w) > 2 and w not in known_names]
    if not capitalized:
        return None
    name = capitalized[0]
    return {
        "action": "ask_classification",
        "name": name,
        "text": f"Is {name} a client, partner, or lead?",
    }


def ruliad_relationship_mapped(
    person_a: str, person_b: str, context: str, existing_edges: set[tuple]
) -> dict | None:
    """R6: Person A mentions person B in deal context → create edge."""
    edge = (person_a, person_b)
    if edge in existing_edges:
        return None
    return {
        "action": "create_edge",
        "from": person_a,
        "to": person_b,
        "edge_type": "mentioned_with",
        "context": context[:200],
    }


def ruliad_warm_intro(
    person_id: str, person_graph: dict, needs: list[str]
) -> dict | None:
    """R7: Suggest connections within SINGLE PERSON'S OWN graph.
    CONSTRAINT: never cross contacts, never cross tenants."""
    contacts = person_graph.get("contacts", [])
    for need in needs:
        for contact in contacts:
            if need.lower() in [s.lower() for s in contact.get("skills", [])]:
                return {
                    "action": "suggest_intro",
                    "text": (
                        f"Did you know {contact['name']} in your contacts "
                        f"might be able to help with {need}?"
                    ),
                    "source": "own_graph_only",
                }
    return None


def ruliad_anniversary(contacts: list[dict], today: datetime) -> list[dict]:
    """R8: Client first-contact anniversary → suggest check-in."""
    results = []
    for contact in contacts:
        first = contact.get("first_contact_date")
        if not first:
            continue
        if isinstance(first, str):
            try:
                first = datetime.fromisoformat(first)
            except ValueError:
                continue
        delta = today - first
        if delta.days > 0 and delta.days % 365 == 0:
            years = delta.days // 365
            results.append({
                "action": "send_message",
                "text": (
                    f"It's been {years} year{'s' if years > 1 else ''} since you "
                    f"started working with {contact['name']}. Worth a check-in?"
                ),
            })
    return results


# ---------------------------------------------------------------------------
# HEALTH (9-12)
# ---------------------------------------------------------------------------


def ruliad_website_down(health_check_ok: bool) -> dict | None:
    """R9: Website health check fails → alert immediately."""
    if health_check_ok:
        return None
    return {
        "action": "alert",
        "severity": "high",
        "text": "Your website appears to be down. Investigating.",
    }


def ruliad_seo_drop(
    impressions_this_week: int, impressions_last_week: int
) -> dict | None:
    """R10: Search impressions dropped >30% → alert."""
    if impressions_last_week == 0:
        return None
    ratio = impressions_this_week / impressions_last_week
    if ratio < 0.7:
        drop_pct = round((1 - ratio) * 100)
        return {
            "action": "send_message",
            "text": (
                f"Your search impressions dropped {drop_pct}% this week "
                f"({impressions_this_week:,} vs {impressions_last_week:,}). "
                f"Want me to investigate?"
            ),
        }
    return None


def ruliad_review_alert(review: dict) -> dict | None:
    """R11: Negative review detected → alert + draft response."""
    sentiment = review.get("sentiment", 0)
    if sentiment >= 0:
        return None
    return {
        "action": "alert",
        "severity": "medium",
        "text": (
            f"New negative review on {review.get('platform', 'unknown')}: "
            f"\"{review.get('text', '')[:100]}...\" Want me to draft a response?"
        ),
    }


def ruliad_competitor_change(
    competitor: str, changes: list[dict], robots_allowed: bool
) -> dict | None:
    """R12: Competitor page changed → alert. CONSTRAINT: robots.txt respected."""
    if not robots_allowed:
        return None
    if not changes:
        return None
    change_summary = ", ".join(c.get("page", "unknown") for c in changes[:3])
    return {
        "action": "send_message",
        "text": (
            f"Your competitor {competitor} just updated: {change_summary}. "
            f"Want me to summarize what changed?"
        ),
    }


# ---------------------------------------------------------------------------
# GROWTH (13-20) — includes S008-E refactored 4
# ---------------------------------------------------------------------------


def ruliad_stale_deal(deal: dict, stale_days: int = 7) -> dict | None:
    """R13: Deal with no action > N days → nudge. (S008-E refactored)"""
    last_action = deal.get("last_action_at")
    if not last_action:
        return None
    if isinstance(last_action, str):
        last_action = datetime.fromisoformat(last_action)
    age = (datetime.now(timezone.utc) - last_action).days
    if age <= stale_days:
        return None
    if deal.get("stage") in ("closed-won", "closed-lost"):
        return None
    return {
        "action": "send_message",
        "text": (
            f"Stale deal: {deal.get('contact_name', 'Unknown')} / "
            f"{deal.get('company_name', 'Unknown')} — {age} days since last action "
            f"at stage {deal.get('stage', '?')}. Want to follow up?"
        ),
    }


def ruliad_hot_opportunity(conversation_summary: str) -> dict | None:
    """R14: Buying keywords in conversation → flag. (S008-E refactored)"""
    match = _HOT_KEYWORDS.search(conversation_summary or "")
    if not match:
        return None
    return {
        "action": "send_message",
        "text": (
            f"Hot signal: someone mentioned \"{match.group(0)}\" in a recent "
            f"conversation. Want to push on this?"
        ),
    }


def ruliad_missing_action(action: dict, grace_minutes: int = 30) -> dict | None:
    """R15: Overdue follow-up past grace → alert. (S008-E refactored)"""
    due = action.get("due_at")
    if not due:
        return None
    if isinstance(due, str):
        due = datetime.fromisoformat(due)
    if action.get("completed_at") or action.get("status") != "pending":
        return None
    overdue_by = (datetime.now(timezone.utc) - due).total_seconds() / 60
    if overdue_by < grace_minutes:
        return None
    return {
        "action": "alert",
        "severity": "medium",
        "text": (
            f"Missed follow-up: scheduled for {due.strftime('%I:%M %p')}. "
            f"No completion logged. What happened?"
        ),
    }


def ruliad_daily_summary(priorities: list[dict]) -> dict | None:
    """R16: 8am daily summary of top 3 actions. (S008-E refactored)"""
    if not priorities:
        return None
    lines = []
    for i, p in enumerate(priorities[:3], 1):
        desc = p.get("description", p.get("action_type", "action"))
        target = p.get("target_name", "")
        lines.append(f"{i}. {desc}" + (f" for {target}" if target else ""))
    return {
        "action": "send_message",
        "text": "Today's priorities:\n\n" + "\n".join(lines),
    }


def ruliad_content_gap(query: str, impressions: int, clicks: int) -> dict | None:
    """R17: Ranking for query but low CTR → suggest improvement."""
    if impressions < 100 or clicks >= 5:
        return None
    ctr = round(clicks / impressions * 100, 1) if impressions else 0
    return {
        "action": "send_message",
        "text": (
            f"You're ranking for \"{query}\" ({impressions:,} impressions) "
            f"but only {ctr}% click through. Want me to improve the meta description?"
        ),
    }


def ruliad_upsell_signal(
    customer_purchases: list[str],
    cohort_patterns: dict[str, list[str]],
    cohort_size: int,
) -> dict | None:
    """R18: Cohort-based upsell suggestion.
    CONSTRAINT: cohort stats only, N≥20, never individual traces."""
    if cohort_size < 20:
        return None
    for purchased in customer_purchases:
        also_bought = cohort_patterns.get(purchased, [])
        for suggestion in also_bought:
            if suggestion not in customer_purchases:
                return {
                    "action": "send_message",
                    "text": (
                        f"Businesses like yours that use {purchased} "
                        f"often also benefit from {suggestion}. Worth exploring?"
                    ),
                    "source": "cohort_aggregate",
                    "cohort_size": cohort_size,
                }
    return None


def ruliad_seasonal_pattern(
    revenue_this_month: float,
    revenue_same_month_last_year: float,
    month_name: str,
) -> dict | None:
    """R19: Same month last year was significantly higher → alert."""
    if revenue_same_month_last_year <= 0:
        return None
    if revenue_this_month >= revenue_same_month_last_year * 0.8:
        return None
    drop = round((1 - revenue_this_month / revenue_same_month_last_year) * 100)
    return {
        "action": "send_message",
        "text": (
            f"Last {month_name}, your revenue was {drop}% higher than this month. "
            f"Seasonal pattern? Want to prep a campaign?"
        ),
    }


def ruliad_milestone_celebrate(
    deals_closed_this_month: int,
    personal_best: int,
    rep_name: str,
) -> dict | None:
    """R20: New personal best → celebrate."""
    if deals_closed_this_month <= personal_best:
        return None
    above = deals_closed_this_month - personal_best
    return {
        "action": "send_message",
        "text": (
            f"New personal best! {deals_closed_this_month} deals closed this month — "
            f"that's {above} above your previous record. Well done{', ' + rep_name if rep_name else ''}."
        ),
    }


# ---------------------------------------------------------------------------
# CARE (21-24) — requires gtm.principal_state (migration 050)
# ---------------------------------------------------------------------------


def ruliad_weekend_silence(is_weekend: bool, has_urgent: bool) -> dict | None:
    """R21: Weekend + no urgent signals → don't message."""
    if is_weekend and not has_urgent:
        return {"action": "suppress_all", "reason": "weekend_silence"}
    return None


def ruliad_burnout_detect(
    response_times_14d: list[float],
    activity_counts_14d: list[int],
    opt_in: bool,
    is_principal: bool,
) -> dict | None:
    """R22: CONSTRAINT: opt-in only, principal-only visibility.
    Response time increasing + activity decreasing over 2 weeks → private check-in."""
    if not opt_in or not is_principal:
        return None
    if len(response_times_14d) < 10 or len(activity_counts_14d) < 10:
        return None

    mid = len(response_times_14d) // 2
    early_rt = sum(response_times_14d[:mid]) / mid
    late_rt = sum(response_times_14d[mid:]) / (len(response_times_14d) - mid)
    early_act = sum(activity_counts_14d[:mid]) / mid
    late_act = sum(activity_counts_14d[mid:]) / (len(activity_counts_14d) - mid)

    if late_rt > early_rt * 1.5 and late_act < early_act * 0.7:
        return {
            "action": "private_message",
            "visibility": "principal_only",
            "text": (
                "I've noticed your response time has increased and activity "
                "has decreased over the past two weeks. Everything okay? "
                "No pressure — just checking in."
            ),
            "state_class": "burnout_risk",
        }
    return None


def ruliad_comeback(days_inactive: int, summary: str) -> dict | None:
    """R23: Person returns after 14+ days → welcome back with summary."""
    if days_inactive < 14:
        return None
    return {
        "action": "send_message",
        "text": (
            f"Welcome back! Here's what happened while you were away:\n\n"
            f"{summary}\n\n"
            f"Nothing urgent. Take your time."
        ),
    }


def ruliad_gratitude(
    team_performance_this_q: float,
    team_performance_last_q: float,
    biggest_driver: str,
) -> dict | None:
    """R24: Quarter-end, team improved → celebrate."""
    if team_performance_this_q <= team_performance_last_q:
        return None
    improvement = round(
        (team_performance_this_q - team_performance_last_q)
        / team_performance_last_q * 100
    )
    return {
        "action": "send_message",
        "text": (
            f"Your team improved {improvement}% this quarter. "
            f"The biggest driver was {biggest_driver}. Worth celebrating."
        ),
    }
