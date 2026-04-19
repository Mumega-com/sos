"""HTTP client for the SOS Registry Service.

Lets sibling services (notably :mod:`sos.services.brain`) list / look up agents
without importing :mod:`sos.services.registry` directly. Required by the
v0.4.5 Wave 5 brain→registry decoupling (P0-09).

``AgentIdentity`` is imported from :mod:`sos.kernel.identity` — never from the
service — so this client stays on the clients→kernel/contracts side of the
R2 line.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from sos.clients.base import (
    AsyncBaseHTTPClient,
    BaseHTTPClient,
    SOSClientError,
)
from sos.kernel.identity import (
    AgentDNA,
    AgentEconomics,
    AgentIdentity,
    PhysicsState,
    VerificationStatus,
)

DEFAULT_BASE_URL = "http://localhost:6067"
_TOKEN_ENV = "SOS_REGISTRY_TOKEN"
_URL_ENV = "SOS_REGISTRY_URL"


def _resolve_base_url(base_url: Optional[str]) -> str:
    return base_url or os.environ.get(_URL_ENV) or DEFAULT_BASE_URL


def _resolve_token(token: Optional[str]) -> Optional[str]:
    return (
        token
        if token is not None
        else (os.environ.get(_TOKEN_ENV) or os.environ.get("SOS_SYSTEM_TOKEN") or None)
    )


def _auth_headers(token: Optional[str]) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


# ---------------------------------------------------------------------------
# Deserialization
# ---------------------------------------------------------------------------


def _build_physics(raw: Optional[Dict[str, Any]]) -> PhysicsState:
    raw = raw or {}
    return PhysicsState(
        C=float(raw.get("C", 0.95)),
        alpha_norm=float(raw.get("alpha_norm", 0.0)),
        regime=str(raw.get("regime", "stable")),
        inner=dict(
            raw.get("inner")
            or {
                "receptivity": 1.0,
                "will": 0.8,
                "logic": 0.9,
            }
        ),
    )


def _build_economics(raw: Optional[Dict[str, Any]]) -> AgentEconomics:
    raw = raw or {}
    econ = AgentEconomics()
    econ.token_balance = float(raw.get("token_balance", econ.token_balance))
    econ.daily_budget_limit = float(raw.get("daily_budget_limit", econ.daily_budget_limit))
    values = raw.get("values")
    if isinstance(values, dict):
        econ.values = {str(k): float(v) for k, v in values.items()}
    return econ


def _build_dna(name: str, raw: Optional[Dict[str, Any]]) -> AgentDNA:
    raw = raw or {}
    return AgentDNA(
        id=f"agent:{name}",
        name=name,
        physics=_build_physics(raw.get("physics") if isinstance(raw, dict) else None),
        economics=_build_economics(raw.get("economics") if isinstance(raw, dict) else None),
        learning_strategy=str(raw.get("learning_strategy", "balanced")),
        beliefs=list(raw.get("beliefs") or []),
        tools=list(raw.get("tools") or []),
    )


def _deserialize_agent(data: Dict[str, Any]) -> AgentIdentity:
    """Inverse of :meth:`AgentIdentity.to_dict` — best-effort reconstruction."""
    name = data.get("name") or (data.get("id", "") or "").removeprefix("agent:")
    if not name:
        raise ValueError("agent record missing both 'name' and 'id'")

    ident = AgentIdentity(
        name=str(name),
        model=data.get("model"),
        squad_id=data.get("squad_id"),
        guild_id=data.get("guild_id"),
        public_key=data.get("public_key"),
        metadata=dict(data.get("metadata") or {}),
        edition=str(data.get("edition", "business")),
        dna=_build_dna(str(name), data.get("dna")),
    )

    capabilities = data.get("capabilities")
    if isinstance(capabilities, list):
        ident.capabilities = [str(c) for c in capabilities]

    raw_vs = data.get("verification_status")
    if raw_vs:
        try:
            ident.verification_status = VerificationStatus(raw_vs)
        except ValueError:
            pass
    verified_by = data.get("verified_by")
    if verified_by:
        ident.verified_by = str(verified_by)

    return ident


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------


class AsyncRegistryClient(AsyncBaseHTTPClient):
    """Async HTTP client for the Registry service."""

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

    async def health(self) -> Dict[str, Any]:
        resp = await self._request("GET", "/health", headers=_auth_headers(self._token))
        return resp.json()

    async def list_agents(self, project: Optional[str] = None) -> List[AgentIdentity]:
        """Return the list of agents visible to this token.

        ``project`` is forwarded as a query param. System / admin tokens may
        pass any value (including ``None`` for "all projects"); scoped tokens
        are forced to their own project by the service — a mismatch raises
        :class:`SOSClientError` with status 403.
        """
        path = "/agents"
        if project is not None:
            path = f"/agents?project={project}"
        resp = await self._request("GET", path, headers=_auth_headers(self._token))
        body = resp.json()
        raw_list = body.get("agents", []) if isinstance(body, dict) else []
        agents: List[AgentIdentity] = []
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            try:
                agents.append(_deserialize_agent(item))
            except Exception:
                # Skip malformed records rather than fail the whole list.
                continue
        return agents

    async def get_agent(
        self, agent_id: str, project: Optional[str] = None
    ) -> Optional[AgentIdentity]:
        """Return a single agent or ``None`` if absent."""
        path = f"/agents/{agent_id}"
        if project is not None:
            path = f"{path}?project={project}"
        try:
            resp = await self._request("GET", path, headers=_auth_headers(self._token))
        except SOSClientError as exc:
            if exc.status_code == 404:
                return None
            raise
        body = resp.json()
        if not isinstance(body, dict):
            return None
        try:
            return _deserialize_agent(body)
        except Exception:
            return None

    async def enroll_mesh(
        self,
        *,
        agent_id: str,
        name: str,
        role: str,
        skills: Optional[list[str]] = None,
        squads: Optional[list[str]] = None,
        heartbeat_url: Optional[str] = None,
        project: Optional[str] = None,
        private_key_b64: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /mesh/enroll — register an agent into the mesh registry.

        Since v0.9.2.1 enrollment requires proof-of-key. The client will:
        1. Load or generate an Ed25519 keypair (at ``~/.sos/keys/<name>.priv``)
           unless ``private_key_b64`` is explicitly passed.
        2. Fetch a one-time nonce from ``POST /mesh/challenge``.
        3. Sign ``enroll_message(agent_id, nonce, hash(identity_fields))``.
        4. POST the full signed envelope to ``/mesh/enroll``.

        Returns the endpoint's response body on success.  Raises
        :class:`SOSClientError` on non-2xx.
        """
        from sos.kernel.crypto import (
            canonical_payload_hash,
            enroll_message,
            load_or_create_keypair,
            public_key_from_private,
            sign,
        )

        if private_key_b64 is None:
            private_key_b64, public_key_b64 = load_or_create_keypair(agent_id)
        else:
            public_key_b64 = public_key_from_private(private_key_b64)

        skills_list = list(skills) if skills is not None else []
        squads_list = list(squads) if squads is not None else []

        # Fetch challenge (no auth required).
        ch_resp = await self._request(
            "POST",
            "/mesh/challenge",
            json={"agent_id": agent_id},
        )
        nonce = ch_resp.json().get("nonce", "")
        if not nonce:
            raise SOSClientError(
                status_code=ch_resp.status_code or 500,
                message="challenge returned no nonce",
                body=ch_resp.text,
            )

        payload_for_hash = {
            "agent_id": agent_id,
            "name": name,
            "role": role,
            "skills": skills_list,
            "squads": squads_list,
            "public_key": public_key_b64,
        }
        payload_hash = canonical_payload_hash(payload_for_hash)
        signature = sign(private_key_b64, enroll_message(agent_id, nonce, payload_hash))

        payload: Dict[str, Any] = {
            "agent_id": agent_id,
            "name": name,
            "role": role,
            "skills": skills_list,
            "squads": squads_list,
            "public_key": public_key_b64,
            "nonce": nonce,
            "signature": signature,
        }
        if heartbeat_url is not None:
            payload["heartbeat_url"] = heartbeat_url
        if project is not None:
            payload["project"] = project
        resp = await self._request(
            "POST",
            "/mesh/enroll",
            headers=_auth_headers(self._token),
            json=payload,
        )
        return resp.json()


class RegistryClient(BaseHTTPClient):
    """Synchronous HTTP client for the Registry service."""

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

    def health(self) -> Dict[str, Any]:
        return self._request("GET", "/health", headers=_auth_headers(self._token)).json()

    def list_agents(self, project: Optional[str] = None) -> List[AgentIdentity]:
        path = "/agents"
        if project is not None:
            path = f"/agents?project={project}"
        resp = self._request("GET", path, headers=_auth_headers(self._token))
        body = resp.json()
        raw_list = body.get("agents", []) if isinstance(body, dict) else []
        agents: List[AgentIdentity] = []
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            try:
                agents.append(_deserialize_agent(item))
            except Exception:
                continue
        return agents

    def get_agent(self, agent_id: str, project: Optional[str] = None) -> Optional[AgentIdentity]:
        path = f"/agents/{agent_id}"
        if project is not None:
            path = f"{path}?project={project}"
        try:
            resp = self._request("GET", path, headers=_auth_headers(self._token))
        except SOSClientError as exc:
            if exc.status_code == 404:
                return None
            raise
        body = resp.json()
        if not isinstance(body, dict):
            return None
        try:
            return _deserialize_agent(body)
        except Exception:
            return None


__all__ = ["AsyncRegistryClient", "RegistryClient"]
