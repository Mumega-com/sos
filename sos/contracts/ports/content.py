"""ContentPort — pre-rendered content cache (KV, S3, GCS, Firestore).

Canonical contract shared between SOS (Python) and Inkwell (TypeScript).
Source of truth for how plugins serve already-rendered HTML pages per
tenant without re-running the render pipeline.

Tenant binding: EXPLICIT tenant_id on every method. Pre-rendered content
is tenant-owned — a cache hit from tenant A must never serve tenant B.

Divergence from Inkwell
-----------------------
Inkwell's ContentPort has getPage/putPage/listPages and no tenantId. We
tightened both:
  1. tenant_id on every method (matches the SOS Mothership convention).
  2. added invalidate(path=None) — Inkwell currently has no explicit
     invalidation primitive; adapters roll their own. We lift it into
     the port because every real CDN layer needs it.
  3. renamed `key` → `path` to reflect that this is URL-path content,
     not arbitrary KV (we already have SessionPort for that).

Inkwell's listPages is NOT mirrored here — that pattern belongs on
StoragePort. Content is path-addressed; callers know their routes.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

# --- Request / response models ---------------------------------------------


class ContentGetRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    path: str = Field(min_length=1, description="URL path, e.g. '/about'.")


class ContentGetResult(BaseModel):
    """Rendered HTML + cache metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    html: str
    cached_at: Optional[str] = Field(
        default=None, description="ISO-8601 when the entry was written."
    )
    expires_at: Optional[str] = Field(default=None, description="ISO-8601 when the entry expires.")


class ContentPutRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    path: str = Field(min_length=1)
    html: str
    ttl_seconds: Optional[int] = Field(default=None, ge=1, description="None = cache indefinitely.")


class ContentInvalidateRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    path: Optional[str] = Field(
        default=None,
        description="None = invalidate ALL pages for the tenant; "
        "otherwise invalidate only that path.",
    )


# --- Port protocol ----------------------------------------------------------


@runtime_checkable
class ContentPort(Protocol):
    """Pre-rendered page cache — tenant-scoped."""

    async def get(self, req: ContentGetRequest) -> Optional[ContentGetResult]:
        """Fetch cached HTML — None on miss."""
        ...

    async def put(self, req: ContentPutRequest) -> None:
        """Store rendered HTML with optional TTL."""
        ...

    async def invalidate(self, req: ContentInvalidateRequest) -> None:
        """Drop one path or the whole tenant namespace."""
        ...


__all__ = [
    "ContentGetRequest",
    "ContentGetResult",
    "ContentPutRequest",
    "ContentInvalidateRequest",
    "ContentPort",
]
