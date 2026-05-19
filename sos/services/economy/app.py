from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from sos import __version__
from sos.contracts.policy import PolicyDecision
from sos.kernel.policy.gate import can_execute
from sos.observability.logging import get_logger
from sos.services.economy.wallet import SovereignWallet, InsufficientFundsError
from sos.services.economy.usage_log import UsageEvent, UsageLog
from sos.services.economy.settlement import settle_usage_event, SettlementResult
from sos.services.economy.shelf import router as shelf_router
from sos.kernel.health import health_response
from sos.kernel.telemetry import init_tracing, instrument_fastapi

SERVICE_NAME = "economy"
_START_TIME = time.time()

log = get_logger(SERVICE_NAME, min_level=os.getenv("SOS_LOG_LEVEL", "info"))

wallet = SovereignWallet()
_usage_log = UsageLog(wallet=wallet)

init_tracing("economy")

app = FastAPI(title="SOS Economy Service", version=__version__)
instrument_fastapi(app)
app.include_router(shelf_router)

# CORS for desktop/mobile apps
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Gate helper — turn a PolicyDecision into the appropriate HTTP response
# ---------------------------------------------------------------------------


def _raise_on_deny(decision: PolicyDecision, *, require_system: bool = False) -> None:
    """Map a gate decision to 401/403 if denied.

    When ``require_system`` is True, also enforce that the successful
    decision came via system/admin scope — the gate allows tenant-scoped
    callers into their own tenant, but admin-only routes require a system token.
    """
    if not decision.allowed:
        reason = decision.reason or "unauthorized"
        if "bearer" in reason.lower() or "auth" in reason.lower():
            raise HTTPException(status_code=401, detail=reason)
        raise HTTPException(status_code=403, detail=reason)

    if require_system:
        pillars = set(decision.pillars_passed)
        # system/admin callers never get 'tenant_scope' added because the
        # gate short-circuits with 'system/admin scope' reason. Check that.
        if "system/admin" not in decision.reason:
            raise HTTPException(
                status_code=403,
                detail="oauth callbacks require system or admin scope",
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
async def credit(
    req: TransactionRequest,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    from sos.kernel.idempotency import with_idempotency

    async def _do() -> dict:
        try:
            new_balance = await wallet.credit(req.user_id, req.amount, req.reason)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return BalanceResponse(user_id=req.user_id, balance=new_balance).model_dump()

    return await with_idempotency(
        key=idempotency_key,
        tenant=req.user_id,
        request_body=req.model_dump(),
        fn=_do,
    )

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
async def debit(
    req: TransactionRequest,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    from sos.kernel.idempotency import with_idempotency

    async def _do() -> dict:
        try:
            new_balance = await wallet.debit(req.user_id, req.amount, req.reason)
        except InsufficientFundsError as e:
            raise HTTPException(status_code=402, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return BalanceResponse(user_id=req.user_id, balance=new_balance).model_dump()

    return await with_idempotency(
        key=idempotency_key,
        tenant=req.user_id,
        request_body=req.model_dump(),
        fn=_do,
    )


# ---------------------------------------------------------------------------
# Budget pre-action gate (v0.5.0 — closes kernel→services leak)
# ---------------------------------------------------------------------------
#
# Kernel governance consults this before allowing a governed action.
# Delegates unchanged to sos.services.economy.metabolism.can_spend so the
# business logic (SQLite, allocation, digest cycle) stays inside the
# economy service. Kernel never imports this module — it calls through
# sos.clients.economy.EconomyClient.can_spend(project, cost).


class CanSpendResponse(BaseModel):
    allowed: bool
    budget: float
    spent: float
    remaining: float
    pct_used: float
    reason: str
    warning: Optional[str] = None


@app.get("/budget/can-spend", response_model=CanSpendResponse)
async def budget_can_spend(
    project: str,
    cost: float = 0.0,
    authorization: Optional[str] = Header(None),
) -> CanSpendResponse:
    """Check whether a project has budget headroom for an action.

    Returns the full metabolism.can_spend contract unchanged. Tenant scope
    is enforced — a token scoped to tenant X cannot peek at tenant Y's
    budget. System-scoped tokens (kernel governance) may query any project.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")
    decision = await can_execute(
        action="budget_read",
        resource=project,
        tenant=project,
        authorization=authorization,
    )
    _raise_on_deny(decision)
    from sos.services.economy.metabolism import can_spend
    result = can_spend(project, cost)
    return CanSpendResponse(**result)


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
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")
    decision = await can_execute(
        action="usage_record",
        resource=req.tenant,
        tenant=req.tenant,
        authorization=authorization,
    )
    _raise_on_deny(decision)

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
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")
    # When no tenant filter is given, default the gate check to the caller's
    # own project so project-scoped tokens can read their own events. System
    # tokens without a project fall back to "mumega".
    from sos.kernel.auth import verify_bearer as _verify
    caller_ctx = _verify(authorization)
    caller_scope = (caller_ctx.project or caller_ctx.tenant_slug) if caller_ctx else None
    gate_tenant = tenant or caller_scope or "mumega"
    decision = await can_execute(
        action="usage_read",
        resource=gate_tenant,
        tenant=gate_tenant,
        authorization=authorization,
    )
    _raise_on_deny(decision)
    # Project-scoped callers without an explicit filter see only their own
    # events. System/admin callers without a filter see everything.
    is_privileged = bool(caller_ctx and (caller_ctx.is_system or caller_ctx.is_admin))
    filter_tenant = tenant if tenant is not None else (None if is_privileged else caller_scope)
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


# ---------------------------------------------------------------------------
# qNFT seat tokens (Phase 5 §5.4)
# ---------------------------------------------------------------------------
#
# POST /qnft/mint  — debit tenant wallet and record a seat token.
# GET  /qnft/{tenant} — list all seat tokens for a tenant.
#
# Storage: JSON list in Redis at sos:qnft:{tenant} via _qnft_store.
# Idempotency: header-keyed, matching /debit pattern.
# Auth: require_system=True for mint (economy write); tenant-scoped read for list.

import os as _os
import uuid as _uuid
from datetime import datetime, timezone

from sos.contracts.qnft import QNFT, QNFTMintRequest
from sos.services.economy._qnft_store import append_qnft, list_qnfts

_DEFAULT_QNFT_SEAT_COST = int(_os.environ.get("MUMEGA_QNFT_SEAT_COST_MIND", "100"))


@app.post("/qnft/mint", response_model=QNFT)
async def mint_qnft(
    req: QNFTMintRequest,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    authorization: Optional[str] = Header(None),
) -> QNFT:
    """Debit the tenant wallet and issue a qNFT seat token.

    System/admin scope required — CLI init calls this; tenants cannot
    self-mint seats to avoid free-credit exploits.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")
    decision = await can_execute(
        action="economy:qnft_mint",
        resource=req.tenant,
        tenant=req.tenant,
        authorization=authorization,
    )
    _raise_on_deny(decision, require_system=True)

    cost = req.cost_mind if req.cost_mind is not None else _DEFAULT_QNFT_SEAT_COST

    from sos.kernel.idempotency import with_idempotency

    async def _do() -> dict:
        try:
            await wallet.debit(req.tenant, cost, reason=f"qnft:mint:{req.role}:{req.seat_id}")
        except InsufficientFundsError as exc:
            raise HTTPException(status_code=402, detail=str(exc))

        token = QNFT(
            token_id=str(_uuid.uuid4()),
            tenant=req.tenant,
            squad_id=req.squad_id,
            role=req.role,
            seat_id=req.seat_id,
            mint_cost_mind=cost,
            minted_at=datetime.now(timezone.utc),
            project=req.project,
        )
        token_dict = token.model_dump(mode="json")
        await append_qnft(token_dict)
        log.info(
            "qnft minted",
            token_id=token.token_id,
            tenant=req.tenant,
            role=req.role,
            cost=cost,
        )
        return token_dict

    return await with_idempotency(
        key=idempotency_key,
        tenant=req.tenant,
        request_body=req.model_dump(),
        fn=_do,
    )


@app.get("/qnft/{tenant}")
async def get_qnfts(
    tenant: str,
    authorization: Optional[str] = Header(None),
) -> dict[str, Any]:
    """List all qNFT seat tokens minted for a tenant."""
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")
    decision = await can_execute(
        action="economy:qnft_read",
        resource=tenant,
        tenant=tenant,
        authorization=authorization,
    )
    _raise_on_deny(decision)
    tokens = await list_qnfts(tenant)
    return {"tenant": tenant, "tokens": tokens, "count": len(tokens)}


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
    if not authorization:
        raise HTTPException(status_code=401, detail="missing bearer token")
    decision = await can_execute(
        action="settlement_retry",
        resource=usage_event_id,
        tenant="mumega",
        authorization=authorization,
    )
    _raise_on_deny(decision, require_system=True)

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