"""Tests for the v0.8.1 ToRivers bridge migration.

Covers:
- execute() posts an Objective (not a Squad task) with the expected shape
- execute() returns the completion artifact once the objective reaches paid
- execute() returns a timeout envelope when the deadline elapses
- _usd_to_mind converts USD to $MIND bounty units

The AsyncObjectivesClient is patched at the import site inside bridge.py so
no live objectives service is required.
"""
from __future__ import annotations

from typing import Any

import pytest

from sos.adapters.torivers import bridge as bridge_module
from sos.adapters.torivers.bridge import ToRiversBridge, _usd_to_mind
from sos.contracts.objective import Objective


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_OBJECTIVE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"  # valid ULID (26 chars)


def _make_objective(state: str = "open", artifact: str | None = None) -> Objective:
    now = Objective.now_iso()
    return Objective(
        id=_OBJECTIVE_ID,
        title="[ToRivers] monthly-seo-audit",
        state=state,  # type: ignore[arg-type]
        created_by="torivers-bridge:test-user",
        created_at=now,
        updated_at=now,
        project="trop",
        tags=["torivers", "workflow:monthly-seo-audit"],
        bounty_mind=2500,
        completion_artifact_url=artifact,
        completion_notes="done" if artifact else "",
    )


def _make_bridge() -> ToRiversBridge:
    return ToRiversBridge(
        squad_url="http://unused",
        bus_url="http://unused",
        bus_token="unused",
        objectives_url="http://unused-objectives:6068",
    )


async def _register_seo_audit(bridge: ToRiversBridge) -> str:
    workflows = ToRiversBridge.list_available_workflows()
    target = next(wf for wf in workflows if wf["name"] == "monthly-seo-audit")
    return await bridge.register_workflow(target)


# ---------------------------------------------------------------------------
# 1. execute() posts an Objective, not a Squad task
# ---------------------------------------------------------------------------


async def test_execute_posts_objective_not_squad_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = _make_bridge()
    automation_id = await _register_seo_audit(bridge)

    captured: dict[str, Any] = {}

    async def fake_create(self: Any, title: str, **kwargs: Any) -> Objective:
        captured["title"] = title
        captured["kwargs"] = kwargs
        return _make_objective(state="paid", artifact="https://s3.example.com/report.zip")

    async def fake_get(
        self: Any, obj_id: str, *, project: str | None = None
    ) -> Objective:
        return _make_objective(
            state="paid", artifact="https://s3.example.com/report.zip"
        )

    monkeypatch.setattr(
        "sos.adapters.torivers.bridge.AsyncObjectivesClient.create",
        fake_create,
    )
    monkeypatch.setattr(
        "sos.adapters.torivers.bridge.AsyncObjectivesClient.get",
        fake_get,
    )

    result = await bridge.execute(
        automation_id,
        input_data={"domain": "trop.example", "tenant": "trop"},
        user_id="trop-user-123",
    )

    # The call must have been routed to Objectives.create, not a Squad task.
    assert captured["title"] == "[ToRivers] monthly-seo-audit"
    kwargs = captured["kwargs"]
    assert kwargs["bounty_mind"] == 2500  # $25 → 2500 $MIND
    assert kwargs["project"] == "trop"
    assert "torivers" in kwargs["tags"]
    assert "workflow:monthly-seo-audit" in kwargs["tags"]
    assert kwargs["capabilities_required"] == ["analytics"]

    # And the return envelope is the v0.8.1 completed shape.
    assert result["status"] == "completed"
    assert result["task_id"] == _OBJECTIVE_ID
    assert result["automation_id"] == automation_id
    assert result["workflow"] == "monthly-seo-audit"
    assert result["artifact"] == "https://s3.example.com/report.zip"


# ---------------------------------------------------------------------------
# 2. execute() returns the artifact once the objective is paid
# ---------------------------------------------------------------------------


async def test_execute_completes_when_objective_paid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Make the poll effectively immediate so the test stays under 2s.
    monkeypatch.setattr(bridge_module, "_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(bridge_module, "_POLL_TIMEOUT_S", 5.0)

    bridge = _make_bridge()
    automation_id = await _register_seo_audit(bridge)

    async def fake_create(self: Any, title: str, **kwargs: Any) -> Objective:
        return _make_objective(state="open")

    call_counter = {"n": 0}

    async def fake_get(
        self: Any, obj_id: str, *, project: str | None = None
    ) -> Objective:
        call_counter["n"] += 1
        if call_counter["n"] == 1:
            return _make_objective(state="open")
        return _make_objective(
            state="paid",
            artifact="https://s3.example.com/final-report.zip",
        )

    monkeypatch.setattr(
        "sos.adapters.torivers.bridge.AsyncObjectivesClient.create",
        fake_create,
    )
    monkeypatch.setattr(
        "sos.adapters.torivers.bridge.AsyncObjectivesClient.get",
        fake_get,
    )

    result = await bridge.execute(
        automation_id,
        input_data={"domain": "trop.example"},
        user_id="trop-user-456",
    )

    assert result["status"] == "completed"
    assert result["artifact"] == "https://s3.example.com/final-report.zip"
    assert result["task_id"] == _OBJECTIVE_ID
    assert call_counter["n"] >= 2


# ---------------------------------------------------------------------------
# 3. execute() times out when the objective never reaches paid
# ---------------------------------------------------------------------------


async def test_execute_times_out_when_not_paid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bridge_module, "_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(bridge_module, "_POLL_TIMEOUT_S", 0.1)

    bridge = _make_bridge()
    automation_id = await _register_seo_audit(bridge)

    async def fake_create(self: Any, title: str, **kwargs: Any) -> Objective:
        return _make_objective(state="open")

    async def fake_get(
        self: Any, obj_id: str, *, project: str | None = None
    ) -> Objective:
        return _make_objective(state="claimed")

    monkeypatch.setattr(
        "sos.adapters.torivers.bridge.AsyncObjectivesClient.create",
        fake_create,
    )
    monkeypatch.setattr(
        "sos.adapters.torivers.bridge.AsyncObjectivesClient.get",
        fake_get,
    )

    result = await bridge.execute(
        automation_id,
        input_data={"domain": "trop.example"},
        user_id="trop-user-timeout",
    )

    assert result["status"] == "timeout"
    assert result["task_id"] == _OBJECTIVE_ID
    assert result["automation_id"] == automation_id
    assert result["workflow"] == "monthly-seo-audit"


# ---------------------------------------------------------------------------
# 4. _usd_to_mind conversion
# ---------------------------------------------------------------------------


def test_usd_to_mind_conversion() -> None:
    assert _usd_to_mind(25.00) == 2500
    assert _usd_to_mind(0) == 0
    assert _usd_to_mind(500.0) == 50000
