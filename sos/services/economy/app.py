from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from sos import __version__
from sos.observability.logging import get_logger
from sos.services.auth import verify_bearer as _auth_verify_bearer
from sos.services.economy.wallet import SovereignWallet, InsufficientFundsError
from sos.services.economy.usage_log import UsageEvent, UsageLog
from sos.services.economy.settlement import settle_usage_event, SettlementResult
from sos.services._health import health_response

SERVICE_NAME = "economy"
_START_TIME = time.time()

log = get_logger(SERVICE_NAME, min_level=os.getenv("SOS_LOG_LEVEL", "info"))

wallet = SovereignWallet()
_usage_log = UsageLog(wallet=wallet)

app = FastAPI(title="SOS Economy Service", version=__version__)

# CORS for desktop/mobile apps
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class BalanceResponse(BaseModel):
    user_id: str
    balance: float
    currency: str = "RU"

class TransactionRequest(BaseModel):
    user_id: str
    amount: float
    reason: str = "transaction"

@app.get("/health")
async def health() -> Dict[str, Any]:
    return health_response(SERVICE_NAME, _START_TIME)

@app.get("/balance/{user_id}", response_model=BalanceResponse)
async def get_balance(user_id: str):
    balance = await wallet.get_balance(user_id)
    return BalanceResponse(user_id=user_id, balance=balance)

@app.post("/credit", response_model=BalanceResponse)
async def credit(req: TransactionRequest):
    try:
        new_balance = await wallet.credit(req.user_id, req.amount, req.reason)
        return BalanceResponse(user_id=req.user_id, balance=new_balance)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

class TransmuteRequest(BaseModel):
    user_id: str
    amount_mind: float
    target_address: str

@app.post("/transmute")
async def transmute(req: TransmuteRequest):
    """
    Burn local $MIND and release external Devnet SOL.
    """
    try:
        tx_hash = await wallet.transmute(req.user_id, req.amount_mind, req.target_address)
        return {
            "status": "confirmed",
            "tx_hash": tx_hash,
            "burned_mind": req.amount_mind
        }
    except InsufficientFundsError as e:
        raise HTTPException(status_code=402, detail=str(e))
    except Exception as e:
        log.error("Transmutation API failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))

class MintProofRequest(BaseModel):
    metadata_uri: str

@app.post("/mint_proof")
async def mint_proof(req: MintProofRequest):
    """
    Log an on-chain proof for a QNFT.
    """
    try:
        signature = await wallet.mint_proof(req.metadata_uri)
        return {"signature": signature, "status": "confirmed"}
    except Exception as e:
        log.error("Mint proof failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/debit", response_model=BalanceResponse)
async def debit(req: TransactionRequest):
    try:
        new_balance = await wallet.debit(req.user_id, req.amount, req.reason)
        return BalanceResponse(user_id=req.user_id, balance=new_balance)
    except InsufficientFundsError as e:
        raise HTTPException(status_code=402, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Usage ingest (trop issue #98)
# ---------------------------------------------------------------------------
#
# POST /usage — edge tenants (Cloudflare Workers / Pages Functions) report
# model-call telemetry here. Tenant-scoped via Bearer auth against tokens.json;
# tenants can only write events for their own scope.
#
# The endpoint is SOS (protocol): canonical UsageEvent shape, tenant scoping,
# append-only log. Commercial concerns (USD billing, Stripe invoicing, volume
# tier negotiation) belong to Mumega and layer on top of this log.


def _auth_ctx_to_entry(ctx: Any) -> dict[str, Any]:
    """Convert an AuthContext to the legacy ``entry`` dict shape.

    ``_resolve_tenant`` reads ``entry.get("project")`` — that key is preserved.
    """
    return {
        "project": ctx.project,
        "tenant_slug": ctx.tenant_slug,
        "agent": ctx.agent,
        "label": ctx.label,
        "is_system": ctx.is_system,
        "is_admin": ctx.is_admin,
        "active": True,
    }


def _verify_bearer(authorization: Optional[str]) -> dict[str, Any]:
    """Return the token record or raise 401.

    Thin wrapper delegating to sos.services.auth.verify_bearer.  Any call-site
    that missed this migration continues to work unchanged because the public
    function signature is preserved.  Internals no longer read tokens.json
    directly — all verification goes through the canonical auth module.
    """
    ctx = _auth_verify_bearer(authorization)
    if ctx is None:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        raise HTTPException(status_code=401, detail="invalid or inactive token")
    return _auth_ctx_to_entry(ctx)


def _resolve_tenant(entry: dict[str, Any]) -> str | None:
    """Extract tenant scope from a token record. Prefers `project` over `tenant`."""
    return entry.get("project") or entry.get("tenant") or None


class UsageEventRequest(BaseModel):
    """Incoming usage event. Mirrors `UsageEvent` but with explicit constraints."""
    tenant: str = Field(..., min_length=1, description="Tenant slug — must match the bearer token's scope.")
    provider: str = Field(..., min_length=1, description="Provider key, e.g. 'google', 'anthropic', 'openai'.")
    model: str = Field(..., min_length=1, description="Provider model id.")
    endpoint: str = Field("", description="Tenant-side endpoint that triggered the call.")
    input_tokens: int = Field(0, ge=0)
    output_tokens: int = Field(0, ge=0)
    image_count: int = Field(0, ge=0)
    cost_micros: int = Field(0, ge=0, description="Cost in integer micros (1e-6 currency unit).")
    cost_currency: str = Field("USD", min_length=1, description="'USD' | 'MIND' | operator-defined.")
    metadata: dict[str, Any] = Field(default_factory=dict)
    occurred_at: Optional[str] = Field(None, description="ISO 8601 UTC; server fills if omitted.")


class UsageEventResponse(BaseModel):
    id: str
    received_at: str


@app.post("/usage", response_model=UsageEventResponse, status_code=201)
async def ingest_usage(
    req: UsageEventRequest,
    authorization: Optional[str] = Header(None),
) -> UsageEventResponse:
    """Accept one usage event from a tenant. Tenant scope is enforced — a token
    scoped to tenant X cannot write events for tenant Y.

    System-scoped tokens (no `project`/`tenant` field) may write for any tenant
    — this is the admin/trusted-service path used by internal adapters.
    """
    entry = _verify_bearer(authorization)
    scope = _resolve_tenant(entry)

    if scope is not None and scope != req.tenant:
        raise HTTPException(
            status_code=403,
            detail=f"token is scoped to tenant '{scope}', cannot write events for '{req.tenant}'",
        )

    event = UsageEvent(
        tenant=req.tenant,
        provider=req.provider,
        model=req.model,
        endpoint=req.endpoint,
        input_tokens=req.input_tokens,
        output_tokens=req.output_tokens,
        image_count=req.image_count,
        cost_micros=req.cost_micros,
        cost_currency=req.cost_currency,
        metadata=req.metadata,
        occurred_at=req.occurred_at or "",
    )
    # Let UsageEvent fill occurred_at/received_at defaults if they were empty.
    if not event.occurred_at:
        event.occurred_at = event.received_at
    stored = _usage_log.append(event)
    log.info(
        "usage event ingested",
        id=stored.id,
        tenant=stored.tenant,
        provider=stored.provider,
        model=stored.model,
        cost_micros=stored.cost_micros,
    )
    return UsageEventResponse(id=stored.id, received_at=stored.received_at)


@app.get("/usage")
async def list_usage(
    tenant: Optional[str] = None,
    limit: int = 100,
    authorization: Optional[str] = Header(None),
) -> dict[str, Any]:
    """Read back usage events. Tenant-scoped unless the caller is system-scoped."""
    entry = _verify_bearer(authorization)
    scope = _resolve_tenant(entry)
    filter_tenant = tenant or scope
    if scope is not None and filter_tenant != scope:
        raise HTTPException(
            status_code=403,
            detail=f"token is scoped to tenant '{scope}', cannot read events for '{filter_tenant}'",
        )
    events = _usage_log.read_all(tenant=filter_tenant, limit=max(1, min(1000, limit)))
    return {"events": [e.to_dict() for e in events], "count": len(events)}


# ---------------------------------------------------------------------------
# Settlement retry endpoint (island #4)
# ---------------------------------------------------------------------------


class SettleResponse(BaseModel):
    usage_event_id: str
    settlement_status: str
    total_charged: int
    total_creator_credit: int
    total_platform_fee: int
    errors: list[str]


@app.post("/settle/{usage_event_id}", response_model=SettleResponse)
async def retry_settle(
    usage_event_id: str,
    authorization: Optional[str] = Header(None),
) -> SettleResponse:
    """Retry settlement for a deferred UsageEvent.  Admin-only.

    Looks up the event in the log by id, re-runs ``settle_usage_event``, and
    returns the result.  Does NOT rewrite the JSONL — callers can poll
    ``GET /usage`` to see the updated ``metadata.settlement_status``.
    """
    entry = _verify_bearer(authorization)
    # Admin check: is_admin (kasra/mumega) OR system-scoped token (no project)
    is_privileged = entry.get("is_admin") or entry.get("is_system") or (
        not entry.get("project") and not entry.get("tenant_slug")
    )
    if not is_privileged:
        raise HTTPException(status_code=403, detail="admin token required for settlement retry")

    # Find the event
    events = _usage_log.read_all()
    matches = [e for e in events if e.id == usage_event_id]
    if not matches:
        raise HTTPException(status_code=404, detail=f"usage event '{usage_event_id}' not found")

    event = matches[-1]  # take the last (most recent) entry for this id
    result: SettlementResult = await settle_usage_event(event, wallet)

    if result.settlement_status == "settled":
        # Patch event metadata and append corrected record to the log
        event.metadata["settlement_status"] = "settled"
        if result.outcomes:
            event.metadata["transaction_id"] = result.outcomes[0].transaction_id
        _usage_log._write_line(event)  # type: ignore[attr-defined]
        log.info("deferred settlement resolved via API", event_id=usage_event_id)

    return SettleResponse(
        usage_event_id=result.usage_event_id,
        settlement_status=result.settlement_status,
        total_charged=result.total_charged,
        total_creator_credit=result.total_creator_credit,
        total_platform_fee=result.total_platform_fee,
        errors=result.errors,
    )