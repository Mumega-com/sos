"""qNFT contract — seat token minted when a tenant hires a squad role.

Design call: qNFT seats vs AgentCard writes
--------------------------------------------
A real AgentCard requires an agent identity (keypair, DID, etc.) that does
not exist at tenant-creation time. Rather than write placeholder cards into
the registry (which would poison the card index with fake identities), Step C
mints lightweight *seat tokens* instead. Each seat carries {tenant, squad_id,
role, seat_id} metadata. When a real agent later calls POST /mesh/enroll, it
claims a seat by matching role + tenant and the seat qNFT is updated with
claimed_by/claimed_at. This keeps the registry clean and defers identity
binding to the moment an actual agent joins.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class QNFT(BaseModel):
    """A qNFT seat token — one per squad role reserved for a tenant."""

    model_config = ConfigDict(extra="forbid")

    token_id: str = Field(description="UUIDv4 generated server-side at mint time.")
    tenant: str
    squad_id: str = Field(description="Usually {tenant}-squad-{role}; caller may override.")
    role: str = Field(description="Default role name or free-form for custom hires.")
    seat_id: str = Field(description="{tenant}:seat:{role} by convention.")
    mint_cost_mind: int = Field(ge=0, description="$MIND debited at mint time.")
    minted_at: datetime
    claimed_by: Optional[str] = Field(
        None, description="agent_id once claimed via /mesh/enroll."
    )
    claimed_at: Optional[datetime] = None
    project: Optional[str] = Field(None, description="Tenant project scope.")


class QNFTMintRequest(BaseModel):
    """Request body for POST /qnft/mint."""

    model_config = ConfigDict(extra="forbid")

    tenant: str
    squad_id: str
    role: str
    seat_id: str
    cost_mind: Optional[int] = Field(
        None, ge=0, description="Override default seat cost. Service uses env default if None."
    )
    project: Optional[str] = None


__all__ = ["QNFT", "QNFTMintRequest"]
