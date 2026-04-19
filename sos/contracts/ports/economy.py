"""EconomyPort — tenant-scoped ledger, usage metering, and transfers.

Canonical contract shared between SOS (Python) and Inkwell (TypeScript).
Source of truth for how plugins and agents talk to the economy service.

Tenant binding: EXPLICIT tenant_id on every method. Inkwell v6.3 widened
these signatures to take tenantId so platform-level billing can fan out
across tenants without thread-local context.

Amounts are integers. Callers pass the smallest unit of whatever currency
the tenant uses (cents for USD ledgers, micros for metered model-call
ledgers). Currency is reported back on responses so callers can verify.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

# --- Request / response models ---------------------------------------------


class RecordUsageRequest(BaseModel):
    """One metered-usage event for a tenant (credits, tokens, etc.)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str = Field(min_length=1)
    type: str = Field(description="Usage category: 'tokens', 'api_call', 'storage_gb_day', ...")
    amount: int = Field(description="Integer amount in the smallest unit for `type`.")


class GetBalanceRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str = Field(min_length=1)


class Balance(BaseModel):
    """Current balance snapshot for a tenant."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    balance: int
    currency: str = "USD"


class ChargeRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str = Field(min_length=1)
    amount: int = Field(gt=0)
    reason: str = Field(max_length=280)


class TransferRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    from_tenant: str = Field(min_length=1)
    to_tenant: str = Field(min_length=1)
    amount: int = Field(gt=0)
    reason: str = Field(max_length=280)


class ChargeResult(BaseModel):
    """Outcome of a charge or transfer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    charged: bool
    tx_id: str
    remaining_balance: int
    reason: Optional[str] = Field(
        default=None,
        description="Populated when charged=False (e.g. 'insufficient funds').",
    )


# --- Port protocol ----------------------------------------------------------


@runtime_checkable
class EconomyPort(Protocol):
    """Tenant-scoped economy service — metering, balances, charges, transfers."""

    async def record_usage(self, req: RecordUsageRequest) -> None:
        """Append a usage event. Fire-and-forget; no response body."""
        ...

    async def get_balance(self, req: GetBalanceRequest) -> Balance:
        """Current balance for `tenant_id`."""
        ...

    async def charge(self, req: ChargeRequest) -> ChargeResult:
        """Debit a tenant. Returns charged=False if the balance would go negative."""
        ...

    async def transfer(self, req: TransferRequest) -> ChargeResult:
        """Move funds between two tenants atomically."""
        ...


__all__ = [
    "RecordUsageRequest",
    "GetBalanceRequest",
    "Balance",
    "ChargeRequest",
    "TransferRequest",
    "ChargeResult",
    "EconomyPort",
]
