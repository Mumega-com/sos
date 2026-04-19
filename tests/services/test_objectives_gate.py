"""Tests for sos.services.objectives.gate — completion gate (v0.8.0).

All tests inject fake read_objective / write_objective and a fake economy_client
so no live Redis or economy service is needed.
"""
from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from sos.contracts.objective import Objective
from sos.services.objectives import gate as _gate_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_PARENT_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAW"


def _make_obj(
    state: str = "shipped",
    acks: list[str] | None = None,
    bounty: int = 100,
    holder: str | None = "kasra",
    parent_id: str | None = None,
) -> Objective:
    now = Objective.now_iso()
    return Objective(
        id=_VALID_ID,
        title="Test gate objective",
        state=state,  # type: ignore[arg-type]
        created_by="codex",
        created_at=now,
        updated_at=now,
        bounty_mind=bounty,
        holder_agent=holder,
        acks=acks or [],
        parent_id=parent_id,
    )


def _fake_economy_client() -> MagicMock:
    client = MagicMock()
    client.credit = AsyncMock(return_value={"ok": True})
    return client


# ---------------------------------------------------------------------------
# 1. Non-shipped state returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_completion_not_shipped_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    obj = _make_obj(state="claimed", acks=["reviewer"])
    monkeypatch.setattr(_gate_module, "read_objective", lambda obj_id, project=None: obj)

    result = await _gate_module.check_completion(_VALID_ID, economy_client=_fake_economy_client())
    assert result is None


# ---------------------------------------------------------------------------
# 2. Missing objective returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_completion_missing_obj_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_gate_module, "read_objective", lambda obj_id, project=None: None)

    result = await _gate_module.check_completion(_VALID_ID, economy_client=_fake_economy_client())
    assert result is None


# ---------------------------------------------------------------------------
# 3. Shipped but no acks returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_completion_shipped_no_acks_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    obj = _make_obj(state="shipped", acks=[])
    monkeypatch.setattr(_gate_module, "read_objective", lambda obj_id, project=None: obj)

    result = await _gate_module.check_completion(_VALID_ID, economy_client=_fake_economy_client())
    assert result is None


# ---------------------------------------------------------------------------
# 4. One peer ack transitions shipped -> paid
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_completion_one_peer_ack_transitions_to_paid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    obj = _make_obj(state="shipped", acks=["reviewer-agent"])
    written: dict[str, Any] = {}

    monkeypatch.setattr(_gate_module, "read_objective", lambda obj_id, project=None: obj)
    monkeypatch.setattr(_gate_module, "write_objective", lambda o: written.update({"obj": o}))

    result = await _gate_module.check_completion(_VALID_ID, economy_client=_fake_economy_client())
    assert result is not None
    assert result.state == "paid"
    assert written["obj"].state == "paid"


# ---------------------------------------------------------------------------
# 5. Economy client called with correct amount and memo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_completion_pays_bounty_with_correct_amount_and_memo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    obj = _make_obj(state="shipped", acks=["reviewer-agent"], bounty=250, holder="kasra")
    monkeypatch.setattr(_gate_module, "read_objective", lambda obj_id, project=None: obj)
    monkeypatch.setattr(_gate_module, "write_objective", lambda o: None)

    client = _fake_economy_client()
    await _gate_module.check_completion(_VALID_ID, economy_client=client)

    client.credit.assert_awaited_once_with(
        user_id="kasra",
        amount=250,
        reason=f"objective:{_VALID_ID}",
    )


# ---------------------------------------------------------------------------
# 6. Zero bounty does not call economy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_completion_zero_bounty_does_not_call_economy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    obj = _make_obj(state="shipped", acks=["reviewer"], bounty=0)
    monkeypatch.setattr(_gate_module, "read_objective", lambda obj_id, project=None: obj)
    monkeypatch.setattr(_gate_module, "write_objective", lambda o: None)

    client = _fake_economy_client()
    result = await _gate_module.check_completion(_VALID_ID, economy_client=client)

    assert result is not None
    assert result.state == "paid"
    client.credit.assert_not_awaited()


# ---------------------------------------------------------------------------
# 7. Parent-holder ack is sufficient even below N threshold
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_completion_parent_holder_ack_is_sufficient_even_below_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Raise the required acks threshold to 3
    monkeypatch.setenv("SOS_OBJECTIVES_REQUIRED_ACKS", "3")
    # Reload the module constant so the new env var takes effect
    import importlib
    import sos.services.objectives.gate as _gate_fresh
    importlib.reload(_gate_fresh)

    obj = _make_obj(
        state="shipped",
        acks=[_PARENT_ID],  # only one ack, but it's the parent_id — should be sufficient
        parent_id=_PARENT_ID,
    )
    written: dict[str, Any] = {}

    monkeypatch.setattr(_gate_fresh, "read_objective", lambda obj_id, project=None: obj)
    monkeypatch.setattr(_gate_fresh, "write_objective", lambda o: written.update({"obj": o}))

    client = _fake_economy_client()
    result = await _gate_fresh.check_completion(_VALID_ID, economy_client=client)

    assert result is not None
    assert result.state == "paid"

    # Restore
    monkeypatch.delenv("SOS_OBJECTIVES_REQUIRED_ACKS", raising=False)
    importlib.reload(_gate_fresh)


# ---------------------------------------------------------------------------
# 8. Economy failure still marks paid (fail-soft)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_completion_economy_failure_still_marks_paid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    obj = _make_obj(state="shipped", acks=["reviewer"], bounty=100, holder="kasra")
    written: dict[str, Any] = {}
    monkeypatch.setattr(_gate_module, "read_objective", lambda obj_id, project=None: obj)
    monkeypatch.setattr(_gate_module, "write_objective", lambda o: written.update({"obj": o}))

    failing_client = MagicMock()
    failing_client.credit = AsyncMock(side_effect=RuntimeError("economy is down"))

    # Should NOT raise — exception is swallowed
    result = await _gate_module.check_completion(_VALID_ID, economy_client=failing_client)

    assert result is not None
    assert result.state == "paid"
    assert written["obj"].state == "paid"


# ---------------------------------------------------------------------------
# 9. Already paid returns None (idempotency)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_completion_already_paid_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    obj = _make_obj(state="paid", acks=["reviewer"])
    monkeypatch.setattr(_gate_module, "read_objective", lambda obj_id, project=None: obj)

    result = await _gate_module.check_completion(_VALID_ID, economy_client=_fake_economy_client())
    assert result is None
