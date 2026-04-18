"""HTTP client for the Billing service.

v0.4.7 Phase 3: MCP's ``/webhook/stripe`` route used to import
``stripe_webhook_handler`` from ``sos.services.billing.webhook`` and
call it in-process (R2 violation). It now proxies the raw Stripe
request to the billing service via ``BillingClient``. Stripe signature
verification still runs inside billing — the proxy forwards the body
and stripe-signature header unchanged.

Billing is expected on ``SOS_BILLING_URL`` (default ``http://localhost:8077``).
"""
from __future__ import annotations

import os
from typing import Any, Dict, Mapping, Optional

import httpx

from sos.clients.base import AsyncBaseHTTPClient, BaseHTTPClient

_DEFAULT_BASE = os.environ.get("SOS_BILLING_URL", "http://localhost:8077")


def _resolve_base_url(base_url: Optional[str]) -> str:
    if base_url:
        return base_url
    return os.environ.get("SOS_BILLING_URL", _DEFAULT_BASE)


class BillingClient(BaseHTTPClient):
    """Sync client — exists for parity; the hot path is async."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout_seconds: float = 30.0,
    ):
        super().__init__(
            base_url=_resolve_base_url(base_url),
            timeout_seconds=timeout_seconds,
        )

    def health(self) -> Dict[str, Any]:
        return self._request("GET", "/health").json()


class AsyncBillingClient(AsyncBaseHTTPClient):
    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout_seconds: float = 30.0,
    ):
        super().__init__(
            base_url=_resolve_base_url(base_url),
            timeout_seconds=timeout_seconds,
        )

    async def health(self) -> Dict[str, Any]:
        resp = await self._request("GET", "/health")
        return resp.json()

    async def forward_stripe_webhook(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
    ) -> httpx.Response:
        """POST raw Stripe webhook payload to billing for signature
        verification + handling.

        Forwards the bytes and every header that matters for Stripe
        (``stripe-signature``, ``content-type``). Returns the raw
        response so callers can mirror status + body back to Stripe.
        """
        forward_headers: Dict[str, str] = {}
        for key in ("stripe-signature", "content-type", "user-agent"):
            val = headers.get(key) or headers.get(key.lower())
            if val:
                forward_headers[key] = val
        forward_headers.setdefault("content-type", "application/json")
        return await self._request(
            "POST",
            "/webhook/stripe",
            content=raw_body,
            headers=forward_headers,
        )
