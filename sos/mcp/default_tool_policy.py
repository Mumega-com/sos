"""Default MCP tool policy for the public kernel.

Hosted products can replace this empty catalogue through their own overlay.
"""

from __future__ import annotations

from typing import Any

CUSTOMER_TOOLS: list[dict[str, Any]] = []
IDENTITY_TOOLS: set[str] = set()
TOOL_MAPPING: dict[str, str] = {}
BLOCKED_TOOLS: set[str] = set()


def get_tools_for_role(role: str = "admin") -> list[dict[str, Any]]:
    return CUSTOMER_TOOLS


def get_tools_for_tier(tier: str, role: str = "admin") -> list[dict[str, Any]]:
    return get_tools_for_role(role)


def is_tool_allowed_for_role(tool_name: str, role: str = "admin") -> bool:
    return tool_name in TOOL_MAPPING


def is_tool_allowed_for_tier(tool_name: str, tier: str, role: str = "admin") -> bool:
    return is_tool_allowed_for_role(tool_name, role)
