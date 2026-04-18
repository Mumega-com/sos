"""HTTP client for the SOS Identity Service.

Lets sibling services (notably autonomy) call avatar generation + social
automation endpoints without importing ``sos.services.identity.avatar``.
Required by the v0.4.5 Wave 4 autonomy→identity decoupling (P0-07).

The ``UV16D`` type comes from ``sos.contracts.identity`` — never from the
service — so this client stays on the clients→contracts side of the R2 line.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from sos.clients.base import (
    AsyncBaseHTTPClient,
    BaseHTTPClient,
)
from sos.contracts.identity import UV16D

DEFAULT_BASE_URL = "http://localhost:6064"
_TOKEN_ENV = "SOS_IDENTITY_TOKEN"
_URL_ENV = "SOS_IDENTITY_URL"


def _resolve_base_url(base_url: Optional[str]) -> str:
    return base_url or os.environ.get(_URL_ENV) or DEFAULT_BASE_URL


def _resolve_token(token: Optional[str]) -> Optional[str]:
    return token if token is not None else (
        os.environ.get(_TOKEN_ENV) or os.environ.get("SOS_SYSTEM_TOKEN") or None
    )


def _auth_headers(token: Optional[str]) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def _generate_payload(
    agent_id: str,
    uv: UV16D,
    alpha_drift: Optional[float],
    event_type: str,
) -> Dict[str, Any]:
    return {
        "agent_id": agent_id,
        "uv": uv.to_dict(),
        "alpha_drift": alpha_drift,
        "event_type": event_type,
    }


def _drift_payload(
    agent_id: str,
    uv: UV16D,
    alpha_value: float,
    insight: str,
    platforms: Optional[List[str]],
) -> Dict[str, Any]:
    return {
        "agent_id": agent_id,
        "uv": uv.to_dict(),
        "alpha_value": alpha_value,
        "insight": insight,
        "platforms": platforms,
    }


class AsyncIdentityClient(AsyncBaseHTTPClient):
    """Async HTTP client for the Identity service."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        super().__init__(
            base_url=_resolve_base_url(base_url),
            timeout_seconds=timeout_seconds,
        )
        self._token = _resolve_token(token)

    async def generate_avatar(
        self,
        agent_id: str,
        uv: UV16D,
        alpha_drift: Optional[float] = None,
        event_type: str = "state_snapshot",
    ) -> Dict[str, Any]:
        """POST /avatar/generate — returns the generator result dict."""
        resp = await self._request(
            "POST",
            "/avatar/generate",
            json=_generate_payload(agent_id, uv, alpha_drift, event_type),
            headers=_auth_headers(self._token),
        )
        return resp.json()

    async def on_alpha_drift(
        self,
        agent_id: str,
        uv: UV16D,
        alpha_value: float,
        insight: str,
        platforms: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """POST /avatar/social/on_alpha_drift — returns the social-post result dict."""
        resp = await self._request(
            "POST",
            "/avatar/social/on_alpha_drift",
            json=_drift_payload(agent_id, uv, alpha_value, insight, platforms),
            headers=_auth_headers(self._token),
        )
        return resp.json()


class IdentityClient(BaseHTTPClient):
    """Synchronous HTTP client for the Identity service."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        super().__init__(
            base_url=_resolve_base_url(base_url),
            timeout_seconds=timeout_seconds,
        )
        self._token = _resolve_token(token)

    def generate_avatar(
        self,
        agent_id: str,
        uv: UV16D,
        alpha_drift: Optional[float] = None,
        event_type: str = "state_snapshot",
    ) -> Dict[str, Any]:
        resp = self._request(
            "POST",
            "/avatar/generate",
            json=_generate_payload(agent_id, uv, alpha_drift, event_type),
            headers=_auth_headers(self._token),
        )
        return resp.json()

    def on_alpha_drift(
        self,
        agent_id: str,
        uv: UV16D,
        alpha_value: float,
        insight: str,
        platforms: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        resp = self._request(
            "POST",
            "/avatar/social/on_alpha_drift",
            json=_drift_payload(agent_id, uv, alpha_value, insight, platforms),
            headers=_auth_headers(self._token),
        )
        return resp.json()


__all__ = ["AsyncIdentityClient", "IdentityClient"]
