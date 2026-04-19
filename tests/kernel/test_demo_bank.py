"""Tests for sos.kernel.demo_bank — the RAG retrieval shim used by agents.

The helpers are purely HTTP + pure-Python string work; we monkeypatch
``httpx.AsyncClient`` to exercise every behaviour without hitting the wire.
"""
from __future__ import annotations

from typing import Any

import pytest

from sos.kernel import demo_bank


# ---------------------------------------------------------------------------
# Httpx test doubles
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, json_body: Any = None) -> None:
        self.status_code = status_code
        self._json = json_body if json_body is not None else {"results": []}
        self.text = ""

    def json(self) -> Any:
        return self._json


class _FakeAsyncClient:
    """Minimal async-context-manager stand-in for ``httpx.AsyncClient``.

    The constructor is monkeypatched over ``httpx.AsyncClient`` so that every
    ``async with httpx.AsyncClient(...) as c`` call in demo_bank routes
    through this fake instead.  The captured calls let the tests inspect
    what params / headers / URL were sent.
    """

    captured: dict[str, Any] = {}

    def __init__(self, *, response: _FakeResponse, raise_exc: Exception | None = None) -> None:
        self._response = response
        self._raise = raise_exc

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        _FakeAsyncClient.captured["url"] = url
        _FakeAsyncClient.captured["params"] = kwargs.get("params")
        _FakeAsyncClient.captured["headers"] = kwargs.get("headers")
        if self._raise is not None:
            raise self._raise
        return self._response


def _install_fake_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    response: _FakeResponse | None = None,
    raise_exc: Exception | None = None,
) -> None:
    _FakeAsyncClient.captured = {}
    resp = response or _FakeResponse()

    def factory(*_args: Any, **_kwargs: Any) -> _FakeAsyncClient:
        return _FakeAsyncClient(response=resp, raise_exc=raise_exc)

    monkeypatch.setattr(demo_bank.httpx, "AsyncClient", factory, raising=True)


# ---------------------------------------------------------------------------
# fetch_winners
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_winners_returns_empty_on_memory_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Network failure → ``[]`` with no raise."""
    monkeypatch.setenv("SOS_SYSTEM_TOKEN", "sys-xyz")
    _install_fake_client(monkeypatch, raise_exc=RuntimeError("connection reset"))

    result = await demo_bank.fetch_winners("social", n=5)
    assert result == []


@pytest.mark.asyncio
async def test_fetch_winners_filters_by_role_and_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Role + project flow through as ``role:`` / ``project:`` tag params."""
    monkeypatch.setenv("SOS_SYSTEM_TOKEN", "sys-xyz")
    _install_fake_client(
        monkeypatch,
        response=_FakeResponse(json_body={"results": []}),
    )

    await demo_bank.fetch_winners(
        "social",
        n=3,
        project="trop",
        memory_base_url="http://mem.example:6061",
        token="sys-xyz",
    )

    captured = _FakeAsyncClient.captured
    assert captured["url"] == "http://mem.example:6061/search"

    params = captured["params"]
    tags = params["tags"]
    assert "role:social" in tags
    assert "kind:winner" in tags
    assert "project:trop" in tags
    assert params["limit"] == 3

    headers = captured["headers"]
    assert headers["Authorization"] == "Bearer sys-xyz"


@pytest.mark.asyncio
async def test_fetch_winners_orders_by_score_desc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Memory returns unsorted; fetch_winners re-sorts by score descending."""
    monkeypatch.setenv("SOS_SYSTEM_TOKEN", "sys-xyz")
    unsorted = {
        "results": [
            {"artifact": "mid", "metadata": {"outcome_score": 0.6}},
            {"artifact": "top", "metadata": {"outcome_score": 0.95}},
            {"artifact": "low", "metadata": {"outcome_score": 0.1}},
        ]
    }
    _install_fake_client(
        monkeypatch,
        response=_FakeResponse(json_body=unsorted),
    )

    result = await demo_bank.fetch_winners("content", n=10)
    assert [r["artifact"] for r in result] == ["top", "mid", "low"]


# ---------------------------------------------------------------------------
# build_few_shot_prompt
# ---------------------------------------------------------------------------


def test_build_few_shot_prompt_appends_winners() -> None:
    winners = [
        {"artifact": "A-artifact", "prompt": "A-prompt", "outcome_score": 0.9},
        {"artifact": "B-artifact", "prompt": "B-prompt", "outcome_score": 0.8},
        {"artifact": "C-artifact", "prompt": "C-prompt", "outcome_score": 0.7},
    ]
    base = "You are trop-social. Write today's post."

    out = demo_bank.build_few_shot_prompt(base, winners)

    assert base in out
    assert "Examples of past high-quality outputs:" in out
    assert "A-artifact" in out
    assert "B-artifact" in out
    assert "C-artifact" in out
    # Score formatting flows through too.
    assert "0.90" in out


def test_build_few_shot_prompt_respects_max_chars() -> None:
    """100 winners with large artifacts get trimmed at max_chars."""
    winners = [
        {
            "artifact": f"artifact-{i}-" + ("x" * 200),
            "prompt": f"prompt-{i}-" + ("y" * 200),
            "outcome_score": 0.5,
        }
        for i in range(100)
    ]
    out = demo_bank.build_few_shot_prompt(
        "BASE PROMPT",
        winners,
        max_chars=500,
    )
    assert len(out) <= 500
    assert out.startswith("BASE PROMPT")


def test_build_few_shot_prompt_empty_winners_returns_base() -> None:
    """No winners → base prompt unchanged (still trimmed to max_chars)."""
    base = "hello world"
    assert demo_bank.build_few_shot_prompt(base, []) == base
