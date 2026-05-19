"""Tests for sos.kernel.heartbeat.emit_card — v0.7.3.

Verifies the fail-soft POST wrapper's behaviour without exercising a
live registry. Every test monkeypatches ``httpx.post`` so the helper's
logic is under test, not network plumbing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from sos.contracts.agent_card import AgentCard
from sos.kernel import heartbeat


@dataclass
class _FakeResponse:
    status_code: int
    text: str = ""


def _card(name: str = "kasra", project: str | None = None) -> AgentCard:
    return AgentCard(
        identity_id=f"agent:{name}",
        name=name,
        role="executor",
        tool="claude-code",
        type="tmux",
        session=name,
        warm_policy="warm",
        cache_ttl_s=300,
        project=project,
        registered_at=AgentCard.now_iso(),
        last_seen=AgentCard.now_iso(),
    )


def test_emit_card_returns_false_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No token → no network call, return False."""
    monkeypatch.delenv("SOS_REGISTRY_TOKEN", raising=False)
    monkeypatch.delenv("SOS_SYSTEM_TOKEN", raising=False)

    called = {"posts": 0}

    def fake_post(*a: Any, **kw: Any) -> _FakeResponse:
        called["posts"] += 1
        return _FakeResponse(status_code=200)

    monkeypatch.setattr(heartbeat.httpx, "post", fake_post, raising=True)
    assert heartbeat.emit_card(_card()) is False
    assert called["posts"] == 0


def test_emit_card_success_returns_true(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kw: Any) -> _FakeResponse:
        captured["url"] = url
        captured["json"] = kw.get("json")
        captured["params"] = kw.get("params")
        captured["headers"] = kw.get("headers")
        return _FakeResponse(status_code=200)

    monkeypatch.setattr(heartbeat.httpx, "post", fake_post, raising=True)

    ok = heartbeat.emit_card(
        _card(),
        ttl_seconds=120,
        base_url="http://registry.example:6067",
        token="sys-abc",
    )
    assert ok is True
    assert captured["url"] == "http://registry.example:6067/agents/cards"
    assert captured["headers"]["Authorization"] == "Bearer sys-abc"
    assert captured["params"] == {"ttl_seconds": 120}
    # Card payload round-trips as JSON-friendly dict.
    assert captured["json"]["name"] == "kasra"
    assert captured["json"]["identity_id"] == "agent:kasra"


def test_emit_card_adds_project_query_from_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kw: Any) -> _FakeResponse:
        captured["params"] = kw.get("params")
        return _FakeResponse(status_code=200)

    monkeypatch.setattr(heartbeat.httpx, "post", fake_post, raising=True)

    ok = heartbeat.emit_card(
        _card(name="viamar-agent", project="viamar"),
        token="sys-abc",
    )
    assert ok is True
    assert captured["params"]["project"] == "viamar"


def test_emit_card_explicit_project_wins_over_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kw: Any) -> _FakeResponse:
        captured["params"] = kw.get("params")
        return _FakeResponse(status_code=200)

    monkeypatch.setattr(heartbeat.httpx, "post", fake_post, raising=True)

    heartbeat.emit_card(
        _card(project="viamar"),
        project="mumega",
        token="sys-abc",
    )
    assert captured["params"]["project"] == "mumega"


def test_emit_card_non_2xx_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, **kw: Any) -> _FakeResponse:
        return _FakeResponse(status_code=403, text="forbidden")

    monkeypatch.setattr(heartbeat.httpx, "post", fake_post, raising=True)
    assert (
        heartbeat.emit_card(_card(), token="sys-abc") is False
    )


def test_emit_card_exception_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Connection refused, DNS fail, timeouts — all fail-soft."""

    def fake_post(url: str, **kw: Any) -> _FakeResponse:
        raise RuntimeError("registry unreachable")

    monkeypatch.setattr(heartbeat.httpx, "post", fake_post, raising=True)
    assert heartbeat.emit_card(_card(), token="sys-abc") is False
