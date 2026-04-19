"""Tests for sos.kernel.objectives — v0.8.0.

Verifies the fail-soft POST wrappers' behaviour without exercising a
live objectives service.  Every test monkeypatches ``httpx.post`` so
the helper's logic is under test, not network plumbing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from sos.kernel import objectives


@dataclass
class _FakeResponse:
    status_code: int
    text: str = ""


# ---------------------------------------------------------------------------
# claim
# ---------------------------------------------------------------------------


def test_claim_no_token_no_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """No token → no network call, return False."""
    monkeypatch.delenv("SOS_OBJECTIVES_TOKEN", raising=False)
    monkeypatch.delenv("SOS_SYSTEM_TOKEN", raising=False)

    called = {"posts": 0}

    def fake_post(*a: Any, **kw: Any) -> _FakeResponse:
        called["posts"] += 1
        return _FakeResponse(status_code=200)

    monkeypatch.setattr(objectives.httpx, "post", fake_post, raising=True)
    assert objectives.claim("obj-1") is False
    assert called["posts"] == 0


def test_claim_success_returns_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """200 → True; verify URL, Authorization header, body."""
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kw: Any) -> _FakeResponse:
        captured["url"] = url
        captured["json"] = kw.get("json")
        captured["headers"] = kw.get("headers")
        return _FakeResponse(status_code=200)

    monkeypatch.setattr(objectives.httpx, "post", fake_post, raising=True)

    ok = objectives.claim(
        "obj-42",
        base_url="http://objectives.example:6068",
        token="tok-abc",
    )
    assert ok is True
    assert captured["url"] == "http://objectives.example:6068/objectives/obj-42/claim"
    assert captured["headers"]["Authorization"] == "Bearer tok-abc"
    assert captured["json"] == {}


def test_claim_with_agent_param_passes_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """agent='kasra' appears in the JSON body."""
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kw: Any) -> _FakeResponse:
        captured["json"] = kw.get("json")
        return _FakeResponse(status_code=200)

    monkeypatch.setattr(objectives.httpx, "post", fake_post, raising=True)

    objectives.claim("obj-7", agent="kasra", token="tok-abc")
    assert captured["json"] == {"agent": "kasra"}


def test_claim_non_2xx_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """409 Conflict (already claimed) → False."""

    def fake_post(url: str, **kw: Any) -> _FakeResponse:
        return _FakeResponse(status_code=409, text="already claimed")

    monkeypatch.setattr(objectives.httpx, "post", fake_post, raising=True)
    assert objectives.claim("obj-1", token="tok-abc") is False


def test_claim_exception_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Connection errors, timeouts — all fail-soft."""

    def fake_post(url: str, **kw: Any) -> _FakeResponse:
        raise RuntimeError("objectives service unreachable")

    monkeypatch.setattr(objectives.httpx, "post", fake_post, raising=True)
    assert objectives.claim("obj-1", token="tok-abc") is False


# ---------------------------------------------------------------------------
# heartbeat_objective
# ---------------------------------------------------------------------------


def test_heartbeat_success_returns_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """200 → True; verify URL ends with /heartbeat."""
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kw: Any) -> _FakeResponse:
        captured["url"] = url
        return _FakeResponse(status_code=200)

    monkeypatch.setattr(objectives.httpx, "post", fake_post, raising=True)

    ok = objectives.heartbeat_objective(
        "obj-99",
        base_url="http://objectives.example:6068",
        token="tok-abc",
    )
    assert ok is True
    assert captured["url"] == "http://objectives.example:6068/objectives/obj-99/heartbeat"


def test_heartbeat_no_token_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """No token → return False, no network call."""
    monkeypatch.delenv("SOS_OBJECTIVES_TOKEN", raising=False)
    monkeypatch.delenv("SOS_SYSTEM_TOKEN", raising=False)

    called = {"posts": 0}

    def fake_post(*a: Any, **kw: Any) -> _FakeResponse:
        called["posts"] += 1
        return _FakeResponse(status_code=200)

    monkeypatch.setattr(objectives.httpx, "post", fake_post, raising=True)
    assert objectives.heartbeat_objective("obj-99") is False
    assert called["posts"] == 0


# ---------------------------------------------------------------------------
# release
# ---------------------------------------------------------------------------


def test_release_success_returns_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """200 → True; verify URL ends with /release."""
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kw: Any) -> _FakeResponse:
        captured["url"] = url
        return _FakeResponse(status_code=200)

    monkeypatch.setattr(objectives.httpx, "post", fake_post, raising=True)

    ok = objectives.release(
        "obj-5",
        base_url="http://objectives.example:6068",
        token="tok-abc",
    )
    assert ok is True
    assert captured["url"] == "http://objectives.example:6068/objectives/obj-5/release"


# ---------------------------------------------------------------------------
# Token fallback
# ---------------------------------------------------------------------------


def test_uses_system_token_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """SOS_OBJECTIVES_TOKEN unset, SOS_SYSTEM_TOKEN set → uses SYSTEM_TOKEN."""
    monkeypatch.delenv("SOS_OBJECTIVES_TOKEN", raising=False)
    monkeypatch.setenv("SOS_SYSTEM_TOKEN", "sys-fallback-token")

    captured: dict[str, Any] = {}

    def fake_post(url: str, **kw: Any) -> _FakeResponse:
        captured["headers"] = kw.get("headers")
        return _FakeResponse(status_code=200)

    monkeypatch.setattr(objectives.httpx, "post", fake_post, raising=True)

    ok = objectives.claim("obj-fallback")
    assert ok is True
    assert captured["headers"]["Authorization"] == "Bearer sys-fallback-token"
