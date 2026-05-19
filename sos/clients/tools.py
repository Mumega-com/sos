from __future__ import annotations

from typing import Any, Dict

from sos.clients.base import BaseHTTPClient


class ToolsClient(BaseHTTPClient):
    def __init__(self, base_url: str = "http://localhost:6063", **kwargs):
        super().__init__(base_url, **kwargs)

    def health(self) -> Dict[str, Any]:
        return self._request("GET", "/health").json()

    async def execute(self, request: Any) -> Dict[str, Any]:
        """
        Execute a tool on the remote service.
        Request can be ToolCallRequest or dict.
        """
        # Handle both pydantic object and dict
        if hasattr(request, "tool_name"):
            tool_name = request.tool_name
            args = request.arguments
        elif isinstance(request, dict):
            tool_name = request.get("tool_name")
            args = request.get("arguments", {})
        else:
            raise ValueError("Invalid tool request format")
            
        # Route to specific endpoint if known, or generic /execute
        if tool_name == "web_search":
            # Map args to WebSearchRequest
            payload = {
                "query": args.get("query"),
                "count": args.get("count", 5),
                "provider": args.get("provider", "auto")
            }
            return self._request("POST", "/tools/web_search", json=payload).json()
            
        if tool_name == "deep_research":
            # Map args to DeepResearchRequest
            payload = {
                "query": args.get("query"),
                "count": args.get("count", 3),
                "depth": args.get("depth", "standard")
            }
            return self._request("POST", "/tools/deep_research", json=payload).json()
            
        # Fallback to generic execute (if server supports it later)
        return self._request("POST", "/execute", json={"tool_name": tool_name, "arguments": args}).json()

