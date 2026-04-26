"""Public MCP tool surface — consumer-agnostic definitions for npm/@mumega/mcp.

These 6 tools are the product interface for external AI users (Claude, Cursor, ChatGPT).
Tool descriptions use generic verbs so the OpenAI Apps wrapper is ~1 day of work post-S013.

LOCK-ENTITLEMENT-1: enforcement is server-side via JWT tier claim.
Free tier: 5 ruliads, 1 channel, 7-day memory. In-tool upgrade prompts included.

Naming convention (locked in brief §8):
  Consumer tools = WHAT (generic verbs): observe_pipeline, get_briefing, ...
  Internal ruliads = HOW (specific): stale_deal_nudge, hot_opportunity_flag, ...
"""
from __future__ import annotations

PUBLIC_TOOLS: list[dict] = [
    {
        "name": "observe_pipeline",
        "description": (
            "Get a live view of your business pipeline — deals, opportunities, "
            "and their current status. Returns what's active, what's stale, "
            "and what needs attention today. "
            "Your AI can call this to stay aware of where revenue stands."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "stage": {
                    "type": "string",
                    "description": (
                        "Filter by deal stage. One of: all, active, stale, hot, closed. "
                        "Default: active."
                    ),
                    "enum": ["all", "active", "stale", "hot", "closed"],
                    "default": "active",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max deals to return (default 10, max 50).",
                    "default": 10,
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_briefing",
        "description": (
            "Get today's business briefing — a summary of what happened, "
            "what needs action, and what signals fired overnight. "
            "Equivalent to a morning stand-up from your AI coordinator. "
            "Call this at the start of a work session."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "window": {
                    "type": "string",
                    "description": (
                        "Time window for the briefing. One of: today, week, month. "
                        "Default: today."
                    ),
                    "enum": ["today", "week", "month"],
                    "default": "today",
                },
            },
            "required": [],
        },
    },
    {
        "name": "list_signals",
        "description": (
            "List active signals your AI coordinator has detected — "
            "stale deals, hot opportunities, relationship gaps, health alerts. "
            "Each signal has a priority, a description, and a suggested action. "
            "Free tier: up to 5 signal types active at once. "
            "Upgrade to $299/mo to unlock all 24 signal types."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": (
                        "Filter by category. One of: all, lifecycle, relationship, "
                        "health, growth, care. Default: all."
                    ),
                    "enum": ["all", "lifecycle", "relationship", "health", "growth", "care"],
                    "default": "all",
                },
                "min_priority": {
                    "type": "string",
                    "description": "Minimum priority to include. One of: low, medium, high.",
                    "enum": ["low", "medium", "high"],
                    "default": "low",
                },
            },
            "required": [],
        },
    },
    {
        "name": "search_memory",
        "description": (
            "Search your business memory — everything your AI has observed "
            "and been told about your clients, deals, and team. "
            "Returns relevant memories with context. "
            "Free tier: memories expire after 7 days. "
            "Upgrade to $299/mo for permanent memory."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for. Be specific for best results.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 5, max 20).",
                    "default": 5,
                },
                "context": {
                    "type": "string",
                    "description": (
                        "Optional context scope — e.g. 'client-acme', 'q1-launch'. "
                        "Narrows search to memories under this label."
                    ),
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "connect_channel",
        "description": (
            "Connect a communication channel (Discord, Slack, email) so your AI "
            "coordinator can send you signals, briefings, and weekly reports there. "
            "Free tier: 1 channel. Upgrade to $299/mo for multiple channels. "
            "Returns a connection link to authorize the channel."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "description": "Channel type to connect.",
                    "enum": ["discord", "slack", "email"],
                },
                "identifier": {
                    "type": "string",
                    "description": (
                        "Channel identifier — Discord channel ID, Slack channel name, "
                        "or email address."
                    ),
                },
            },
            "required": ["type", "identifier"],
        },
    },
    {
        "name": "mint_knight",
        "description": (
            "Provision a named AI coordinator for a specific person or role "
            "in your business — a closer, a support lead, an account manager. "
            "The knight learns that person's rhythm and sends them personalised signals. "
            "Returns the knight's handle and a setup link."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Name for this coordinator. Use the person's first name or a role: "
                        "'sarah', 'closer-1', 'support-lead'."
                    ),
                },
                "role": {
                    "type": "string",
                    "description": (
                        "Role this coordinator serves. "
                        "One of: closer, account_manager, support_lead, founder, ops."
                    ),
                    "enum": ["closer", "account_manager", "support_lead", "founder", "ops"],
                },
                "channel_type": {
                    "type": "string",
                    "description": "Where this knight sends signals. One of: discord, slack, email.",
                    "enum": ["discord", "slack", "email"],
                    "default": "discord",
                },
            },
            "required": ["name", "role"],
        },
    },
]

# Free-tier tool allowlist — enforced server-side via JWT tier claim (LOCK-ENTITLEMENT-1).
# These are the tools available without a paid subscription.
# observe_pipeline and list_signals are read-only and always available.
# search_memory is available but with 7-day TTL on stored memories.
# connect_channel is available for 1 channel only.
# get_briefing is always available (drives upgrade motivation).
# mint_knight is gated to paid tier.
FREE_TIER_TOOLS = frozenset({
    "observe_pipeline",
    "get_briefing",
    "list_signals",
    "search_memory",
    "connect_channel",
})

PAID_TIER_TOOLS = frozenset({
    "mint_knight",
})

ALL_PUBLIC_TOOLS = FREE_TIER_TOOLS | PAID_TIER_TOOLS


def get_public_tools_for_tier(tier: str | None) -> list[dict]:
    """Return tool definitions filtered by tier.

    tier=None or 'free' → FREE_TIER_TOOLS only
    tier='starter'|'growth'|'scale' → all tools
    """
    allowed = FREE_TIER_TOOLS if tier in (None, "free") else ALL_PUBLIC_TOOLS
    return [t for t in PUBLIC_TOOLS if t["name"] in allowed]


# Upgrade prompts injected into tool responses when free-tier limit is hit.
UPGRADE_PROMPTS: dict[str, str] = {
    "list_signals": (
        "You're on the free tier (5 signal types). "
        "Upgrade to $299/mo at mumega.com/start to unlock all 24 signal types "
        "across lifecycle, relationship, health, growth, and care."
    ),
    "search_memory": (
        "You're on the free tier (7-day memory). "
        "Upgrade to $299/mo at mumega.com/start for permanent memory "
        "— your AI never forgets anything."
    ),
    "connect_channel": (
        "You're on the free tier (1 channel). "
        "Upgrade to $299/mo at mumega.com/start to connect multiple channels "
        "and route different signals to different places."
    ),
    "mint_knight": (
        "Minting knights requires the $299/mo plan. "
        "Start at mumega.com/start — your business never forgets."
    ),
}
