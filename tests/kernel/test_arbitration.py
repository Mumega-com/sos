"""Tests for sos.kernel.arbitration — v0.5.2 deliberative arbitration."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sos.contracts.arbitration import ArbitrationDecision
from sos.contracts.audit import AuditEventKind
from sos.kernel.arbitration import arbitrate, propose_intent, read_proposals
from sos.kernel.audit import read_events


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect all audit I/O to a temp dir for the duration of each test."""
    monkeypatch.setattr("sos.kernel.audit._audit_dir", lambda: tmp_path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_single_proposer_wins() -> None:
    """One proposal wins unconditionally; no losers."""
    await propose_intent(agent="alpha", action="write", resource="r1", tenant="t1")
    decision = await arbitrate(resource="r1", tenant="t1", window_ms=5_000)
    assert decision.winner_agent == "alpha"
    assert decision.proposal_count == 1
    assert decision.losers == []


async def test_higher_priority_wins() -> None:
    """Higher-priority proposal beats lower-priority proposal."""
    await propose_intent(agent="lo", action="write", resource="r2", tenant="t1", priority=1)
    await propose_intent(agent="hi", action="write", resource="r2", tenant="t1", priority=5)
    decision = await arbitrate(resource="r2", tenant="t1", window_ms=5_000)
    assert decision.winner_agent == "hi"
    assert len(decision.losers) == 1
    assert decision.losers[0].agent == "lo"
    assert decision.losers[0].priority == 1


async def test_coherence_tie_break(monkeypatch: pytest.MonkeyPatch) -> None:
    """When priorities tie, higher conductance sum wins."""
    conductance_map = {"alpha": 3.0, "beta": 1.0}
    monkeypatch.setattr(
        "sos.kernel.arbitration._agent_conductance_sum",
        lambda agent: conductance_map.get(agent, 0.0),
    )
    await propose_intent(agent="alpha", action="write", resource="r3", tenant="t1", priority=0)
    await propose_intent(agent="beta", action="write", resource="r3", tenant="t1", priority=0)
    decision = await arbitrate(resource="r3", tenant="t1", window_ms=5_000)
    assert decision.winner_agent == "alpha"


async def test_recency_tie_break(monkeypatch: pytest.MonkeyPatch) -> None:
    """When priority and conductance tie, later timestamp wins."""
    monkeypatch.setattr(
        "sos.kernel.arbitration._agent_conductance_sum",
        lambda agent: 0.0,
    )
    await propose_intent(agent="early", action="write", resource="r4", tenant="t1", priority=0)
    await asyncio.sleep(0.015)
    await propose_intent(agent="late", action="write", resource="r4", tenant="t1", priority=0)
    decision = await arbitrate(resource="r4", tenant="t1", window_ms=5_000)
    assert decision.winner_agent == "late"


async def test_no_proposals_returns_empty_decision() -> None:
    """No proposals in window → empty decision, no exception."""
    decision = await arbitrate(resource="nonexistent", tenant="t1")
    assert decision.winner_agent is None
    assert decision.winner_proposal_id is None
    assert decision.winner_reason == "no proposals in window"
    assert decision.proposal_count == 0


async def test_arbitration_event_written() -> None:
    """arbitrate() emits an ARBITRATION audit event with proposal_count in metadata."""
    await propose_intent(agent="alpha", action="write", resource="r5", tenant="t1")
    decision = await arbitrate(resource="r5", tenant="t1", window_ms=5_000)
    arb_events = read_events("t1", kind=AuditEventKind.ARBITRATION)
    assert len(arb_events) >= 1
    match = next((e for e in arb_events if e.target == "r5"), None)
    assert match is not None
    assert "proposal_count" in match.metadata


async def test_multi_tenant_isolation() -> None:
    """Proposals in different tenants do not cross-contaminate arbitration."""
    await propose_intent(agent="agent-a", action="write", resource="r1", tenant="alpha")
    await propose_intent(agent="agent-b", action="write", resource="r1", tenant="beta")
    dec_alpha = await arbitrate(resource="r1", tenant="alpha", window_ms=5_000)
    dec_beta = await arbitrate(resource="r1", tenant="beta", window_ms=5_000)
    assert dec_alpha.winner_agent == "agent-a"
    assert dec_beta.winner_agent == "agent-b"
    assert dec_alpha.proposal_count == 1
    assert dec_beta.proposal_count == 1
