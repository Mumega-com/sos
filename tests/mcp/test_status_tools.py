from __future__ import annotations

import sys
import types
from typing import Any

import pytest

_mirror_db_stub = types.ModuleType("mirror.kernel.db")
_mirror_db_stub.get_db = lambda: None
_mirror_embeddings_stub = types.ModuleType("mirror.kernel.embeddings")
_mirror_embeddings_stub.get_embedding = lambda text: []
sys.modules.setdefault("mirror.kernel.db", _mirror_db_stub)
sys.modules.setdefault("mirror.kernel.embeddings", _mirror_embeddings_stub)

from sos.mcp.sos_mcp_sse import MCPAuthContext, handle_tool  # noqa: E402
from sos.mcp.tools import status as status_tools  # noqa: E402


class _Redis:
    def __init__(self) -> None:
        self.scans: list[str | None] = []

    async def xrevrange(self, stream: str, count: int = 1) -> list[tuple[str, dict[str, str]]]:
        if stream == "sos:stream:global:agent:sol":
            return [("9999999999999-0", {})]
        return []

    async def scan(self, cursor: int, match: str | None = None, count: int = 100):
        self.scans.append(match)
        return 0, [
            "sos:stream:project:acme:agent:sol",
            "sos:stream:project:acme:agent:wake-daemon",
        ]


def test_render_status_contract_sections_and_icons() -> None:
    result = status_tools.render_status(
        agent_statuses=[
            {
                "agent": "sol",
                "model": "Claude",
                "role": "Content",
                "status": "active",
            }
        ],
        service_statuses=[{"service": "sos-mcp-sse", "status": "active"}],
        task_count_by_status={"queued": 2, "done": 1},
    )

    text = result["content"][0]["text"]
    assert "# SOS Status" in text
    assert "## Agents" in text
    assert "🟡 **sol** (Claude) — Content [active]" in text
    assert "## Services" in text
    assert "🟢 sos-mcp-sse: active" in text
    assert "## Tasks" in text
    assert "- queued: 2" in text


@pytest.mark.asyncio
async def test_project_agents_filters_internal_agents() -> None:
    redis = _Redis()

    agents = await status_tools.project_agents(
        redis,
        stream_prefix="sos:stream:project:acme",
    )

    assert agents == {"sol"}
    assert redis.scans == ["sos:stream:project:acme:agent:*"]


def test_task_counts_tolerates_missing_token() -> None:
    assert status_tools.task_counts(squad_service_url="http://squad", squad_system_token=None) == {}


def test_task_counts_preserves_status_counter_contract() -> None:
    class _Response:
        ok = True

        def json(self) -> list[dict[str, str]]:
            return [{"status": "queued"}, {"status": "queued"}, {"status": "done"}]

    def fake_get(*args: Any, **kwargs: Any) -> _Response:
        assert args[0] == "http://squad/tasks?limit=500"
        assert kwargs["headers"] == {"Authorization": "Bearer token"}
        return _Response()

    assert status_tools.task_counts(
        squad_service_url="http://squad",
        squad_system_token="token",
        requests_get=fake_get,
    ) == {"queued": 2, "done": 1}


@pytest.mark.asyncio
async def test_handle_tool_status_delegates_to_extracted_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sos.mcp import sos_mcp_sse as sse

    captured: dict[str, Any] = {}

    async def fake_handle_status_tool(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return status_tools.text_result("delegated status")

    async def noop_publish_log(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(sse, "_get_redis", lambda: _Redis())
    monkeypatch.setattr(sse, "_publish_log", noop_publish_log)
    monkeypatch.setattr(sse, "handle_status_tool", fake_handle_status_tool)

    auth = MCPAuthContext(
        token="sk-test",
        tenant_id="acme",
        is_system=False,
        agent_name="sol",
        scope="agent",
    )

    result = await handle_tool("status", {}, auth)

    assert result["content"][0]["text"] == "delegated status"
    assert captured["is_system"] is False
    assert captured["project_scope"] == "acme"
    assert captured["stream_prefix"] == "sos:stream:project:acme"
