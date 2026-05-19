"""Integration tests for the ``code_mode`` MCP tool.

Targets the standalone ``_handle_code_mode`` helper (plus ``get_tools`` for
the registration check). ``handle_tool`` itself touches Redis via
``_get_redis``/``_publish_log`` in its preamble, so these tests exercise
the helper directly — which is the wiring the MCP server actually calls
for the ``code_mode`` branch. All runtime-level auth/redis state is
bypassed via a minimal ``MCPAuthContext`` stub.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from sos.mcp.sos_mcp_sse import (
    MCPAuthContext,
    _handle_code_mode,
    get_tools,
)


pytestmark = pytest.mark.asyncio


def _fake_auth() -> MCPAuthContext:
    # is_system=True so any downstream capability gates are no-ops; token is
    # a hash-looking string because MCPAuthContext stores hashes, never raw.
    return MCPAuthContext(
        token="test" * 16,
        tenant_id=None,
        is_system=True,
        source="test",
        agent_name="test-agent",
    )


def _parse_payload(result: dict[str, Any]) -> dict[str, Any]:
    assert "content" in result
    assert result["content"]
    block = result["content"][0]
    assert block["type"] == "text"
    return json.loads(block["text"])


async def test_code_mode_tool_registered_in_get_tools() -> None:
    tools = get_tools()
    matches = [t for t in tools if t.get("name") == "code_mode"]
    assert len(matches) == 1, "code_mode tool must be registered exactly once"
    schema = matches[0]["inputSchema"]
    assert "code" in schema.get("required", []), "code must be a required input"
    assert "code" in schema["properties"]
    assert schema["properties"]["code"]["type"] == "string"


async def test_code_mode_handler_executes_trivial_snippet() -> None:
    result = await _handle_code_mode({"code": "1 + 2"}, _fake_auth())
    payload = _parse_payload(result)
    assert payload["value"] == "3"  # repr(3)
    assert payload["stderr"] == ""


async def test_code_mode_handler_rejects_empty_code() -> None:
    result = await _handle_code_mode({"code": ""}, _fake_auth())
    assert result["content"][0]["text"] == "error: empty code"

    # Whitespace-only should also be rejected.
    result2 = await _handle_code_mode({"code": "   \n\t "}, _fake_auth())
    assert result2["content"][0]["text"] == "error: empty code"


async def test_code_mode_handler_respects_timeout() -> None:
    result = await _handle_code_mode(
        {"code": "while True:\n    pass", "timeout_s": 0.3},
        _fake_auth(),
    )
    payload = _parse_payload(result)
    assert "Timeout" in payload["stderr"]
    assert payload["value"] == "None"  # repr(None)


async def test_code_mode_cant_import_os() -> None:
    result = await _handle_code_mode(
        {"code": "import os\nos.getcwd()"},
        _fake_auth(),
    )
    payload = _parse_payload(result)
    # Restricted __builtins__ has no __import__, so the import itself raises.
    assert "ImportError" in payload["stderr"] or "not defined" in payload["stderr"]
    assert payload["value"] == "None"


async def test_code_mode_surfaces_stdout_capture() -> None:
    result = await _handle_code_mode(
        {"code": "print(42)"},
        _fake_auth(),
    )
    payload = _parse_payload(result)
    assert "42" in payload["stdout"]
