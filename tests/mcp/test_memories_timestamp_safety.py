"""Regression tests for issue #190: datetime.datetime object is not subscriptable.

The ``memories``, ``squad_recall``, and ``recall`` MCP handlers format
engram timestamps with a ``[:10]`` slice. When the direct Mirror DB
path returns native ``datetime.datetime`` objects (psycopg / postgrest),
that slice raises ``TypeError``. The HTTP fallback returns JSON
strings, which is why the bug only surfaced on the direct-DB path.

These tests pin the behavior so any future re-introduction is caught.
"""

from __future__ import annotations

import asyncio
import datetime
import sys
import types
from typing import Any

import pytest


def _patch_mirror_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub heavy modules so the MCP handler imports in isolation."""
    mirror_db = types.ModuleType("mirror.kernel.db")
    mirror_db.get_db = lambda: None
    monkeypatch.setitem(sys.modules, "mirror.kernel.db", mirror_db)

    mirror_emb = types.ModuleType("mirror.kernel.embeddings")
    mirror_emb.get_embedding = lambda text: []
    monkeypatch.setitem(sys.modules, "mirror.kernel.embeddings", mirror_emb)


def _make_auth() -> Any:
    """Construct an MCPAuthContext with the scopes required for memory reads."""
    from sos.mcp.sos_mcp_sse import MCPAuthContext

    return MCPAuthContext(
        token="test-token",
        tenant_id="tenant-test",
    )


def _call_handle_tool(name: str, args: dict[str, Any], auth: Any) -> str:
    """Invoke ``handle_tool`` synchronously and return its text payload.

    ``handle_tool`` returns an MCP-shaped dict
    (``{"content": [{"text": ..., "type": "text"}]}``). Tests only care
    about the rendered text, so unwrap it here.
    """
    from sos.mcp.sos_mcp_sse import handle_tool

    result = asyncio.run(handle_tool(name=name, args=args, auth=auth))
    content = result.get("content") if isinstance(result, dict) else None
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and isinstance(first.get("text"), str):
            return first["text"]
    return str(result)


def test_memories_handler_accepts_datetime_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduces issue #190: direct-DB path returns datetime objects."""
    _patch_mirror_stubs(monkeypatch)

    fake_db = types.SimpleNamespace()
    fake_db.recent_engrams = lambda **_: [
        {
            "timestamp": datetime.datetime(
                2026, 6, 12, 23, 0, 0, tzinfo=datetime.timezone.utc,
            ),
            "raw_data": {"text": "datetime timestamp path"},
            "context_id": "ctx-dt",
        }
    ]
    monkeypatch.setattr("sos.mcp.sos_mcp_sse._mirror_db", fake_db, raising=False)

    text = _call_handle_tool(
        "memories",
        {"limit": 5},
        _make_auth(),
    )
    assert "datetime timestamp path" in text
    assert "2026-06-12" in text


def test_memories_handler_accepts_string_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The HTTP-fallback path returns ISO strings — make sure it still works."""
    _patch_mirror_stubs(monkeypatch)

    fake_db = types.SimpleNamespace()
    fake_db.recent_engrams = lambda **_: [
        {
            "timestamp": "2026-06-12T23:00:00Z",
            "raw_data": {"text": "string timestamp path"},
            "context_id": "ctx-str",
        }
    ]
    monkeypatch.setattr("sos.mcp.sos_mcp_sse._mirror_db", fake_db, raising=False)

    text = _call_handle_tool(
        "memories",
        {"limit": 5},
        _make_auth(),
    )
    assert "string timestamp path" in text
    assert "2026-06-12" in text


def test_squad_recall_handler_accepts_datetime_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """squad_recall mirrors the same ``[:10]`` slice hazard."""
    _patch_mirror_stubs(monkeypatch)

    fake_response = [
        {
            "timestamp": datetime.datetime(
                2026, 6, 12, 23, 0, 0, tzinfo=datetime.timezone.utc,
            ),
            "raw_data": {"text": "squad memory"},
            "context_id": "ctx-squad",
        }
    ]
    monkeypatch.setattr(
        "sos.mcp.sos_mcp_sse.mirror_post",
        lambda *args, **kwargs: fake_response,
        raising=False,
    )

    text = _call_handle_tool(
        "squad_recall",
        {"squad_id": "s1", "query": "q", "limit": 5},
        _make_auth(),
    )
    assert "squad memory" in text
    assert "2026-06-12" in text


def test_recall_handler_accepts_datetime_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """recall (semantic search) has the same ``ts[:10]`` slice hazard."""
    _patch_mirror_stubs(monkeypatch)

    fake_db = types.SimpleNamespace()
    fake_db.search_engrams = lambda **_: [
        {
            "ts": datetime.datetime(
                2026, 6, 12, 23, 0, 0, tzinfo=datetime.timezone.utc,
            ),
            "raw_data": {"text": "recall hit"},
            "context_id": "ctx-recall",
            "similarity": 0.91,
        }
    ]
    monkeypatch.setattr("sos.mcp.sos_mcp_sse._mirror_db", fake_db, raising=False)
    monkeypatch.setattr(
        "sos.mcp.sos_mcp_sse._get_mirror_embedding",
        lambda text: [0.0],
        raising=False,
    )

    text = _call_handle_tool(
        "recall",
        {"query": "anything", "limit": 5},
        _make_auth(),
    )
    assert "recall hit" in text
    assert "2026-06-12" in text
