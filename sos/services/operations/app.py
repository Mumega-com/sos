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

import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from sos import __version__
from sos.kernel.auth import verify_bearer as _auth_verify_bearer
from sos.kernel.health import health_response
from sos.observability.logging import get_logger
from sos.services.operations.runner import load_template, run_operation

SERVICE_NAME = "operations"
DEFAULT_PORT = 6068
_START_TIME = time.time()

log = get_logger(SERVICE_NAME, min_level=os.getenv("SOS_LOG_LEVEL", "info"))

app = FastAPI(title="SOS Operations Service", version=__version__)

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


def _require_admin(authorization: Optional[str]) -> Dict[str, Any]:
    ctx = _auth_verify_bearer(authorization)
    if ctx is None:
        raise HTTPException(status_code=401, detail="missing or invalid bearer token")
    if not (ctx.is_system or ctx.is_admin):
        raise HTTPException(
            status_code=403, detail="operations.run requires system or admin token"
        )
    return {
        "project": ctx.project,
        "agent": ctx.agent,
        "is_system": ctx.is_system,
        "is_admin": ctx.is_admin,
    }


class RunRequest(BaseModel):
    customer: str
    product: str
    dry_run: bool = False


@app.get("/health")
async def health() -> Dict[str, Any]:
    return health_response(SERVICE_NAME, _START_TIME)


@app.post("/run")
async def run(
    req: RunRequest, authorization: Optional[str] = Header(default=None)
) -> Dict[str, Any]:
    _require_admin(authorization)
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
    _require_admin(authorization)
    _sos_root = Path(os.environ.get("SOS_ROOT", Path(__file__).parent.parent.parent.parent))
    ops_dir = Path(os.environ.get("SOS_OPERATIONS_DIR", str(_sos_root / "operations")))
    templates = sorted(f.stem for f in ops_dir.glob("*.yaml")) if ops_dir.exists() else []
    return {"templates": templates}


@app.get("/templates/{product}")
async def get_template(
    product: str, authorization: Optional[str] = Header(default=None)
) -> Dict[str, Any]:
    _require_admin(authorization)
    try:
        return load_template(product)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"template '{product}' not found")
