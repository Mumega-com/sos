"""MediaPort — AI-powered media pipeline for images and video.

Mirrors Inkwell v7.1's MediaPort (kernel/types.ts:380-430). Handles upload,
AI analysis (vision + transcription), transforms, image generation, and
search. Assets become knowledge graph nodes.

Tenant binding: EXPLICIT via optional `tenant` field — an asset can be
tenant-scoped or cross-tenant (shared library). Adapters enforce access
checks at call time.
"""
from __future__ import annotations

from typing import Literal, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


# --- Data models -----------------------------------------------------------


class MediaChapter(BaseModel):
    """Auto-generated chapter marker inside a video/audio transcript."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    time: float = Field(description="Seconds from start of asset")
    title: str


class MediaAsset(BaseModel):
    """A media asset stored and analyzed by Inkwell."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    tenant: Optional[str] = None
    filename: str
    content_type: str = Field(alias="contentType")
    r2_key: str = Field(alias="r2Key")
    width: Optional[int] = None
    height: Optional[int] = None
    size_bytes: int = Field(alias="sizeBytes")
    alt_text: Optional[str] = Field(default=None, alias="altText")
    description: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    thumbhash: Optional[str] = None
    nsfw_score: Optional[float] = Field(default=None, alias="nsfwScore")
    transcript: Optional[str] = None
    chapters: Optional[list[MediaChapter]] = None
    variants: dict[str, str] = Field(
        default_factory=dict,
        description="Variant name → URL (thumbnail, hero, og, ...)",
    )
    graph_slug: Optional[str] = Field(default=None, alias="graphSlug")
    source_type: Literal["upload", "generate", "import"] = Field(alias="sourceType")
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


# --- Request / response models --------------------------------------------


class MediaUploadRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    file: bytes = Field(description="Raw file bytes — adapter pushes to R2")
    filename: str
    content_type: str = Field(alias="contentType")
    tenant: Optional[str] = None


class MediaGetRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str


class MediaDescribeResult(BaseModel):
    """Vision-model analysis output for an image asset."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    alt_text: str = Field(alias="altText")
    description: str
    tags: list[str]
    nsfw_score: float = Field(alias="nsfwScore")


class MediaTranscribeResult(BaseModel):
    """Transcription + chapter output for a video/audio asset."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    transcript: str
    chapters: list[MediaChapter]


class MediaTransformRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    variant: str = Field(description="Variant name e.g. 'thumbnail', 'hero', 'og'")


class MediaSearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str = Field(min_length=1)
    tenant: Optional[str] = None
    limit: Optional[int] = Field(default=None, ge=1, le=1000)


class MediaListRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant: Optional[str] = None
    cursor: Optional[str] = None
    limit: Optional[int] = Field(default=None, ge=1, le=1000)


class MediaListResult(BaseModel):
    """Cursor-paginated list page."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assets: list[MediaAsset]
    cursor: Optional[str] = None


class MediaDeleteRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str


class MediaGenerateImageRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    prompt: str = Field(min_length=1)
    tenant: Optional[str] = None


# --- Port protocol ---------------------------------------------------------


@runtime_checkable
class MediaPort(Protocol):
    """AI-powered media pipeline. Tenant scope via request field."""

    async def upload(self, req: MediaUploadRequest) -> MediaAsset:
        """Upload a file, store in R2, run AI analysis, return enriched asset."""
        ...

    async def get(self, req: MediaGetRequest) -> Optional[MediaAsset]:
        """Fetch asset metadata by ID. Returns None if not found."""
        ...

    async def describe(self, req: MediaGetRequest) -> MediaDescribeResult:
        """Run vision analysis on an image — alt text, description, tags, NSFW."""
        ...

    async def transcribe(self, req: MediaGetRequest) -> MediaTranscribeResult:
        """Transcribe a video/audio asset with auto-generated chapters."""
        ...

    async def transform(self, req: MediaTransformRequest) -> str:
        """Return a transformed variant URL (thumbnail, hero, og, ...)."""
        ...

    async def search(self, req: MediaSearchRequest) -> list[MediaAsset]:
        """Search assets by text query (alt text, description, tags, transcript)."""
        ...

    async def list(self, req: MediaListRequest) -> MediaListResult:
        """Cursor-paginated list of assets."""
        ...

    async def delete(self, req: MediaDeleteRequest) -> None:
        """Delete asset + underlying R2 object."""
        ...

    async def generate_image(self, req: MediaGenerateImageRequest) -> MediaAsset:
        """Generate an image from a text prompt via Workers AI."""
        ...


__all__ = [
    "MediaChapter",
    "MediaAsset",
    "MediaUploadRequest",
    "MediaGetRequest",
    "MediaDescribeResult",
    "MediaTranscribeResult",
    "MediaTransformRequest",
    "MediaSearchRequest",
    "MediaListRequest",
    "MediaListResult",
    "MediaDeleteRequest",
    "MediaGenerateImageRequest",
    "MediaPort",
]
