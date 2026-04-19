"""Tests for sos.agents.curator.

Scenarios covered:
- ``run_once`` short-circuits when no harvest objectives are open.
- ``run_once`` claims → harvests → completes → acks when winners exist.
- Memory-write failure does not wedge the harvest objective (we still ack).
- ``derive_role`` cascades from tag → capability → general.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from sos.agents import curator as curator_mod
from sos.agents.curator import CuratorAgent, derive_role
from sos.clients.objectives import AsyncObjectivesClient
from sos.contracts.objective import Objective


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ULIDs are Crockford base-32 (no I, L, O, U) and exactly 26 chars.
ULID_HARVEST = "01HWZZZZZZZZZZZZZZZZHARVAB"
ULID_PAID_A = "01HWZZZZZZZZZZZZZZZZPADAAA"
ULID_PAID_B = "01HWZZZZZZZZZZZZZZZZPADBBB"
ULID_PAID_C = "01HWZZZZZZZZZZZZZZZZPADCCC"


def _obj(
    obj_id: str,
    *,
    state: str = "open",
    tags: list[str] | None = None,
    caps: list[str] | None = None,
    project: str | None = "trop",
    outcome_score: float | None = None,
    artifact: str | None = None,
    title: str = "ex",
    description: str = "do a thing",
    bounty_mind: int = 0,
) -> Objective:
    now = Objective.now_iso()
    return Objective(
        id=obj_id,
        title=title,
        description=description,
        state=state,  # type: ignore[arg-type]
        tags=tags or [],
        capabilities_required=caps or [],
        project=project,
        outcome_score=outcome_score,
        completion_artifact_url=artifact,
        bounty_mind=bounty_mind,
        created_by="test",
        created_at=now,
        updated_at=now,
    )


class _FakeClient:
    """Replaces ``AsyncObjectivesClient`` instances created in-process.

    We install a factory on the module that constructs our fakes rather than
    real clients.  The fake shares state with the test so assertions can read
    call history.
    """

    def __init__(
        self,
        *,
        harvest_objs: list[Objective] | None = None,
        postmortem_objs: list[Objective] | None = None,
        stored_by_id: dict[str, Objective] | None = None,
        query_raises: Exception | None = None,
    ) -> None:
        self.harvest_objs = harvest_objs or []
        self.postmortem_objs = postmortem_objs or []
        self.stored_by_id = stored_by_id or {}
        self.query_raises = query_raises

        self.query_calls: list[dict[str, Any]] = []
        self.claim_calls: list[dict[str, Any]] = []
        self.heartbeat_calls: list[dict[str, Any]] = []
        self.complete_calls: list[dict[str, Any]] = []
        self.ack_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []

    # The real client exposes these as async methods; we mirror.
    async def query(self, *, tag: str | None = None, project: str | None = None, **_: Any) -> list[Objective]:
        self.query_calls.append({"tag": tag, "project": project})
        if self.query_raises:
            raise self.query_raises
        if tag == "kind:harvest-winners":
            return list(self.harvest_objs)
        if tag == "kind:postmortem":
            return list(self.postmortem_objs)
        return []

    async def claim(self, obj_id: str, *, agent: str | None = None, project: str | None = None) -> dict[str, Any]:
        self.claim_calls.append({"obj_id": obj_id, "agent": agent, "project": project})
        return {"ok": True, "obj_id": obj_id, "holder_agent": agent}

    async def heartbeat(self, obj_id: str, *, project: str | None = None) -> bool:
        self.heartbeat_calls.append({"obj_id": obj_id, "project": project})
        return True

    async def complete(
        self,
        obj_id: str,
        *,
        artifact_url: str,
        notes: str = "",
        project: str | None = None,
    ) -> dict[str, Any]:
        self.complete_calls.append(
            {"obj_id": obj_id, "artifact_url": artifact_url, "notes": notes, "project": project}
        )
        return {"ok": True, "state": "shipped"}

    async def ack(self, obj_id: str, *, acker: str, project: str | None = None) -> dict[str, Any]:
        self.ack_calls.append({"obj_id": obj_id, "acker": acker, "project": project})
        return {"ok": True, "acks": [acker], "state": "paid"}

    async def get(self, obj_id: str, *, project: str | None = None) -> Objective | None:
        self.get_calls.append({"obj_id": obj_id, "project": project})
        return self.stored_by_id.get(obj_id)


def _install_fake_client(
    monkeypatch: pytest.MonkeyPatch,
    fake: _FakeClient,
) -> None:
    """Patch AsyncObjectivesClient so curator gets our fake instead."""

    def factory(*_args: Any, **_kwargs: Any) -> _FakeClient:
        return fake

    monkeypatch.setattr(curator_mod, "AsyncObjectivesClient", factory, raising=True)


class _FakeHttpResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.text = ""


class _FakeHttpClient:
    """Minimal ``httpx.AsyncClient`` double for curator's memory POSTs."""

    def __init__(
        self,
        *,
        calls: list[dict[str, Any]],
        status_code: int = 200,
        raise_exc: Exception | None = None,
    ) -> None:
        self._calls = calls
        self._status = status_code
        self._raise = raise_exc

    async def __aenter__(self) -> "_FakeHttpClient":
        return self

    async def __aexit__(self, *_a: Any) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> _FakeHttpResponse:
        self._calls.append({"url": url, "json": kwargs.get("json"), "headers": kwargs.get("headers")})
        if self._raise is not None:
            raise self._raise
        return _FakeHttpResponse(status_code=self._status)


def _install_fake_http(
    monkeypatch: pytest.MonkeyPatch,
    *,
    calls: list[dict[str, Any]],
    status_code: int = 200,
    raise_exc: Exception | None = None,
) -> None:
    def factory(*_a: Any, **_kw: Any) -> _FakeHttpClient:
        return _FakeHttpClient(calls=calls, status_code=status_code, raise_exc=raise_exc)

    monkeypatch.setattr(curator_mod.httpx, "AsyncClient", factory, raising=True)


def _install_audit_events(monkeypatch: pytest.MonkeyPatch, events: list[dict[str, Any]]) -> None:
    """Patch ``CuratorAgent._read_recent_paid_events`` to return canned events."""

    async def _canned(self: CuratorAgent) -> list[dict[str, Any]]:
        return list(events)

    monkeypatch.setattr(
        CuratorAgent,
        "_read_recent_paid_events",
        _canned,
        raising=True,
    )


# ---------------------------------------------------------------------------
# derive_role
# ---------------------------------------------------------------------------


def test_derive_role_from_tags() -> None:
    obj = _obj(
        ULID_PAID_A,
        tags=["role:social", "kind:harvest-winners"],
        caps=["post-instagram"],
    )
    assert derive_role(obj) == "social"


def test_derive_role_falls_back_to_capability() -> None:
    obj = _obj(ULID_PAID_A, tags=["trop"], caps=["post-instagram"])
    assert derive_role(obj) == "post-instagram"


def test_derive_role_default_general() -> None:
    obj = _obj(ULID_PAID_A, tags=[], caps=[])
    assert derive_role(obj) == "general"


# ---------------------------------------------------------------------------
# run_once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_once_no_harvest_objectives_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeClient(harvest_objs=[], postmortem_objs=[])
    _install_fake_client(monkeypatch, fake)
    _install_audit_events(monkeypatch, [])

    agent = CuratorAgent(project="trop")
    written = await agent.run_once()

    assert written == 0
    assert fake.claim_calls == []
    assert fake.complete_calls == []
    assert fake.ack_calls == []


@pytest.mark.asyncio
async def test_run_once_claims_and_processes_harvest_objective(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harvest = _obj(
        ULID_HARVEST,
        tags=["kind:harvest-winners", "project:trop"],
        project="trop",
    )
    # Three paid objectives: A and B are winners (>=0.7), C is not (0.5).
    paid_a = _obj(
        ULID_PAID_A,
        state="paid",
        tags=["role:social"],
        outcome_score=0.95,
        artifact="https://example.test/a",
        description="social prompt A",
        project="trop",
    )
    paid_b = _obj(
        ULID_PAID_B,
        state="paid",
        tags=[],
        caps=["blog-draft"],
        outcome_score=0.8,
        artifact="https://example.test/b",
        description="content prompt B",
        project="trop",
    )
    paid_c = _obj(
        ULID_PAID_C,
        state="paid",
        tags=["role:social"],
        outcome_score=0.5,
        artifact="https://example.test/c",
        description="mediocre post",
        project="trop",
    )

    fake = _FakeClient(
        harvest_objs=[harvest],
        postmortem_objs=[],
        stored_by_id={
            paid_a.id: paid_a,
            paid_b.id: paid_b,
            paid_c.id: paid_c,
        },
    )
    _install_fake_client(monkeypatch, fake)

    _install_audit_events(
        monkeypatch,
        [
            {"id": paid_a.id, "new_state": "paid", "outcome_score": 0.95, "project": "trop"},
            {"id": paid_b.id, "new_state": "paid", "outcome_score": 0.8, "project": "trop"},
            {"id": paid_c.id, "new_state": "paid", "outcome_score": 0.5, "project": "trop"},
        ],
    )

    http_calls: list[dict[str, Any]] = []
    _install_fake_http(monkeypatch, calls=http_calls, status_code=200)

    agent = CuratorAgent(
        memory_url="http://mem.example:6061",
        memory_token="sys-xyz",
        project="trop",
    )
    written = await agent.run_once()

    # Two winners: A and B (C is below threshold).
    assert written == 2
    assert len(http_calls) == 2

    # Claim + complete + ack all happened exactly once for the harvest objective.
    assert [c["obj_id"] for c in fake.claim_calls] == [ULID_HARVEST]
    assert [c["obj_id"] for c in fake.complete_calls] == [ULID_HARVEST]
    assert [c["obj_id"] for c in fake.ack_calls] == [ULID_HARVEST]
    assert fake.ack_calls[0]["acker"] == "curator"

    # Memory calls carry the derived role + kind:winner tag for each winner.
    tags_list = [set(c["json"]["tags"]) for c in http_calls]
    # A -> role:social, B -> role:blog-draft (from capability fallback)
    assert {"role:social", "kind:winner", "project:trop"} in [t for t in tags_list]
    assert any("role:blog-draft" in t for t in tags_list)
    # All memory calls were POSTs to /memories with a bearer token.
    for call in http_calls:
        assert call["url"] == "http://mem.example:6061/memories"
        assert call["headers"]["Authorization"] == "Bearer sys-xyz"
        assert call["json"]["metadata"]["kind"] == "winner"


@pytest.mark.asyncio
async def test_run_once_handles_memory_write_failure_softly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the memory POST raises, curator still completes + acks — no wedge."""
    harvest = _obj(
        ULID_HARVEST,
        tags=["kind:harvest-winners", "project:trop"],
        project="trop",
    )
    paid_a = _obj(
        ULID_PAID_A,
        state="paid",
        tags=["role:social"],
        outcome_score=0.9,
        artifact="https://example.test/a",
        project="trop",
    )
    fake = _FakeClient(
        harvest_objs=[harvest],
        stored_by_id={paid_a.id: paid_a},
    )
    _install_fake_client(monkeypatch, fake)

    _install_audit_events(
        monkeypatch,
        [{"id": paid_a.id, "new_state": "paid", "outcome_score": 0.9, "project": "trop"}],
    )

    http_calls: list[dict[str, Any]] = []
    _install_fake_http(
        monkeypatch,
        calls=http_calls,
        raise_exc=RuntimeError("memory service down"),
    )

    agent = CuratorAgent(
        memory_url="http://mem.example:6061",
        memory_token="sys-xyz",
        project="trop",
    )

    written = await agent.run_once()

    # No winners successfully written — but the harvest objective is not
    # wedged: claim + complete + ack still ran.
    assert written == 0
    assert fake.claim_calls, "expected curator to still claim"
    assert fake.complete_calls, "expected curator to still complete"
    assert fake.ack_calls, "expected curator to still ack (preventing wedge)"
