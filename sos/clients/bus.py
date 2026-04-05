"""SOS Bus Client — send/receive messages via Redis bus."""
from __future__ import annotations

from typing import Any

from sos.clients.base import BaseHTTPClient


class BusClient(BaseHTTPClient):
    """Client for the Redis bus (local) or bus bridge (remote)."""

    def __init__(self, base_url: str = "http://localhost:6380", token: str = "", agent: str = "unknown", **kwargs):
        super().__init__(base_url, **kwargs)
        self.token = token
        self.agent = agent
        self._extra_headers = {"Authorization": f"Bearer {token}"} if token else {}

    def _req(self, method: str, path: str, **kwargs) -> Any:
        headers = kwargs.pop("headers", {})
        headers.update(self._extra_headers)
        return self._request(method, path, headers=headers, **kwargs).json()

    def send(self, to: str, text: str, project: str = None) -> dict:
        body = {"from": self.agent, "to": to, "text": text}
        if project:
            body["project"] = project
        return self._req("POST", "/send", json=body)

    def inbox(self, agent: str = None, limit: int = 10, project: str = None) -> dict:
        params = f"?agent={agent or self.agent}&limit={limit}"
        if project:
            params += f"&project={project}"
        return self._req("GET", f"/inbox{params}")

    def peers(self, project: str = None) -> dict:
        params = f"?project={project}" if project else ""
        return self._req("GET", f"/peers{params}")

    def broadcast(self, text: str, squad: str = None, project: str = None) -> dict:
        body = {"from": self.agent, "text": text}
        if squad:
            body["squad"] = squad
        if project:
            body["project"] = project
        return self._req("POST", "/broadcast", json=body)

    def announce(self, tool: str = "sdk", summary: str = "") -> dict:
        return self._req("POST", "/announce", json={
            "agent": self.agent,
            "tool": tool,
            "summary": summary or f"{self.agent} via SDK",
        })

    def health(self) -> dict:
        return self._req("GET", "/health")
