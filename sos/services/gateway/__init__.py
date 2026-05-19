"""
SOS Gateway Service - External Agent Bridge + MCP Gateway

Provides:
- Bridge API for external agents (ChatGPT, Claude, etc.)
- MCP Gateway with OAuth 2.1 support
- Unified entry point for external integrations
"""

from sos.services.gateway.bridge import app as bridge_app
from sos.services.gateway.mcp import app as mcp_app

__all__ = ["bridge_app", "mcp_app"]
