"""Generic MCP tool registry for the public SOS kernel."""
from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any


ToolHandler = Callable[[Mapping[str, Any]], Mapping[str, Any] | Awaitable[Mapping[str, Any]]]


class ToolUnavailableError(LookupError):
    """Raised when a requested tool has no mounted handler."""


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: Mapping[str, Any]

    def as_mcp_tool(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": dict(self.input_schema),
        }


PUBLIC_PROTOCOL_TOOL_NAMES = frozenset({
    "send",
    "inbox",
    "broadcast",
    "recall",
    "status",
    "peers",
})


PUBLIC_PROTOCOL_TOOLS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name="send",
        description="Send an asynchronous message to another agent.",
        input_schema={
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient agent name."},
                "text": {"type": "string", "description": "Message body."},
            },
            "required": ["to", "text"],
        },
    ),
    ToolDefinition(
        name="inbox",
        description="Read messages addressed to an agent.",
        input_schema={
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Agent name."},
                "limit": {"type": "integer", "default": 10},
                "since": {"type": "string", "description": "Optional stream cursor."},
                "format": {
                    "type": "string",
                    "enum": ["text", "json"],
                    "default": "text",
                },
            },
        },
    ),
    ToolDefinition(
        name="broadcast",
        description="Publish a message to a shared project channel.",
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Message body."},
                "channel": {"type": "string", "default": "global"},
            },
            "required": ["text"],
        },
    ),
    ToolDefinition(
        name="recall",
        description="Search local memory visible to the current agent.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    ),
    ToolDefinition(
        name="status",
        description="Return agent and service status for this SOS runtime.",
        input_schema={"type": "object", "properties": {}},
    ),
    ToolDefinition(
        name="peers",
        description="List known agents in the local project.",
        input_schema={"type": "object", "properties": {}},
    ),
)


class ToolRegistry:
    """Definitions plus optional handlers, with fail-closed dispatch."""

    def __init__(
        self,
        tools: Iterable[ToolDefinition],
        handlers: Mapping[str, ToolHandler] | None = None,
    ) -> None:
        self._tools = {tool.name: tool for tool in tools}
        self._handlers = dict(handlers or {})

    def list_tools(self) -> list[dict[str, Any]]:
        return [tool.as_mcp_tool() for tool in self._tools.values()]

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def mount(self, name: str, handler: ToolHandler) -> None:
        if name not in self._tools:
            raise ToolUnavailableError(f"Tool not registered: {name}")
        self._handlers[name] = handler

    async def dispatch(self, name: str, arguments: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        if name not in self._tools:
            raise ToolUnavailableError(f"Tool not registered: {name}")
        handler = self._handlers.get(name)
        if handler is None:
            raise ToolUnavailableError(f"Tool not mounted: {name}")
        result = handler(dict(arguments or {}))
        if inspect.isawaitable(result):
            result = await result
        return result


def public_protocol_tools() -> list[dict[str, Any]]:
    return [tool.as_mcp_tool() for tool in PUBLIC_PROTOCOL_TOOLS]


def default_public_registry(handlers: Mapping[str, ToolHandler] | None = None) -> ToolRegistry:
    return ToolRegistry(PUBLIC_PROTOCOL_TOOLS, handlers)
