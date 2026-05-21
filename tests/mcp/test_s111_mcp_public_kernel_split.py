from __future__ import annotations

import asyncio
from pathlib import Path

from sos.mcp import public_tools, tool_registry, transport


def test_transport_boundary_is_hosted_product_clean() -> None:
    repo = Path(__file__).resolve().parents[2]
    text = (repo / "sos" / "mcp" / "transport.py").read_text().lower()
    forbidden = (
        "mumega",
        "customer",
        "tenant",
        "mcp.mumega.com",
        "app.mumega.com",
        "stripe",
        "marketplace",
        "inkwell",
    )
    for term in forbidden:
        assert term not in text


def test_public_mcp_modules_import_without_hosted_dependencies() -> None:
    assert transport.jsonrpc_ok(1, {}) == {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert public_tools.PUBLIC_TOOLS == tool_registry.public_protocol_tools()


def test_public_registry_lists_kernel_primitives_only() -> None:
    registry = tool_registry.default_public_registry()

    assert {tool["name"] for tool in registry.list_tools()} == {
        "send",
        "inbox",
        "broadcast",
        "recall",
        "status",
        "peers",
    }


def test_public_registry_dispatches_mounted_handler() -> None:
    async def _send(args: dict) -> dict:
        return {"ok": True, "to": args["to"]}

    registry = tool_registry.default_public_registry({"send": _send})
    result = asyncio.run(registry.dispatch("send", {"to": "agent-a", "text": "hello"}))

    assert result == {"ok": True, "to": "agent-a"}


def test_public_registry_fails_closed_for_unmounted_overlay_tool() -> None:
    registry = tool_registry.default_public_registry()

    try:
        asyncio.run(registry.dispatch("hosted_overlay_tool", {}))
    except tool_registry.ToolUnavailableError as exc:
        assert "not registered" in str(exc)
    else:
        raise AssertionError("unregistered tools must fail closed")


def test_generic_transport_processes_fake_tool_registry() -> None:
    async def _status(args: dict) -> dict:
        return {"content": [{"type": "text", "text": "ok"}]}

    registry = tool_registry.default_public_registry({"status": _status})
    response = asyncio.run(transport.process_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "status", "arguments": {}},
        },
        registry,
    ))

    assert response == {
        "jsonrpc": "2.0",
        "id": 7,
        "result": {"content": [{"type": "text", "text": "ok"}]},
    }
