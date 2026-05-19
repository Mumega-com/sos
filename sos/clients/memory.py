from __future__ import annotations

from typing import Any, Dict, List, Optional

from sos.clients.base import BaseHTTPClient


class MemoryClient(BaseHTTPClient):
    def __init__(self, base_url: str = "http://localhost:6061", **kwargs):
        super().__init__(base_url, **kwargs)

    async def health(self) -> Dict[str, Any]:
        return self._request("GET", "/health").json()

    async def get_arf_state(self) -> Dict[str, Any]:
        return self._request("GET", "/state").json()

    async def add(self, content: str, metadata: Dict[str, Any] = None) -> str:
        payload = {"content": content, "metadata": metadata or {}}
        return self._request("POST", "/add", json=payload).json()["id"]

    async def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        payload = {"query": query, "limit": limit}
        return self._request("POST", "/search", json=payload).json()["results"]

    async def get_coherence(self) -> float:
        """Get current coherence from ARF state."""
        try:
            state = self._request("GET", "/state").json()
            return state.get("coherence_raw", 0.5)
        except Exception:
            return 0.5  # Default fallback
