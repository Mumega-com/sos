"""Compatibility definitions for customer-scoped MCP tool gating.

Public SOS exposes generic public tools in ``sos.mcp.public_tools``. The MCP
server still imports the historical ``customer_tools`` module name, so this
module adapts public definitions without reintroducing private Mumega product
tooling.
"""
from __future__ import annotations

from sos.mcp.public_tools import PUBLIC_TOOLS, get_public_tools_for_tier

CUSTOMER_TOOLS: list[dict] = PUBLIC_TOOLS
TOOL_MAPPING: dict[str, str] = {tool["name"]: tool["name"] for tool in CUSTOMER_TOOLS}
IDENTITY_TOOLS: set[str] = set()

BLOCKED_TOOLS: set[str] = {
    "send",
    "broadcast",
    "inbox",
    "peers",
    "ask",
    "onboard",
    "status",
    "search_code",
    "task_board",
    "task_update",
    "memories",
    "request",
}


def is_customer_tool(name: str) -> bool:
    return name in TOOL_MAPPING


def get_tools_for_role(role: str = "admin") -> list[dict]:
    _ = role
    return CUSTOMER_TOOLS


def is_tool_allowed_for_role(tool_name: str, role: str = "admin") -> bool:
    _ = role
    return is_customer_tool(tool_name)


def get_tools_for_tier(tier: str, role: str = "admin") -> list[dict]:
    _ = role
    return get_public_tools_for_tier(tier)


def is_tool_allowed_for_tier(tool_name: str, tier: str, role: str = "admin") -> bool:
    return any(tool["name"] == tool_name for tool in get_tools_for_tier(tier, role))
