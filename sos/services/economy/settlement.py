"""Settlement helper — converts a UsageEvent into canonical Economy Transactions.

Emits up to three Transactions for every chargeable UsageEvent:
  1. USAGE_CHARGE  — debit the tenant's wallet by ``cost_micros`` (microMIND).
  2. SKILL_PAYOUT  — credit the skill author 85% when a seller_skill / ai_to_ai
                      commerce flag is present in the event metadata.
  3. PLATFORM_FEE  — credit ``agent:treasury`` with the remaining 15%.

When ``cost_micros == 0`` or ``cost_currency != "MIND"`` the event is free to
settle trivially (no wallet ops, status "settled").

Settlement is **best-effort**: wallet failures (InsufficientFundsError or any
other exception) do not raise — they return a SettlementResult with
``settlement_status = "deferred"`` so the caller can tag the UsageEvent and
move on without blocking the append-only log.

microMIND is the only ledger unit (1 MIND = 1_000_000 microMIND). No floats.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from sos.contracts.economy import Transaction, TransactionStatus, TransactionType
from sos.observability.logging import get_logger

log = get_logger("economy_settlement")

# 85/15 split — 85% creator, 15% platform treasury
CREATOR_SHARE_BPS = 8500   # basis points out of 10_000
PLATFORM_SHARE_BPS = 1500  # basis points out of 10_000
TREASURY_AGENT = "agent:treasury"


def _bps(amount: int, bps: int) -> int:
    """Integer basis-point calculation. Floor division — no floats."""
    return (amount * bps) // 10_000


@dataclass
class WalletOutcome:
    """Result of a single wallet debit or credit operation."""
    agent: str
    amount: int  # microMIND
    tx_type: str
    transaction_id: Optional[str] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class SettlementResult:
    """Aggregated outcome of settling one UsageEvent."""
    usage_event_id: str
    settlement_status: str  # "settled" | "deferred" | "skipped"
    outcomes: list[WalletOutcome] = field(default_factory=list)
    total_charged: int = 0        # microMIND debited from buyer
    total_creator_credit: int = 0 # microMIND credited to author
    total_platform_fee: int = 0   # microMIND credited to treasury
    errors: list[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


def _make_tx(
    tx_type: TransactionType,
    from_agent: str,
    to_agent: str,
    amount: int,
    reason: str,
    usage_event_id: str,
    extra_meta: Optional[dict[str, Any]] = None,
) -> Transaction:
    meta = {"usage_event_id": usage_event_id}
    if extra_meta:
        meta.update(extra_meta)
    return Transaction(
        id=str(uuid.uuid4()),
        tx_type=tx_type,
        from_agent=from_agent,
        to_agent=to_agent,
        amount=amount,
        currency="MIND",
        status=TransactionStatus.SETTLED,
        reason=reason,
        created_at=datetime.now(timezone.utc),
        settled_at=datetime.now(timezone.utc),
        metadata=meta,
    )


async def settle_usage_event(
    event: Any,  # UsageEvent — imported lazily to avoid circular deps
    wallet: Any,  # SovereignWallet
) -> SettlementResult:
    """Settle one UsageEvent against the SovereignWallet.

    Parameters
    ----------
    event:
        A ``UsageEvent`` dataclass instance (from ``sos.services.economy.usage_log``).
    wallet:
        A ``SovereignWallet`` instance (from ``sos.services.economy.wallet``).

    Returns
    -------
    SettlementResult
        Always returns — never raises.  Check ``.settlement_status`` and
        ``.has_errors`` on the result.
    """
    result = SettlementResult(usage_event_id=event.id, settlement_status="skipped")

    # Only settle MIND-denominated chargeable events
    if event.cost_micros <= 0 or event.cost_currency.upper() != "MIND":
        result.settlement_status = "skipped"
        return result

    amount: int = event.cost_micros
    tenant_agent = f"tenant:{event.tenant}" if event.tenant else "tenant:unknown"

    # ------------------------------------------------------------------ #
    # Step 1: debit the buyer
    # ------------------------------------------------------------------ #
    debit_outcome = WalletOutcome(agent=tenant_agent, amount=amount, tx_type="usage_charge")
    try:
        await wallet.debit(tenant_agent, amount, reason=f"usage_charge:{event.id}")
        tx = _make_tx(
            TransactionType.USAGE_CHARGE,
            from_agent=tenant_agent,
            to_agent=TREASURY_AGENT,
            amount=amount,
            reason=f"UsageEvent {event.id}: {event.provider}/{event.model}",
            usage_event_id=event.id,
        )
        debit_outcome.transaction_id = tx.id
        result.total_charged = amount
        log.info(
            "usage_charge debited",
            tenant=event.tenant,
            amount=amount,
            event_id=event.id,
        )
    except Exception as exc:  # noqa: BLE001
        debit_outcome.error = str(exc)
        result.errors.append(f"debit failed: {exc}")
        result.outcomes.append(debit_outcome)
        result.settlement_status = "deferred"
        return result

    result.outcomes.append(debit_outcome)

    # ------------------------------------------------------------------ #
    # Step 2: creator split (85%) — only when skill metadata present
    # ------------------------------------------------------------------ #
    seller_skill: str | None = event.metadata.get("seller_skill")
    ai_to_ai: bool = bool(event.metadata.get("ai_to_ai_commerce"))
    author_agent: str | None = event.metadata.get("author_agent")

    if (seller_skill or ai_to_ai) and author_agent:
        creator_amount = _bps(amount, CREATOR_SHARE_BPS)
        platform_amount = amount - creator_amount  # exact complement → no rounding loss

        # Credit author
        creator_outcome = WalletOutcome(
            agent=author_agent, amount=creator_amount, tx_type="skill_payout"
        )
        try:
            await wallet.credit(author_agent, creator_amount, reason=f"skill_payout:{event.id}")
            tx_creator = _make_tx(
                TransactionType.SKILL_PAYOUT,
                from_agent=TREASURY_AGENT,
                to_agent=author_agent,
                amount=creator_amount,
                reason=f"85% creator share for UsageEvent {event.id}",
                usage_event_id=event.id,
                extra_meta={"seller_skill": seller_skill, "ai_to_ai_commerce": ai_to_ai},
            )
            creator_outcome.transaction_id = tx_creator.id
            result.total_creator_credit = creator_amount
            log.info(
                "skill_payout credited",
                author=author_agent,
                amount=creator_amount,
                event_id=event.id,
            )
        except Exception as exc:  # noqa: BLE001
            creator_outcome.error = str(exc)
            result.errors.append(f"creator credit failed: {exc}")

        result.outcomes.append(creator_outcome)

        # Credit treasury (platform fee)
        fee_outcome = WalletOutcome(
            agent=TREASURY_AGENT, amount=platform_amount, tx_type="platform_fee"
        )
        try:
            await wallet.credit(TREASURY_AGENT, platform_amount, reason=f"platform_fee:{event.id}")
            tx_fee = _make_tx(
                TransactionType.PLATFORM_FEE,
                from_agent=tenant_agent,
                to_agent=TREASURY_AGENT,
                amount=platform_amount,
                reason=f"15% platform fee for UsageEvent {event.id}",
                usage_event_id=event.id,
            )
            fee_outcome.transaction_id = tx_fee.id
            result.total_platform_fee = platform_amount
            log.info(
                "platform_fee credited",
                amount=platform_amount,
                event_id=event.id,
            )
        except Exception as exc:  # noqa: BLE001
            fee_outcome.error = str(exc)
            result.errors.append(f"platform fee credit failed: {exc}")

        result.outcomes.append(fee_outcome)

    else:
        # No creator split — full amount goes to treasury as platform fee
        fee_outcome = WalletOutcome(
            agent=TREASURY_AGENT, amount=amount, tx_type="platform_fee"
        )
        try:
            await wallet.credit(TREASURY_AGENT, amount, reason=f"platform_fee:{event.id}")
            tx_fee = _make_tx(
                TransactionType.PLATFORM_FEE,
                from_agent=tenant_agent,
                to_agent=TREASURY_AGENT,
                amount=amount,
                reason=f"Platform fee (no seller) for UsageEvent {event.id}",
                usage_event_id=event.id,
            )
            fee_outcome.transaction_id = tx_fee.id
            result.total_platform_fee = amount
        except Exception as exc:  # noqa: BLE001
            fee_outcome.error = str(exc)
            result.errors.append(f"platform fee credit failed: {exc}")

        result.outcomes.append(fee_outcome)

    result.settlement_status = "deferred" if result.errors else "settled"
    return result


async def retry_deferred_settlements(
    log_path: Any,
    wallet: Any,
    tenant: str | None = None,
    limit: int = 100,
) -> list[SettlementResult]:
    """Re-attempt settlement for events tagged ``settlement_status=deferred``.

    Reads the JSONL log, finds deferred events, and calls
    ``settle_usage_event`` again.  Successful events are NOT rewritten (the
    JSONL is append-only); callers should use the returned SettlementResult
    list for reporting.

    Parameters
    ----------
    log_path:
        A ``UsageLog`` instance or compatible object with ``read_all()``.
    wallet:
        A ``SovereignWallet`` instance.
    tenant:
        Optional tenant filter.
    limit:
        Maximum deferred events to retry in one pass.

    Returns
    -------
    list[SettlementResult]
    """
    from sos.services.economy.usage_log import UsageLog

    if isinstance(log_path, UsageLog):
        usage_log = log_path
    else:
        usage_log = UsageLog(path=log_path)

    events = usage_log.read_all(tenant=tenant)
    deferred = [
        e for e in events
        if e.metadata.get("settlement_status") == "deferred"
    ][:limit]

    results: list[SettlementResult] = []
    for event in deferred:
        result = await settle_usage_event(event, wallet)
        results.append(result)
        if result.settlement_status == "settled":
            log.info("deferred settlement resolved", event_id=event.id)

    return results
