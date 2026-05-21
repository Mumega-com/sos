"""Compatibility exports for the public SOS MCP tool surface."""
from __future__ import annotations

from sos.mcp.tool_registry import PUBLIC_PROTOCOL_TOOL_NAMES, public_protocol_tools


PUBLIC_TOOLS: list[dict] = public_protocol_tools()
FREE_TIER_TOOLS = PUBLIC_PROTOCOL_TOOL_NAMES
PAID_TIER_TOOLS: frozenset[str] = frozenset()
ALL_PUBLIC_TOOLS = PUBLIC_PROTOCOL_TOOL_NAMES
UPGRADE_PROMPTS: dict[str, str] = {}


def get_public_tools_for_tier(tier: str | None) -> list[dict]:
    """Return public kernel tools.

    The public kernel has no product entitlement table. Hosts can mount
    product-specific tools through an overlay policy.
    """
    return list(PUBLIC_TOOLS)
