"""SOS Operations Service — HTTP surface for the operation runner.

Exposes :mod:`sos.services.operations.runner` over HTTP so ``sos.clients.operations``
can call ``run_operation`` / ``load_template`` without importing the runner
module directly. That direct-import path is the P1-07 violation closed by
v0.4.6 Step 1.

Endpoints:
- ``GET /health`` — canonical SOS health response.
- ``POST /run`` — execute an operation pipeline for a customer.
- ``GET /templates`` — list available operation template slugs.
- ``GET /templates/{product}`` — load one template as a dict.

Auth: system / admin tokens only. Operations touch the filesystem + external
APIs (Mirror, metabolism) with elevated privileges; scoped tokens must not
trigger runs.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from sos import __version__
from sos.contracts.policy import PolicyDecision
from sos.kernel.health import health_response
from sos.kernel.policy.gate import can_execute
from sos.kernel.telemetry import init_tracing, instrument_fastapi
from sos.observability.logging import get_logger
from sos.services.operations.pulse import post_morning_pulse
from sos.services.operations.runner import load_template, run_operation

SERVICE_NAME = "operations"
DEFAULT_PORT = 6068
_START_TIME = time.time()

log = get_logger(SERVICE_NAME, min_level=os.getenv("SOS_LOG_LEVEL", "info"))

init_tracing("operations")

app = FastAPI(title="SOS Operations Service", version=__version__)
instrument_fastapi(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup() -> None:
    try:
        from sos.services.bus.discovery import register_service

        await register_service(SERVICE_NAME, DEFAULT_PORT)
    except Exception as exc:  # pragma: no cover — discovery is best-effort
        log.warning("operations discovery registration failed", error=str(exc))


def _raise_on_deny(decision: PolicyDecision, *, require_system: bool = False) -> None:
    """Map a gate decision to 401/403 if denied.

    When ``require_system`` is True, also enforce that the successful
    decision came via system/admin scope — the gate allows tenant-scoped
    callers into their own tenant, but operations are only meaningful
    from MCP's system token.
    """
    if not decision.allowed:
        reason = decision.reason or "unauthorized"
        if "bearer" in reason.lower() or "auth" in reason.lower():
            raise HTTPException(status_code=401, detail=reason)
        raise HTTPException(status_code=403, detail=reason)

    if require_system:
        # system/admin callers never get 'tenant_scope' added because the
        # gate short-circuits with 'system/admin scope' reason. Check that.
        if "system/admin" not in decision.reason:
            raise HTTPException(
                status_code=403,
                detail="operations require system or admin scope",
            )


class RunRequest(BaseModel):
    customer: str
    product: str
    dry_run: bool = False


class PulseTriggerRequest(BaseModel):
    tenant: str
    project: str


@app.get("/health")
async def health() -> Dict[str, Any]:
    return health_response(SERVICE_NAME, _START_TIME)


@app.post("/run")
async def run(
    req: RunRequest, authorization: Optional[str] = Header(default=None)
) -> Dict[str, Any]:
    decision = await can_execute(
        action="operations:run",
        resource=req.product,
        tenant="mumega",
        authorization=authorization,
    )
    _raise_on_deny(decision, require_system=True)
    try:
        return run_operation(req.customer, req.product, dry_run=req.dry_run)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        log.error("operation run failed", customer=req.customer, product=req.product, error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/templates")
async def list_templates(
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, List[str]]:
    decision = await can_execute(
        action="operations:templates_list",
        resource="all",
        tenant="mumega",
        authorization=authorization,
    )
    _raise_on_deny(decision, require_system=True)
    _sos_root = Path(os.environ.get("SOS_ROOT", Path(__file__).parent.parent.parent.parent))
    ops_dir = Path(os.environ.get("SOS_OPERATIONS_DIR", str(_sos_root / "operations")))
    templates = sorted(f.stem for f in ops_dir.glob("*.yaml")) if ops_dir.exists() else []
    return {"templates": templates}


@app.get("/templates/{product}")
async def get_template(
    product: str, authorization: Optional[str] = Header(default=None)
) -> Dict[str, Any]:
    decision = await can_execute(
        action="operations:template_read",
        resource=product,
        tenant="mumega",
        authorization=authorization,
    )
    _raise_on_deny(decision, require_system=True)
    try:
        return load_template(product)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"template '{product}' not found")


@app.post("/pulse/trigger")
async def trigger_pulse(
    req: PulseTriggerRequest,
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    decision = await can_execute(
        action="operations:pulse_trigger",
        resource=req.project,
        tenant=req.tenant,
        authorization=authorization,
    )
    _raise_on_deny(decision, require_system=True)
    started_at = datetime.now(timezone.utc).isoformat()
    # post_morning_pulse is fail-soft — it logs and returns "" on objectives
    # service failure. We fire-and-forget in a task so the HTTP response is
    # immediate; the pulse tree creation is async and best-effort.
    asyncio.create_task(post_morning_pulse(req.project))
    return {
        "ok": True,
        "tenant": req.tenant,
        "project": req.project,
        "started_at": started_at,
    }
