"""ContentSourcePort — pull-based content ingestion (Obsidian, Notion, Drive, GitHub).

Canonical contract shared between SOS (Python) and Inkwell (TypeScript).
Source of truth for the /api/ingest pipeline — each adapter pulls from
one external system (a vault, a Notion database, a Drive folder, a repo).

Tenant binding: IMPLICIT. A ContentSourcePort instance is bound to one
tenant's source configuration at adapter construction. Platform jobs that
fan out across many tenants instantiate one port per tenant.

Introduced in Inkwell v7.1; kept in sync here.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

# --- Request / response models ---------------------------------------------


class ContentSourceItem(BaseModel):
    """One piece of pulled content — directly mirrors Inkwell's shape."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: str = Field(description="URL-safe identifier derived from the source.")
    title: str
    content: str = Field(description="Raw markdown or MDX.")
    updated_at: str = Field(description="ISO-8601 last-modified in the source.")
    metadata: Optional[dict[str, Any]] = Field(
        default=None,
        description="Source-specific metadata (file path, notion page id, ...).",
    )


class ListRequest(BaseModel):
    """No parameters today — kept as a model for JSON-Schema parity."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class SyncRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    since: Optional[str] = Field(
        default=None,
        description="ISO-8601 — only return items modified after this. None = full sync.",
    )


# --- Port protocol ----------------------------------------------------------


@runtime_checkable
class ContentSourcePort(Protocol):
    """One external content source. Tenant bound by adapter wiring."""

    # Port-level identity — human-readable source type ('obsidian', 'notion',
    # 'gdrive', 'github'). Represented as a method so the Protocol check works
    # under runtime_checkable; adapters may implement it as a property.
    @property
    def name(self) -> str:
        """Short slug identifying the source type."""
        ...

    async def list(self, req: ListRequest) -> list[ContentSourceItem]:
        """All items currently available from the source."""
        ...

    async def sync(self, req: SyncRequest) -> list[ContentSourceItem]:
        """Items changed since `req.since`. If None, same as list()."""
        ...


__all__ = [
    "ContentSourceItem",
    "ListRequest",
    "SyncRequest",
    "ContentSourcePort",
]
