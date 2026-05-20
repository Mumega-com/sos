from __future__ import annotations

import importlib


def test_public_mcp_customer_tools_adapter_imports() -> None:
    tools = importlib.import_module("sos.mcp.customer_tools")

    assert tools.is_customer_tool("observe_pipeline")
    assert not tools.is_customer_tool("send")
    assert tools.is_tool_allowed_for_tier("observe_pipeline", "free")
    assert not tools.is_tool_allowed_for_tier("mint_knight", "free")


def test_public_information_event_imports() -> None:
    events = importlib.import_module("sos.kernel.information_event")

    event = events.InformationEvent(
        event_id="evt-1",
        project="demo",
        tenant="tenant",
        actor="tester",
        event_type="demo",
        summary=events.safe_summary("hello"),
    )

    assert events.project_event_stream("demo") == "sos:stream:project:demo:events"
    assert event.to_redis_fields()["payload"] == "{}"


def test_public_squad_event_router_imports() -> None:
    router = importlib.import_module("sos.services.squad.event_router")

    assert callable(router.route_task_event)
    assert callable(router.project_event_to_bus)


def test_public_mcp_server_imports_without_optional_private_modules() -> None:
    mcp = importlib.import_module("sos.mcp.sos_mcp_sse")

    assert mcp.SproutTenantEngine is None or callable(mcp.SproutTenantEngine)
    assert hasattr(mcp, "_mirror_db")
