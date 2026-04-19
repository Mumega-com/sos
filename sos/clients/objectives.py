"""HTTP client for the SOS Objectives Service (v0.8.0).

Agents and services use this to interact with the objectives tree without
hand-rolling requests or importing from sos.services.*.

Two classes are provided:
- ObjectivesClient  — synchronous, extends BaseHTTPClient
- AsyncObjectivesClient — async, extends AsyncBaseHTTPClient

Env vars:
  SOS_OBJECTIVES_URL    — base URL (default http://localhost:6068)
  SOS_OBJECTIVES_TOKEN  — auth token (falls back to SOS_SYSTEM_TOKEN)
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from sos.clients.base import (
    AsyncBaseHTTPClient,
    BaseHTTPClient,
    SOSClientError,
)
from sos.contracts.objective import Objective

DEFAULT_BASE_URL = "http://localhost:6068"
_URL_ENV = "SOS_OBJECTIVES_URL"
_TOKEN_ENV = "SOS_OBJECTIVES_TOKEN"


def _resolve_base_url(base_url: Optional[str]) -> str:
    return base_url or os.environ.get(_URL_ENV) or DEFAULT_BASE_URL


def _resolve_token(token: Optional[str]) -> Optional[str]:
    if token is not None:
        return token
    return os.environ.get(_TOKEN_ENV) or os.environ.get("SOS_SYSTEM_TOKEN") or None


def _auth_headers(token: Optional[str]) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def _build_params(**kwargs: Any) -> Dict[str, Any]:
    """Build a query-params dict, omitting None values."""
    return {k: v for k, v in kwargs.items() if v is not None}


# ---------------------------------------------------------------------------
# Synchronous client
# ---------------------------------------------------------------------------


class ObjectivesClient(BaseHTTPClient):
    """Synchronous HTTP client for the Objectives service."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout_seconds: float = 10.0,
        transport: Optional[Any] = None,
    ) -> None:
        super().__init__(
            base_url=_resolve_base_url(base_url),
            timeout_seconds=timeout_seconds,
            transport=transport,
        )
        self._token = _resolve_token(token)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        title: str,
        *,
        parent_id: Optional[str] = None,
        description: str = "",
        bounty_mind: int = 0,
        tags: Optional[List[str]] = None,
        capabilities_required: Optional[List[str]] = None,
        subscribers: Optional[List[str]] = None,
        tenant_id: str = "default",
        project: Optional[str] = None,
        created_by: str,
    ) -> Objective:
        body: Dict[str, Any] = {
            "title": title,
            "description": description,
            "bounty_mind": bounty_mind,
            "tags": tags or [],
            "capabilities_required": capabilities_required or [],
            "subscribers": subscribers or [],
            "tenant_id": tenant_id,
            "created_by": created_by,
        }
        if parent_id is not None:
            body["parent_id"] = parent_id
        if project is not None:
            body["project"] = project
        resp = self._request(
            "POST",
            "/objectives",
            json=body,
            headers=_auth_headers(self._token),
            params=_build_params(project=project),
        )
        return Objective.model_validate(resp.json())

    def get(self, obj_id: str, *, project: Optional[str] = None) -> Optional[Objective]:
        try:
            resp = self._request(
                "GET",
                f"/objectives/{obj_id}",
                headers=_auth_headers(self._token),
                params=_build_params(project=project),
            )
        except SOSClientError as exc:
            if exc.status_code == 404:
                return None
            raise
        return Objective.model_validate(resp.json())

    def tree(
        self,
        obj_id: str,
        *,
        max_depth: int = 10,
        project: Optional[str] = None,
    ) -> dict:
        resp = self._request(
            "GET",
            f"/objectives/{obj_id}/tree",
            headers=_auth_headers(self._token),
            params=_build_params(max_depth=max_depth, project=project),
        )
        return resp.json()

    def query(
        self,
        *,
        tag: Optional[str] = None,
        min_bounty: Optional[int] = None,
        subtree: Optional[str] = None,
        capability: Optional[str] = None,
        project: Optional[str] = None,
    ) -> List[Objective]:
        resp = self._request(
            "GET",
            "/objectives",
            headers=_auth_headers(self._token),
            params=_build_params(
                tag=tag,
                min_bounty=min_bounty,
                subtree=subtree,
                capability=capability,
                project=project,
            ),
        )
        body = resp.json()
        raw_list = body.get("objectives", []) if isinstance(body, dict) else []
        return [Objective.model_validate(item) for item in raw_list]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def claim(
        self,
        obj_id: str,
        *,
        agent: Optional[str] = None,
        project: Optional[str] = None,
    ) -> dict:
        body: Dict[str, Any] = {}
        if agent is not None:
            body["agent"] = agent
        resp = self._request(
            "POST",
            f"/objectives/{obj_id}/claim",
            json=body,
            headers=_auth_headers(self._token),
            params=_build_params(project=project),
        )
        return resp.json()

    def heartbeat(self, obj_id: str, *, project: Optional[str] = None) -> bool:
        resp = self._request(
            "POST",
            f"/objectives/{obj_id}/heartbeat",
            headers=_auth_headers(self._token),
            params=_build_params(project=project),
        )
        return bool(resp.json().get("ok", False))

    def release(self, obj_id: str, *, project: Optional[str] = None) -> bool:
        resp = self._request(
            "POST",
            f"/objectives/{obj_id}/release",
            headers=_auth_headers(self._token),
            params=_build_params(project=project),
        )
        return bool(resp.json().get("ok", False))

    def complete(
        self,
        obj_id: str,
        *,
        artifact_url: str,
        notes: str = "",
        project: Optional[str] = None,
    ) -> dict:
        resp = self._request(
            "POST",
            f"/objectives/{obj_id}/complete",
            json={"artifact_url": artifact_url, "notes": notes},
            headers=_auth_headers(self._token),
            params=_build_params(project=project),
        )
        return resp.json()

    def ack(self, obj_id: str, *, acker: str, project: Optional[str] = None) -> dict:
        resp = self._request(
            "POST",
            f"/objectives/{obj_id}/ack",
            json={"acker": acker},
            headers=_auth_headers(self._token),
            params=_build_params(project=project),
        )
        return resp.json()


# ---------------------------------------------------------------------------
# Async client
# ---------------------------------------------------------------------------


class AsyncObjectivesClient(AsyncBaseHTTPClient):
    """Async HTTP client for the Objectives service."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        super().__init__(
            base_url=_resolve_base_url(base_url),
            timeout_seconds=timeout_seconds,
        )
        self._token = _resolve_token(token)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def create(
        self,
        title: str,
        *,
        parent_id: Optional[str] = None,
        description: str = "",
        bounty_mind: int = 0,
        tags: Optional[List[str]] = None,
        capabilities_required: Optional[List[str]] = None,
        subscribers: Optional[List[str]] = None,
        tenant_id: str = "default",
        project: Optional[str] = None,
        created_by: str,
    ) -> Objective:
        body: Dict[str, Any] = {
            "title": title,
            "description": description,
            "bounty_mind": bounty_mind,
            "tags": tags or [],
            "capabilities_required": capabilities_required or [],
            "subscribers": subscribers or [],
            "tenant_id": tenant_id,
            "created_by": created_by,
        }
        if parent_id is not None:
            body["parent_id"] = parent_id
        if project is not None:
            body["project"] = project
        resp = await self._request(
            "POST",
            "/objectives",
            json=body,
            headers=_auth_headers(self._token),
            params=_build_params(project=project),
        )
        return Objective.model_validate(resp.json())

    async def get(
        self, obj_id: str, *, project: Optional[str] = None
    ) -> Optional[Objective]:
        try:
            resp = await self._request(
                "GET",
                f"/objectives/{obj_id}",
                headers=_auth_headers(self._token),
                params=_build_params(project=project),
            )
        except SOSClientError as exc:
            if exc.status_code == 404:
                return None
            raise
        return Objective.model_validate(resp.json())

    async def tree(
        self,
        obj_id: str,
        *,
        max_depth: int = 10,
        project: Optional[str] = None,
    ) -> dict:
        resp = await self._request(
            "GET",
            f"/objectives/{obj_id}/tree",
            headers=_auth_headers(self._token),
            params=_build_params(max_depth=max_depth, project=project),
        )
        return resp.json()

    async def query(
        self,
        *,
        tag: Optional[str] = None,
        min_bounty: Optional[int] = None,
        subtree: Optional[str] = None,
        capability: Optional[str] = None,
        project: Optional[str] = None,
    ) -> List[Objective]:
        resp = await self._request(
            "GET",
            "/objectives",
            headers=_auth_headers(self._token),
            params=_build_params(
                tag=tag,
                min_bounty=min_bounty,
                subtree=subtree,
                capability=capability,
                project=project,
            ),
        )
        body = resp.json()
        raw_list = body.get("objectives", []) if isinstance(body, dict) else []
        return [Objective.model_validate(item) for item in raw_list]

    async def claim(
        self,
        obj_id: str,
        *,
        agent: Optional[str] = None,
        project: Optional[str] = None,
    ) -> dict:
        body: Dict[str, Any] = {}
        if agent is not None:
            body["agent"] = agent
        resp = await self._request(
            "POST",
            f"/objectives/{obj_id}/claim",
            json=body,
            headers=_auth_headers(self._token),
            params=_build_params(project=project),
        )
        return resp.json()

    async def heartbeat(self, obj_id: str, *, project: Optional[str] = None) -> bool:
        resp = await self._request(
            "POST",
            f"/objectives/{obj_id}/heartbeat",
            headers=_auth_headers(self._token),
            params=_build_params(project=project),
        )
        return bool(resp.json().get("ok", False))

    async def release(self, obj_id: str, *, project: Optional[str] = None) -> bool:
        resp = await self._request(
            "POST",
            f"/objectives/{obj_id}/release",
            headers=_auth_headers(self._token),
            params=_build_params(project=project),
        )
        return bool(resp.json().get("ok", False))

    async def complete(
        self,
        obj_id: str,
        *,
        artifact_url: str,
        notes: str = "",
        project: Optional[str] = None,
    ) -> dict:
        resp = await self._request(
            "POST",
            f"/objectives/{obj_id}/complete",
            json={"artifact_url": artifact_url, "notes": notes},
            headers=_auth_headers(self._token),
            params=_build_params(project=project),
        )
        return resp.json()

    async def ack(
        self, obj_id: str, *, acker: str, project: Optional[str] = None
    ) -> dict:
        resp = await self._request(
            "POST",
            f"/objectives/{obj_id}/ack",
            json={"acker": acker},
            headers=_auth_headers(self._token),
            params=_build_params(project=project),
        )
        return resp.json()


__all__ = ["ObjectivesClient", "AsyncObjectivesClient"]
