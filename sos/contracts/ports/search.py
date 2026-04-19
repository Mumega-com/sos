"""SearchPort — full-text and vector search over tenant content.

Canonical contract shared between SOS (Python) and Inkwell (TypeScript).
Source of truth for how plugins index documents and run ranked search.

Tenant binding: EXPLICIT tenant_id on every method. Tenants MUST NOT see
each other's search results, and adapters use tenant_id to scope the
index partition.

Divergence from Inkwell
-----------------------
Inkwell's SearchPort has no tenant_id — it relies on the adapter being
bound per tenant. We tightened this to match the SOS Mothership pattern
where one platform-level SearchPort instance may fan out across many
tenants. The extra parameter is cheap; the safety is not.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

# --- Request / response models ---------------------------------------------


class SearchDoc(BaseModel):
    """Input document for indexing."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    content: str
    metadata: Optional[dict[str, Any]] = None


class IndexRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    doc: SearchDoc


class SearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    query: str = Field(min_length=1)
    limit: Optional[int] = Field(default=None, ge=1, le=1000)


class SearchHit(BaseModel):
    """One ranked result."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    score: float = Field(ge=0.0)
    metadata: Optional[dict[str, Any]] = None


class DeleteSearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    doc_id: str = Field(min_length=1)


# --- Port protocol ----------------------------------------------------------


@runtime_checkable
class SearchPort(Protocol):
    """Search index — tenant-scoped indexing and ranked query."""

    async def index(self, req: IndexRequest) -> None:
        """Upsert a document into the tenant's index."""
        ...

    async def search(self, req: SearchRequest) -> list[SearchHit]:
        """Ranked results for `query`, limited to the tenant's documents."""
        ...

    async def delete(self, req: DeleteSearchRequest) -> None:
        """Remove a document by ID."""
        ...


__all__ = [
    "SearchDoc",
    "IndexRequest",
    "SearchRequest",
    "SearchHit",
    "DeleteSearchRequest",
    "SearchPort",
]
