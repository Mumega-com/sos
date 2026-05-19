"""SOS kernel — deliberative arbitration (v0.5.2).

When multiple agents propose conflicting actions on the same resource,
arbitration picks a winner. Design: intent-proposal-ratification.

Proposals ARE audit ``INTENT`` events — we reuse the spine from v0.5.0
rather than adding a second durable store. ``arbitrate()`` reads recent
intents for a given ``(tenant, resource)`` within a time window, sorts
by the strategy, and emits an ``ARBITRATION`` audit event.

Surface
-------
Three public coroutines + a read helper. Nothing else:

- ``propose_intent(agent, action, resource, tenant, priority, metadata)``
  → writes an ``INTENT`` event tagged ``metadata.arbitration=True`` with
  ``metadata.priority`` and returns the proposal id (the event id).
- ``arbitrate(resource, tenant, window_ms, strategy)`` →
  ``ArbitrationDecision``. Reads proposals in window, picks winner,
  emits ``ARBITRATION``.
- ``read_proposals(tenant, resource, window_ms)`` → ``list[AuditEvent]``
  (observability helper, not required for arbitration itself).

Strategy: ``priority+coherence+recency``
----------------------------------------
Sort descending by:
1. ``metadata.priority`` (int, default 0). Higher wins.
2. Agent conductance sum: ``sum(G[agent][skill] for skill in G[agent])``
   from ``sos.kernel.conductance``. Higher wins. Agents absent from G
   score 0.
3. Recency: later ``timestamp`` wins.

A winner is deterministic given identical inputs. No randomness.
Strategy name is recorded in the ``ArbitrationDecision`` — future
strategies ship as new values without schema churn.

Durability
----------
- No new persistence layer. Reads via ``sos.kernel.audit.read_events``;
  writes via ``sos.kernel.audit.append_event``. The audit spine is
  disk-authoritative, fsync'd, and already hardened.
- ``ArbitrationDecision`` is frozen (v0.5.2 baseline). Never remove,
  rename, or narrow fields.
- Arbitration failures are logged but never raise from the public API
  — an arbitration step must never brick a caller; default to "no
  winner" and let the policy gate denial path handle it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sos.contracts.arbitration import ArbitrationDecision, LoserRecord
from sos.contracts.audit import AuditDecision, AuditEvent, AuditEventKind
from sos.kernel.audit import append_event as _audit_append
from sos.kernel.audit import new_event as _audit_new_event
from sos.kernel.audit import read_events as _audit_read

logger = logging.getLogger("sos.kernel.arbitration")

DEFAULT_STRATEGY = "priority+coherence+recency"
DEFAULT_WINDOW_MS = 500


async def propose_intent(
    *,
    agent: str,
    action: str,
    resource: str,
    tenant: str = "mumega",
    priority: int = 0,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Record a proposal. Returns the proposal id (audit event id).

    The event is written with ``kind=INTENT`` and
    ``metadata.arbitration=True`` so ``read_proposals`` can filter it
    back out of the general intent stream.
    """
    meta = dict(metadata or {})
    meta["arbitration"] = True
    meta["priority"] = int(priority)

    ev = _audit_new_event(
        agent=agent,
        tenant=tenant,
        kind=AuditEventKind.INTENT,
        action=action,
        target=resource,
        decision=AuditDecision.NOT_APPLICABLE,
        reason=f"arbitration proposal (priority={priority})",
        policy_tier="arbitration_proposal",
        metadata=meta,
    )
    return await _audit_append(ev)


def read_proposals(
    tenant: str,
    resource: str,
    *,
    window_ms: int = DEFAULT_WINDOW_MS,
) -> list[AuditEvent]:
    """Return ``INTENT`` proposals for ``resource`` within the window.

    Window is measured backward from the current UTC instant. Today's
    and yesterday's audit files are consulted so windows that straddle
    midnight still work.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(milliseconds=window_ms)
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    proposals: list[AuditEvent] = []
    for date_str in (yesterday, today):
        for ev in _audit_read(tenant, date=date_str, kind=AuditEventKind.INTENT, limit=10_000):
            if ev.target != resource:
                continue
            if not ev.metadata.get("arbitration"):
                continue
            try:
                ts = datetime.fromisoformat(ev.timestamp)
            except ValueError:
                continue
            if ts < cutoff:
                continue
            proposals.append(ev)
    return proposals


def _agent_conductance_sum(agent: str) -> float:
    """Sum of ``G[agent][skill]`` across all skills — the kernel's
    proven-flow signal for an agent. Agents absent from the matrix score 0.
    Unavailable conductance file → 0 for everyone (fail-soft)."""
    try:
        from sos.kernel.conductance import _load_conductance

        G = _load_conductance()
    except Exception as exc:
        logger.debug("conductance load failed (%s): %s", type(exc).__name__, exc)
        return 0.0
    row = G.get(agent)
    if not row:
        return 0.0
    return float(sum(row.values()))


def _rank_key(ev: AuditEvent) -> tuple[int, float, str]:
    """Sort key per the default strategy. Descending sort via negation."""
    priority = int(ev.metadata.get("priority", 0))
    conductance = _agent_conductance_sum(ev.agent)
    timestamp = ev.timestamp  # ISO-8601, lexicographic sort == chronological
    return (priority, conductance, timestamp)


async def arbitrate(
    *,
    resource: str,
    tenant: str = "mumega",
    window_ms: int = DEFAULT_WINDOW_MS,
    strategy: str = DEFAULT_STRATEGY,
) -> ArbitrationDecision:
    """Pick one winner among proposals for ``resource`` within ``window_ms``.

    Emits one ``ARBITRATION`` audit event capturing the outcome. Never
    raises — returns a no-winner decision on any internal failure so
    callers can continue their denial path cleanly.
    """
    try:
        proposals = read_proposals(tenant, resource, window_ms=window_ms)
    except Exception as exc:
        logger.warning(
            "arbitration read failed (%s): %s — returning empty decision",
            type(exc).__name__,
            exc,
        )
        proposals = []

    if not proposals:
        decision = ArbitrationDecision(
            resource=resource,
            tenant=tenant,
            strategy=strategy,
            window_ms=window_ms,
            winner_agent=None,
            winner_proposal_id=None,
            winner_reason="no proposals in window",
            losers=[],
            proposal_count=0,
        )
        return await _emit(decision)

    if strategy != DEFAULT_STRATEGY:
        # Future strategies plug in here. For now, fall back to default
        # but record what was asked in the audit trail.
        logger.debug(
            "unknown strategy '%s' — falling back to '%s'", strategy, DEFAULT_STRATEGY
        )

    ranked = sorted(proposals, key=_rank_key, reverse=True)
    winner = ranked[0]
    losers_events = ranked[1:]

    winner_priority = int(winner.metadata.get("priority", 0))
    winner_conductance = _agent_conductance_sum(winner.agent)
    winner_reason = (
        f"priority={winner_priority}, "
        f"conductance={winner_conductance:.2f}, "
        f"timestamp={winner.timestamp}"
    )

    losers: list[LoserRecord] = []
    for ev in losers_events:
        loser_priority = int(ev.metadata.get("priority", 0))
        loser_conductance = _agent_conductance_sum(ev.agent)
        losers.append(
            LoserRecord(
                agent=ev.agent,
                proposal_id=ev.id,
                reason=(
                    f"lost to {winner.agent} "
                    f"(priority={loser_priority} vs {winner_priority}, "
                    f"conductance={loser_conductance:.2f} vs {winner_conductance:.2f})"
                ),
                priority=loser_priority,
            )
        )

    decision = ArbitrationDecision(
        resource=resource,
        tenant=tenant,
        strategy=strategy,
        window_ms=window_ms,
        winner_agent=winner.agent,
        winner_proposal_id=winner.id,
        winner_reason=winner_reason,
        losers=losers,
        proposal_count=len(proposals),
        metadata={
            "winner_priority": winner_priority,
            "winner_conductance": winner_conductance,
        },
    )
    return await _emit(decision)


async def _emit(decision: ArbitrationDecision) -> ArbitrationDecision:
    """Write one ARBITRATION audit event and return decision with audit_id."""
    try:
        ev = _audit_new_event(
            agent=decision.winner_agent or "arbitration",
            tenant=decision.tenant,
            kind=AuditEventKind.ARBITRATION,
            action="arbitrate",
            target=decision.resource,
            decision=AuditDecision.ALLOW if decision.winner_agent else AuditDecision.NOT_APPLICABLE,
            reason=decision.winner_reason,
            policy_tier=decision.strategy,
            metadata={
                "window_ms": decision.window_ms,
                "proposal_count": decision.proposal_count,
                "losers": [loser.model_dump() for loser in decision.losers],
                **decision.metadata,
            },
        )
        audit_id = await _audit_append(ev)
        return decision.model_copy(update={"audit_id": audit_id})
    except Exception as exc:
        logger.warning("arbitration audit emit failed (%s): %s", type(exc).__name__, exc)
        return decision


__all__ = [
    "propose_intent",
    "arbitrate",
    "read_proposals",
    "DEFAULT_STRATEGY",
    "DEFAULT_WINDOW_MS",
]
