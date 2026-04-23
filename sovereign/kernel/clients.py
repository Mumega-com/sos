"""
Sovereign Kernel Clients — thin HTTP client wrappers for Mirror and Squad Service.

Usage:
    from kernel.clients import MirrorClient, SquadClient

    mirror = MirrorClient()
    mirror.store({"agent": "brain", "text": "...", "context_id": "..."})
    results = mirror.search("active goals", top_k=10)

    squad = SquadClient()
    task = squad.create_task({"id": "t-001", "title": "Do thing", ...})
"""

import logging
from typing import Optional

import requests

from kernel import config

logger = logging.getLogger(__name__)


class MirrorClient:
    """Thin wrapper around the Mirror API (:8844)."""

    def __init__(self, base_url: Optional[str] = None, token: Optional[str] = None) -> None:
        self.base_url = base_url or config.MIRROR_URL
        self.token = token or config.MIRROR_TOKEN

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def store(self, payload: dict) -> dict:
        """POST /store — persist an engram."""
        try:
            r = requests.post(
                f"{self.base_url}/store",
                json=payload,
                headers=self._headers(),
                timeout=10,
            )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            logger.warning("MirrorClient.store failed: %s", exc)
            return {}

    def search(
        self,
        query: str,
        top_k: int = 10,
        threshold: float = 0.3,
        workspace_id: Optional[str] = None,
    ) -> list:
        """POST /search — semantic search over engrams."""
        body: dict = {"query": query, "top_k": top_k, "threshold": threshold}
        if workspace_id:
            body["workspace_id"] = workspace_id
        try:
            r = requests.post(
                f"{self.base_url}/search",
                json=body,
                headers=self._headers(),
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else data.get("results", [])
        except Exception as exc:
            logger.warning("MirrorClient.search failed: %s", exc)
            return []

    def recent(self, agent: str, limit: int = 10) -> list:
        """GET /recent/{agent} — fetch recent engrams for an agent."""
        try:
            r = requests.get(
                f"{self.base_url}/recent/{agent}",
                headers=self._headers(),
                params={"limit": limit},
                timeout=10,
            )
            r.raise_for_status()
            return r.json().get("engrams", [])
        except Exception as exc:
            logger.warning("MirrorClient.recent failed: %s", exc)
            return []


class SquadClient:
    """Thin wrapper around the Squad Service (:8060)."""

    def __init__(self, base_url: Optional[str] = None) -> None:
        self.base_url = base_url or config.SQUAD_URL

    def _headers(self) -> dict[str, str]:
        import os
        token = os.environ.get("SOS_SYSTEM_TOKEN", "sk-sos-system")
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def create_task(self, payload: dict) -> dict:
        """POST /tasks — create a task."""
        try:
            r = requests.post(
                f"{self.base_url}/tasks",
                json=payload,
                headers=self._headers(),
                timeout=10,
            )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            logger.warning("SquadClient.create_task failed: %s", exc)
            return {}

    def list_tasks(self, status: Optional[str] = None, label: Optional[str] = None) -> list:
        """GET /tasks — list tasks with optional filters."""
        params: dict = {}
        if status:
            params["status"] = status
        if label:
            params["label"] = label
        try:
            r = requests.get(
                f"{self.base_url}/tasks",
                params=params,
                headers=self._headers(),
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else data.get("tasks", [])
        except Exception as exc:
            logger.warning("SquadClient.list_tasks failed: %s", exc)
            return []

    def claim_task(self, task_id: str, agent: str) -> dict:
        """POST /tasks/{id}/claim — atomically claim a task."""
        try:
            r = requests.post(
                f"{self.base_url}/tasks/{task_id}/claim",
                json={"assignee": agent},
                headers=self._headers(),
                timeout=10,
            )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            logger.warning("SquadClient.claim_task failed: %s", exc)
            return {}

    def complete_task(self, task_id: str, result: dict) -> dict:
        """POST /tasks/{id}/complete — mark a task done."""
        try:
            r = requests.post(
                f"{self.base_url}/tasks/{task_id}/complete",
                json={"result": result},
                headers=self._headers(),
                timeout=10,
            )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            logger.warning("SquadClient.complete_task failed: %s", exc)
            return {}

    def health(self) -> dict:
        """GET /health — check Squad Service health."""
        try:
            r = requests.get(
                f"{self.base_url}/health",
                headers=self._headers(),
                timeout=10,
            )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            logger.warning("SquadClient.health failed: %s", exc)
            return {}
