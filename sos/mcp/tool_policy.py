"""Pluggable MCP tool policy.

The public SOS kernel ships no hosted-product tool catalogue. Operators can
provide one by setting ``SOS_MCP_TOOL_POLICY_MODULE`` to a module exporting the
same symbols as this file.
"""

from __future__ import annotations

import importlib
import os
from typing import Any

_overlay_name = os.environ.get("SOS_MCP_TOOL_POLICY_MODULE", "sos.mcp.default_tool_policy")
try:
    _overlay_module = importlib.import_module(_overlay_name)
except Exception:
    _overlay_module = importlib.import_module("sos.mcp.default_tool_policy")

CUSTOMER_TOOLS: list[dict[str, Any]] = list(getattr(_overlay_module, "CUSTOMER_TOOLS", []))
IDENTITY_TOOLS: set[str] = set(getattr(_overlay_module, "IDENTITY_TOOLS", set()))
TOOL_MAPPING: dict[str, str] = dict(getattr(_overlay_module, "TOOL_MAPPING", {}))
BLOCKED_TOOLS: set[str] = set(getattr(_overlay_module, "BLOCKED_TOOLS", set()))

_get_tools_for_role = getattr(_overlay_module, "get_tools_for_role", None)
_get_tools_for_tier = getattr(_overlay_module, "get_tools_for_tier", None)
_is_tool_allowed_for_role = getattr(_overlay_module, "is_tool_allowed_for_role", None)
_is_tool_allowed_for_tier = getattr(_overlay_module, "is_tool_allowed_for_tier", None)


def get_tools_for_role(role: str = "admin") -> list[dict[str, Any]]:
    if callable(_get_tools_for_role):
        return list(_get_tools_for_role(role))
    return CUSTOMER_TOOLS


def get_tools_for_tier(tier: str, role: str = "admin") -> list[dict[str, Any]]:
    if callable(_get_tools_for_tier):
        return list(_get_tools_for_tier(tier, role))
    return get_tools_for_role(role)


def is_customer_tool(name: str) -> bool:
    return name in TOOL_MAPPING


def is_tool_allowed_for_role(tool_name: str, role: str = "admin") -> bool:
    if callable(_is_tool_allowed_for_role):
        return bool(_is_tool_allowed_for_role(tool_name, role))
    return is_customer_tool(tool_name)


def is_tool_allowed_for_tier(tool_name: str, tier: str, role: str = "admin") -> bool:
    if callable(_is_tool_allowed_for_tier):
        return bool(_is_tool_allowed_for_tier(tool_name, tier, role))
    return is_tool_allowed_for_role(tool_name, role)
