"""Customer MCP tools -- the product interface.

When a customer connects their AI to Mumega, these are the tools they get.
Each tool is scoped to their tenant via token auth.
No admin tools, no raw bus access, no cross-tenant visibility.
"""
from __future__ import annotations

# Tool definitions for MCP tools/list response
CUSTOMER_TOOLS: list[dict] = [
    {
        "name": "remember",
        "description": (
            "Save something to your business memory. Your AI remembers this "
            "across all future conversations. Use for: client preferences, "
            "project details, meeting notes, business rules, anything you "
            "want to recall later."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "What to remember",
                },
                "context": {
                    "type": "string",
                    "description": (
                        "Optional context label "
                        "(e.g., 'client-acme', 'product-launch')"
                    ),
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "recall",
        "description": (
            "Search your business memory. Finds relevant memories from all "
            "your past conversations. Use when you need context about clients, "
            "projects, decisions, or anything you previously saved."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 5)",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "publish",
        "description": (
            "Publish content to your website. Creates a blog post, page, or "
            "article on your Inkwell-powered site. The content goes live "
            "immediately or as a draft for your approval."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Content title",
                },
                "content": {
                    "type": "string",
                    "description": "Markdown content",
                },
                "slug": {
                    "type": "string",
                    "description": "URL slug (auto-generated if omitted)",
                },
                "status": {
                    "type": "string",
                    "enum": ["draft", "published"],
                    "default": "draft",
                    "description": "Publish immediately or save as draft",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Content tags",
                },
            },
            "required": ["title", "content"],
        },
    },
    {
        "name": "dashboard",
        "description": (
            "See how your business is doing. Returns website traffic, leads, "
            "revenue, and key metrics for the current period."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "description": (
                        "Time period: 'today', '7d', '30d', 'month' "
                        "(default '7d')"
                    ),
                },
            },
        },
    },
    {
        "name": "create_task",
        "description": (
            "Delegate work to your AI team. Creates a task for your squad "
            "(content writing, SEO audit, lead outreach, etc.). The task is "
            "automatically assigned to the best available agent."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Task title",
                },
                "description": {
                    "type": "string",
                    "description": "What needs to be done",
                },
                "priority": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "urgent"],
                    "default": "medium",
                },
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Task labels (e.g., 'content', 'seo', 'outreach')"
                    ),
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "list_tasks",
        "description": (
            "See what your AI team is working on. Shows tasks by status: "
            "queued, in progress, done."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["queued", "in_progress", "done", "all"],
                    "default": "all",
                },
            },
        },
    },
    {
        "name": "sell",
        "description": (
            "Create a payment link for a product or service. Uses Stripe to "
            "generate a checkout URL that you can share with clients."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "product_name": {
                    "type": "string",
                    "description": "What you're selling",
                },
                "price_cents": {
                    "type": "integer",
                    "description": "Price in cents (e.g., 2900 for $29)",
                },
                "currency": {
                    "type": "string",
                    "default": "usd",
                },
                "description": {
                    "type": "string",
                    "description": "Product description",
                },
            },
            "required": ["product_name", "price_cents"],
        },
    },
    {
        "name": "my_site",
        "description": (
            "Get information about your website -- URL, status, recent posts, "
            "active features."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "browse_marketplace",
        "description": (
            "Browse available AI squads and tools. Find teams to hire for "
            "content, SEO, development, outreach, and more."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search by keyword",
                },
                "category": {
                    "type": "string",
                    "enum": [
                        "content",
                        "seo",
                        "dev",
                        "outreach",
                        "marketing",
                        "data",
                        "other",
                    ],
                    "description": "Filter by category",
                },
            },
        },
    },
    {
        "name": "subscribe",
        "description": (
            "Subscribe to a squad or tool from the marketplace. "
            "The team starts working for you immediately."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "listing_id": {
                    "type": "string",
                    "description": "ID of the listing to subscribe to",
                },
            },
            "required": ["listing_id"],
        },
    },
    {
        "name": "my_subscriptions",
        "description": (
            "See your active marketplace subscriptions — squads and tools "
            "you're paying for."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "create_listing",
        "description": (
            "List your own squad or tool on the marketplace for others to "
            "subscribe to."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Listing title",
                },
                "description": {
                    "type": "string",
                    "description": "What this squad or tool does",
                },
                "category": {
                    "type": "string",
                    "description": "Category (content, seo, dev, outreach, marketing, data, other)",
                },
                "listing_type": {
                    "type": "string",
                    "enum": ["squad", "tool", "service"],
                    "description": "Type of listing",
                },
                "price_cents": {
                    "type": "integer",
                    "description": "Monthly price in cents (e.g. 4900 for $49)",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags to help buyers find your listing",
                },
            },
            "required": ["title", "description", "category", "listing_type", "price_cents"],
        },
    },
    {
        "name": "my_earnings",
        "description": (
            "See how much you're earning from your marketplace listings."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "notification_settings",
        "description": (
            "Configure how you receive notifications — email, Telegram, or webhook URL. "
            "Choose your preferred channels for business alerts and updates."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "email": {
                    "type": "boolean",
                    "description": "Receive email notifications",
                },
                "telegram": {
                    "type": "boolean",
                    "description": "Receive Telegram notifications (requires chat_id setup)",
                },
                "webhook": {
                    "type": "string",
                    "description": "Webhook URL to receive event notifications (optional)",
                },
                "in_app": {
                    "type": "boolean",
                    "description": "Receive in-app notifications in the dashboard",
                },
            },
        },
    },
    {
        "name": "request_squad",
        "description": (
            "Request a Mumega squad to join your project. Specialists load your "
            "business context and start working on your priorities. Use when you "
            "need help with specific tasks: content, SEO, ops, sales, technical work."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "description": (
                        "Type of squad needed: content, seo, ops, sales, "
                        "technical, support"
                    ),
                    "enum": ["content", "seo", "ops", "sales", "technical", "support"],
                },
                "task": {
                    "type": "string",
                    "description": "What you need them to work on",
                },
                "urgency": {
                    "type": "string",
                    "enum": ["low", "normal", "high"],
                    "default": "normal",
                },
            },
            "required": ["type", "task"],
        },
    },
    {
        "name": "squad_status",
        "description": (
            "Check who from the Mumega team is currently working on your project "
            "and what they're doing."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    # ─── S016 Track A — BYOA identity tools ────────────────────────────────
    {
        "name": "my_profile",
        "description": (
            "Show your Mumega identity — your name, email, QNFT, and the projects "
            "you have access to. Use this any time to see who you're signed in as."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "list_projects",
        "description": (
            "List the projects you have access to. Each project is a separate "
            "workspace with its own memory, tasks, and team. Use sign_in(project) "
            "to switch into one."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "sign_in",
        "description": (
            "Sign in to one of your projects. After sign-in, every tool call "
            "(remember, recall, task_create, etc.) is scoped to that project. "
            "You can switch projects any time by calling sign_in again."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": (
                        "Project slug to sign into (e.g. 'viamar', 'gaf'). "
                        "See list_projects for what's available."
                    ),
                },
            },
            "required": ["project"],
        },
    },
    {
        "name": "sign_out",
        "description": (
            "Sign out of the active project. Tool list reverts to identity-only "
            "tools (sign_in, list_projects, my_profile). Memory and tasks for the "
            "previous project remain saved — sign_in again to resume."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "invite",
        "description": (
            "Generate an invite link to add someone to your active project. "
            "Returns a https://mcp.mumega.com/join/<code> URL — share it; the "
            "recipient signs in with Google and joins automatically. "
            "Owner/admin only. You must be signed in to a project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "role": {
                    "type": "string",
                    "enum": ["viewer", "member", "admin", "owner"],
                    "default": "member",
                    "description": "Role the invitee receives on redemption.",
                },
                "max_uses": {
                    "type": "integer",
                    "default": 1,
                    "description": (
                        "How many times the link can be redeemed before "
                        "auto-expiry. Default 1 (single-use)."
                    ),
                },
                "expires_in_hours": {
                    "type": "integer",
                    "description": (
                        "Optional expiry in hours from now. Omit for no expiry."
                    ),
                },
            },
            "required": [],
        },
    },
]

# Map customer tool names to internal SOS MCP tool names
TOOL_MAPPING: dict[str, str] = {
    "remember": "remember",
    "recall": "recall",
    "publish": "publish_content",  # maps to Inkwell MCP tool
    "dashboard": "get_dashboard",
    "create_task": "task_create",
    "list_tasks": "task_list",
    "sell": "create_checkout",
    "my_site": "site_info",
    "notification_settings": "notification_settings",
    # Marketplace tools — handled directly in handle_tool, not remapped
    "browse_marketplace": "browse_marketplace",
    "subscribe": "subscribe",
    "my_subscriptions": "my_subscriptions",
    "create_listing": "create_listing",
    "my_earnings": "my_earnings",
    # Squad tools — handled directly in handle_tool, not remapped
    "request_squad": "request_squad",
    "squad_status": "squad_status",
    # S016 BYOA identity tools — handled directly, not remapped
    "my_profile": "my_profile",
    "list_projects": "list_projects",
    "sign_in": "sign_in",
    "sign_out": "sign_out",
    # S016 Track B — invite generator (admin/owner only)
    "invite": "invite",
}

# Identity tools — visible BEFORE sign-in (Step 5 dynamic tool list).
# These four are always allowed regardless of role/tier so a fresh BYOA
# connection can introspect itself and select a project.
IDENTITY_TOOLS: set[str] = {
    "my_profile",
    "list_projects",
    "sign_in",
    "sign_out",
}

# Tools that are explicitly BLOCKED for customers
BLOCKED_TOOLS: set[str] = {
    "send",         # raw bus messaging
    "broadcast",    # send to all agents
    "inbox",        # raw inbox access
    "peers",        # see internal agents
    "ask",          # direct agent queries
    "onboard",      # admin: create tenants
    "status",       # admin: system status
    "search_code",  # admin: code search
    "task_board",   # admin: cross-project board
    "task_update",  # admin: raw task mutation
    "memories",     # admin: raw memory listing
    "request",      # internal: raw request routing
}


def get_customer_tools() -> list[dict]:
    """Return the curated tool list for customer MCP connections."""
    return CUSTOMER_TOOLS


def is_customer_tool(name: str) -> bool:
    """Check if a tool name is in the customer-safe set."""
    return name in TOOL_MAPPING


def is_blocked_tool(name: str) -> bool:
    """Check if a tool name is explicitly blocked for customers."""
    return name in BLOCKED_TOOLS


def resolve_internal_name(customer_tool_name: str) -> str | None:
    """Map a customer-facing tool name to the internal SOS MCP tool name."""
    return TOOL_MAPPING.get(customer_tool_name)


# ---------------------------------------------------------------------------
# Role-based access control
# ---------------------------------------------------------------------------

# Tools allowed per role.  None means "all customer tools".
ROLE_TOOLS: dict[str, set[str] | None] = {
    "admin": None,   # admin sees every customer tool
    "owner": None,   # owner = full access (same as admin)
    "editor": {
        "remember",
        "recall",
        "publish",
        "create_task",
        "list_tasks",
        "sell",
        "dashboard",
        "my_site",
        "notification_settings",
        "browse_marketplace",
        "subscribe",
        "my_subscriptions",
        "request_squad",
        "squad_status",
        # Identity tools — every role gets these
        "my_profile",
        "list_projects",
        "sign_in",
        "sign_out",
    },
    "viewer": {
        "recall",
        "list_tasks",
        "dashboard",
        "my_site",
        "notification_settings",
        "my_subscriptions",
        "browse_marketplace",
        "squad_status",
        # Identity tools — every role gets these
        "my_profile",
        "list_projects",
        "sign_in",
        "sign_out",
    },
}


def get_tools_for_role(role: str = "admin") -> list[dict]:
    """Return tool definitions filtered by role.

    Unknown roles fall back to the most restrictive set (viewer).
    """
    allowed = ROLE_TOOLS.get(role, ROLE_TOOLS["viewer"])
    if allowed is None:
        return CUSTOMER_TOOLS
    return [t for t in CUSTOMER_TOOLS if t["name"] in allowed]


def is_tool_allowed_for_role(tool_name: str, role: str = "admin") -> bool:
    """Check if *tool_name* is permitted for *role*.

    Works on customer-facing tool names (before TOOL_MAPPING resolution).
    """
    allowed = ROLE_TOOLS.get(role, ROLE_TOOLS["viewer"])
    if allowed is None:
        return is_customer_tool(tool_name)
    return tool_name in allowed


# ---------------------------------------------------------------------------
# Tier-based access control (prospect / free tier gating)
# ---------------------------------------------------------------------------

# Prospect (free) tier — read-only exploration tools.
# Drives the PLG hook: they get immediate value, upgrade unlocks everything.
PROSPECT_TOOLS: set[str] = {
    "recall",
    "dashboard",
    "my_site",
    "list_tasks",
    "browse_marketplace",
    "my_subscriptions",
    "squad_status",
    # Identity tools — prospects need to sign in / list their projects
    "my_profile",
    "list_projects",
    "sign_in",
    "sign_out",
}


def get_tools_for_tier(tier: str, role: str = "admin") -> list[dict]:
    """Return tool definitions filtered by tier, then by role.

    free  → PROSPECT_TOOLS (read-only exploration)
    any other tier → full role-based set (starter / growth / scale)
    """
    if not tier or tier == "free":
        role_tools = get_tools_for_role(role)
        return [t for t in role_tools if t["name"] in PROSPECT_TOOLS]
    return get_tools_for_role(role)


def is_tool_allowed_for_tier(tool_name: str, tier: str, role: str = "admin") -> bool:
    """Check if *tool_name* is permitted for the given tier and role."""
    if not tier or tier == "free":
        return tool_name in PROSPECT_TOOLS and is_tool_allowed_for_role(tool_name, role)
    return is_tool_allowed_for_role(tool_name, role)
