from __future__ import annotations

import os
from dataclasses import fields as _dc_fields
from typing import Any, Dict, List, Optional

from sos.clients.base import AsyncBaseHTTPClient, BaseHTTPClient
from sos.contracts.economy import UsageEvent


def _auth_headers(token: Optional[str]) -> Dict[str, str]:
    token = token or os.environ.get("SOS_ECONOMY_TOKEN") or os.environ.get("SOS_SYSTEM_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {}


class EconomyClient(BaseHTTPClient):
    def __init__(
        self,
        base_url: str = "http://localhost:6062",
        token: Optional[str] = None,
        **kwargs,
    ):
        headers = kwargs.pop("headers", None) or {}
        resolved = _auth_headers(token)
        for k, v in resolved.items():
            headers.setdefault(k, v)
        super().__init__(base_url, headers=headers, **kwargs)

    def health(self) -> Dict[str, Any]:
        return self._request("GET", "/health").json()

    async def get_balance(self, user_id: str) -> float:
        resp = self._request("GET", f"/balance/{user_id}")
        return resp.json().get("balance", 0.0)

    async def credit(self, user_id: str, amount: float, reason: str = "deposit") -> Dict[str, Any]:
        payload = {"user_id": user_id, "amount": amount, "reason": reason}
        return self._request("POST", "/credit", json=payload).json()

    async def debit(self, user_id: str, amount: float, reason: str = "spend") -> Dict[str, Any]:
        payload = {"user_id": user_id, "amount": amount, "reason": reason}
        return self._request("POST", "/debit", json=payload).json()

    async def mint_proof(self, metadata_uri: str) -> Dict[str, Any]:
        """
        Log an on-chain proof for a QNFT.
        """
        payload = {"metadata_uri": metadata_uri}
        return self._request("POST", "/mint_proof", json=payload).json()

    def can_spend(self, project: str, cost: float = 0.0) -> Dict[str, Any]:
        """Ask economy whether `project` has headroom for a `cost` action.

        Returns the full metabolism.can_spend contract:
        {allowed, budget, spent, remaining, pct_used, reason, warning?}.

        Closes kernel→services.economy.metabolism leak (v0.5.0). Callers in
        `sos.kernel.governance` must wrap in try/except and fail-open.
        """
        resp = self._request("GET", "/budget/can-spend", params={"project": project, "cost": cost})
        return resp.json()

    def list_usage(self, tenant: Optional[str] = None, limit: int = 100) -> List[UsageEvent]:
        """Read usage events from economy.

        Returns typed ``UsageEvent`` instances — the dashboard's money/tenants
        routes walk attribute access (e.cost_micros, e.metadata, etc.), so we
        deserialize at the client boundary rather than forcing callers to
        convert dicts.
        """
        from urllib.parse import urlencode
        query = {"limit": str(limit)}
        if tenant is not None:
            query["tenant"] = tenant
        resp = self._request("GET", f"/usage?{urlencode(query)}")
        data = resp.json()
        known = {f.name for f in _dc_fields(UsageEvent)}
        return [UsageEvent(**{k: v for k, v in ev.items() if k in known}) for ev in data.get("events", [])]


class AsyncEconomyClient(AsyncBaseHTTPClient):
    """Async HTTP client for the economy service.

    Introduced in v0.5.0 so `sos.kernel.governance.before_action` (async)
    can call `/budget/can-spend` without blocking. Other methods will be
    added as kernel touchpoints require them.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:6062",
        token: Optional[str] = None,
        **kwargs,
    ):
        headers = kwargs.pop("headers", None) or {}
        for k, v in _auth_headers(token).items():
            headers.setdefault(k, v)
        super().__init__(base_url, headers=headers, **kwargs)

    async def can_spend(self, project: str, cost: float = 0.0) -> Dict[str, Any]:
        """Async variant — see EconomyClient.can_spend."""
        resp = await self._request(
            "GET",
            "/budget/can-spend",
            params={"project": project, "cost": cost},
        )
        return resp.json()
