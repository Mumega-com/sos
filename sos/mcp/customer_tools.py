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
    # Marketplace tools — handled directly in handle_tool, not remapped
    "browse_marketplace": "browse_marketplace",
    "subscribe": "subscribe",
    "my_subscriptions": "my_subscriptions",
    "create_listing": "create_listing",
    "my_earnings": "my_earnings",
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
