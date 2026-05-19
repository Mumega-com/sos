"""SessionPort — session / KV storage (KV, Redis, DynamoDB, Firestore).

Canonical contract shared between SOS (Python) and Inkwell (TypeScript).
Source of truth for how plugins persist short-lived keyed state.

Tenant binding: IMPLICIT. Namespace is applied at adapter construction so
callers can't accidentally read another tenant's session. The richer
sos.contracts.storage.KVStore is available for kernel-internal use.

Values are strings — match Inkwell's SessionPort exactly. If you need to
store structured data, JSON-encode at the call site.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

# --- Request / response models ---------------------------------------------


class SessionGetRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(min_length=1)


class SessionPutRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(min_length=1)
    value: str
    ttl_seconds: Optional[int] = Field(
        default=None, ge=1, description="Expire after N seconds — None = no TTL."
    )


class SessionDeleteRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(min_length=1)


# --- Port protocol ----------------------------------------------------------


@runtime_checkable
class SessionPort(Protocol):
    """Keyed session/string store. Tenant bound by adapter wiring."""

    async def get(self, req: SessionGetRequest) -> Optional[str]:
        """Fetch value for key — None if missing or expired."""
        ...

    async def put(self, req: SessionPutRequest) -> None:
        """Store string value with optional TTL."""
        ...

    async def delete(self, req: SessionDeleteRequest) -> None:
        """Drop a key. Idempotent."""
        ...


__all__ = [
    "SessionGetRequest",
    "SessionPutRequest",
    "SessionDeleteRequest",
    "SessionPort",
]
