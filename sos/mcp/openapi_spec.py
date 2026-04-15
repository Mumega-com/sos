"""Generate OpenAPI spec from customer MCP tools.

ChatGPT custom GPTs use OpenAPI specs for Actions.
This converts our MCP tool definitions into an OpenAPI spec
so ChatGPT can call Mumega tools natively.
"""
from __future__ import annotations

from typing import Any

from sos.mcp.customer_tools import CUSTOMER_TOOLS


def _input_schema_to_openapi_body(tool: dict[str, Any]) -> dict[str, Any]:
    """Convert an MCP inputSchema to an OpenAPI requestBody schema."""
    schema = dict(tool.get("inputSchema", {}))
    # Remove MCP-specific keys that are not valid OpenAPI
    schema.pop("additionalProperties", None)
    return schema


def generate_openapi_spec(
    base_url: str = "https://mcp.mumega.com",
) -> dict[str, Any]:
    """Generate an OpenAPI 3.0 spec from the customer MCP tool definitions.

    Each tool becomes a POST /tools/{tool_name} endpoint.
    Bearer token auth is included in the security scheme.
    """
    paths: dict[str, Any] = {}

    for tool in CUSTOMER_TOOLS:
        name = tool["name"]
        description = tool.get("description", "")
        input_schema = _input_schema_to_openapi_body(tool)
        required = input_schema.get("required", [])
        properties = input_schema.get("properties", {})

        # Build request body only if the tool has input properties
        request_body: dict[str, Any] | None = None
        if properties:
            request_body = {
                "required": bool(required),
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": properties,
                            **({"required": required} if required else {}),
                        },
                    },
                },
            }

        operation: dict[str, Any] = {
            "operationId": name,
            "summary": description,
            "security": [{"bearerAuth": []}],
            "responses": {
                "200": {
                    "description": "Tool result",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "content": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "type": {
                                                    "type": "string",
                                                    "example": "text",
                                                },
                                                "text": {
                                                    "type": "string",
                                                },
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
                "401": {"description": "Invalid or missing token"},
                "403": {"description": "Tool not available for this token"},
                "429": {"description": "Rate limit exceeded"},
            },
        }

        if request_body:
            operation["requestBody"] = request_body

        paths[f"/tools/{name}"] = {"post": operation}

    spec: dict[str, Any] = {
        "openapi": "3.0.3",
        "info": {
            "title": "Mumega API",
            "description": (
                "AI business operating system. Connect your AI to manage "
                "memory, content, tasks, and commerce."
            ),
            "version": "1.0.0",
            "contact": {
                "name": "Mumega",
                "url": "https://mumega.com",
                "email": "support@mumega.com",
            },
        },
        "servers": [
            {
                "url": base_url,
                "description": "Mumega MCP endpoint",
            },
        ],
        "security": [{"bearerAuth": []}],
        "paths": paths,
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "MCP Token",
                    "description": (
                        "Your Mumega API token. Get it from your dashboard "
                        "or ask your Mumega admin."
                    ),
                },
            },
        },
    }

    return spec


def openapi_json(base_url: str = "https://mcp.mumega.com") -> str:
    """Return the OpenAPI spec as a JSON string."""
    import json

    return json.dumps(generate_openapi_spec(base_url), indent=2)


if __name__ == "__main__":
    print(openapi_json())
