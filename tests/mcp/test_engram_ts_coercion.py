"""Tests for _fmt_engram_ts — engram timestamp coercion helper (issue #190).

Root cause: mirror's direct-DB path returns native ``datetime.datetime``
objects; the HTTP-fallback path returns JSON strings.  Before this fix,
all three formatting sites did ``e.get('timestamp', '?')[:10]`` which
crashes with ``'datetime.datetime' object is not subscriptable`` on the
direct-DB code path.

These tests verify:
  1. The helper itself handles datetime objects, date objects, strings, and
     fallback sentinel ``"?"`` correctly.
  2. The ``memories`` handler (tool name ``"memories"``) formats engrams
     without raising when ``_mirror_db.recent_engrams()`` returns rows with
     native datetime timestamps.
  3. The ``recall`` handler formats rows without raising when rows carry
     datetime-typed ``"ts"`` values (via search_engrams / mirror_match_engrams_v2).
"""
from __future__ import annotations

import datetime
import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Mirror stubs — mirror modules are not on the test path.
# ---------------------------------------------------------------------------
_mirror_db_stub = types.ModuleType("mirror.kernel.db")
_mirror_db_stub.get_db = lambda: None  # type: ignore[attr-defined]
_mirror_embeddings_stub = types.ModuleType("mirror.kernel.embeddings")
_mirror_embeddings_stub.get_embedding = lambda text: []  # type: ignore[attr-defined]
sys.modules.setdefault("mirror.kernel.db", _mirror_db_stub)
sys.modules.setdefault("mirror.kernel.embeddings", _mirror_embeddings_stub)

from sos.mcp.sos_mcp_sse import MCPAuthContext, _fmt_engram_ts  # noqa: E402


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _auth(permissions: list[str] | None = None) -> MCPAuthContext:
    return MCPAuthContext(
        token="s" * 64,
        tenant_id="sos",
        is_system=False,
        source="test",
        agent_name="kasra",
        scope="",
        permissions=permissions or ["memories", "recall", "search", "squad_recall"],
    )


# ---------------------------------------------------------------------------
# Unit tests for _fmt_engram_ts (sync — no asyncio mark needed)
# ---------------------------------------------------------------------------

class TestFmtEngramTs:
    def test_datetime_returns_date_prefix(self) -> None:
        dt = datetime.datetime(2026, 6, 12, 14, 30, 0)
        assert _fmt_engram_ts(dt) == "2026-06-12"

    def test_date_object_returns_date_prefix(self) -> None:
        d = datetime.date(2026, 6, 12)
        assert _fmt_engram_ts(d) == "2026-06-12"

    def test_string_iso_returns_date_prefix(self) -> None:
        assert _fmt_engram_ts("2026-06-12T14:30:00Z") == "2026-06-12"

    def test_plain_date_string_unchanged(self) -> None:
        assert _fmt_engram_ts("2026-06-12") == "2026-06-12"

    def test_sentinel_question_mark_returns_question_mark(self) -> None:
        assert _fmt_engram_ts("?") == "?"

    def test_none_like_string(self) -> None:
        # str("None")[:10] == "None" — no crash
        result = _fmt_engram_ts("None")
        assert isinstance(result, str)

    def test_integer_ts_does_not_raise(self) -> None:
        # epoch seconds as int — should not crash, returns str prefix
        result = _fmt_engram_ts(1718200000)
        assert isinstance(result, str)

    def test_datetime_with_timezone(self) -> None:
        dt = datetime.datetime(2026, 6, 12, 14, 30, 0, tzinfo=datetime.timezone.utc)
        assert _fmt_engram_ts(dt) == "2026-06-12"


# ---------------------------------------------------------------------------
# Integration: memories handler with datetime-typed timestamps (issue #190)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_memories_handler_datetime_timestamp_does_not_crash(monkeypatch: Any) -> None:
    """_mirror_db.recent_engrams() returns rows with datetime objects — must not raise."""
    from sos.mcp import sos_mcp_sse as module

    # Build a fake _mirror_db whose recent_engrams returns native datetime rows.
    fake_db = MagicMock()
    fake_db.recent_engrams.return_value = [
        {
            "raw_data": {"text": "hello world"},
            "context_id": "ctx:1",
            "timestamp": datetime.datetime(2026, 6, 12, 10, 0, 0),
        },
        {
            "raw_data": {"text": "second memory"},
            "context_id": "ctx:2",
            "timestamp": datetime.datetime(2026, 1, 1, 0, 0, 0),
        },
    ]

    monkeypatch.setattr(module, "_mirror_db", fake_db)
    fake_scope = MagicMock()
    fake_scope.agent = "kasra"
    fake_scope.project = "sos"
    fake_scope.mirror_project = "sos"
    fake_scope.workspace_id = "ws-test"
    monkeypatch.setattr(module, "_memory_scope", lambda auth: fake_scope)
    monkeypatch.setattr(module, "_audit_tool_call", lambda *a, **kw: None)
    monkeypatch.setattr(module, "_append_audit", lambda *a, **kw: None)

    result = await module._process_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "memories", "arguments": {"limit": 5}},
        },
        session_id=None,
        auth=_auth(),
    )

    # Must not raise; result must contain formatted lines with date prefix.
    content = result.get("result", {}).get("content", [{}])[0].get("text", "")
    assert "2026-06-12" in content
    assert "hello world" in content


# ---------------------------------------------------------------------------
# Integration: recall handler with datetime-typed "ts" field (issue #190)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recall_handler_datetime_ts_does_not_crash(monkeypatch: Any) -> None:
    """recall tool calls search_engrams() which returns rows with 'ts' as datetime.

    mirror_match_engrams_v2 returns: context_id, series, raw_data, ts, similarity.
    The 'ts' column is a native datetime from the direct-DB path — must not raise.
    """
    from sos.mcp import sos_mcp_sse as module

    fake_db = MagicMock()
    fake_db.search_engrams.return_value = [
        {
            "raw_data": {"text": "a recalled result"},
            "context_id": "ctx:99",
            "ts": datetime.datetime(2026, 5, 1, 8, 0, 0),
            "similarity": 0.9,
        },
    ]

    monkeypatch.setattr(module, "_mirror_db", fake_db)
    fake_scope = MagicMock()
    fake_scope.agent = "kasra"
    fake_scope.project = "sos"
    fake_scope.mirror_project = "sos"
    fake_scope.workspace_id = "ws-test"
    monkeypatch.setattr(module, "_memory_scope", lambda auth: fake_scope)
    monkeypatch.setattr(module, "_audit_tool_call", lambda *a, **kw: None)
    monkeypatch.setattr(module, "_append_audit", lambda *a, **kw: None)
    # Stub the embedding call so it doesn't attempt a real model call.
    monkeypatch.setattr(module, "_get_mirror_embedding", lambda text: [0.1] * 4)

    result = await module._process_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "recall", "arguments": {"query": "result", "limit": 5}},
        },
        session_id=None,
        auth=_auth(),
    )

    content = result.get("result", {}).get("content", [{}])[0].get("text", "")
    assert "2026-05-01" in content
    assert "a recalled result" in content
