"""MemoryPort — engram store, semantic recall, filtered search.

Canonical contract shared between SOS (Python) and Inkwell (TypeScript).
Source of truth for the memory surface plugins talk to.

Tenant binding: IMPLICIT. The caller's session/bus-token scopes reads and
writes. Inkwell's MemoryPort follows the same convention — signatures don't
carry tenantId. A deeper, tenant-aware memory API lives in sos.contracts.memory
(MemoryContract.store(..., capability=...)) for kernel-internal use.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


# --- Request / response models ---------------------------------------------


class RememberRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    content: str = Field(min_length=1, max_length=32768)
    metadata: Optional[dict[str, Any]] = None


class RememberResult(BaseModel):
    """Returned from remember() — the stored engram's ID."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    memory_id: str


class RecallRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str = Field(min_length=1, max_length=2048)
    limit: Optional[int] = Field(default=None, ge=1, le=1000)


class SearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str = Field(min_length=1, max_length=2048)
    filters: Optional[dict[str, Any]] = Field(
        default=None,
        description="Arbitrary metadata filters — backend-dependent semantics.",
    )


class MemoryResult(BaseModel):
    """One recall/search hit. Mirrors Inkwell's MemoryResult."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    content: str
    metadata: Optional[dict[str, Any]] = None
    score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Similarity / relevance score when available.",
    )
    created_at: str = Field(description="ISO-8601 timestamp")


# --- Port protocol ----------------------------------------------------------


@runtime_checkable
class MemoryPort(Protocol):
    """Engram store / recall / search. Tenant bound via caller context."""

    async def remember(self, req: RememberRequest) -> RememberResult:
        """Persist `req.content` and return its assigned memory ID."""
        ...

    async def recall(self, req: RecallRequest) -> list[MemoryResult]:
        """Semantic recall — nearest-neighbor search over embeddings."""
        ...

    async def search(self, req: SearchRequest) -> list[MemoryResult]:
        """Filtered search — combines semantic and metadata predicates."""
        ...


__all__ = [
    "RememberRequest",
    "RememberResult",
    "RecallRequest",
    "SearchRequest",
    "MemoryResult",
    "MemoryPort",
]
