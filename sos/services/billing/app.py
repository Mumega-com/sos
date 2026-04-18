"""Billing service — Stripe webhook + provisioning.

v0.4.7 Phase 3: moves Stripe webhook handling out of the MCP gateway so
MCP never imports sos.services.billing directly (R2). The MCP route
``/webhook/stripe`` now proxies to this service's ``/webhook/stripe``.

Port: 8077 (env: SOS_BILLING_PORT).
"""
from __future__ import annotations

import logging
import os
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from sos.kernel.health import health_response
from sos.kernel.telemetry import init_tracing, instrument_fastapi
from sos.services.billing.webhook import stripe_webhook_handler

log = logging.getLogger("sos.billing")

init_tracing("billing")

app = FastAPI(title="Mumega Billing Service", version="0.1.0")
instrument_fastapi(app)
_START_TIME = time.time()


@app.get("/health")
def health() -> dict:
    return health_response("billing", _START_TIME)


@app.post("/webhook/stripe")
async def stripe_webhook(request: Request) -> JSONResponse:
    """Entry point for Stripe webhook events.

    The handler reads the raw body + stripe-signature header directly
    from ``request``; the proxy in MCP must forward both unchanged for
    signature verification to succeed.
    """
    return await stripe_webhook_handler(request)


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("SOS_BILLING_PORT", "8077"))
    uvicorn.run(app, host="0.0.0.0", port=port)
