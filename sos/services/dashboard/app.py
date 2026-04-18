"""Mumega Tenant Dashboard — customer-facing web UI.

Shows agent status, recent tasks, memory entries, analytics, and billing.
Runs on port 8090. Auth via sos.services.auth — single source of truth.

Entry point: python3 -m sos.services.dashboard  (see __main__.py)
"""
from __future__ import annotations

import logging

from fastapi import FastAPI

from .routes import brain, health, login, customer, sos_operator, marketplace
from sos.kernel.telemetry import init_tracing, instrument_fastapi

logger = logging.getLogger("dashboard")

init_tracing("dashboard")

app = FastAPI(title="Mumega Dashboard", docs_url=None, redoc_url=None)
instrument_fastapi(app)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(login.router)
app.include_router(customer.router)
app.include_router(sos_operator.router)
app.include_router(marketplace.router)
app.include_router(brain.router)
app.include_router(health.router)
