"""BrandVector + Dossier — growth-intel squad outputs (Phase 7)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BrandVector(BaseModel):
    """Synthesized positioning signal for a tenant."""

    model_config = ConfigDict(extra="forbid")

    tenant: str = Field(min_length=1)
    computed_at: datetime
    tone: list[str] = Field(default_factory=list, description="Adjectives describing brand voice.")
    audience: list[str] = Field(default_factory=list, description="Who this brand serves.")
    opportunity_vector: list[str] = Field(
        default_factory=list,
        description="Positive signals — topics or positions to lean into.",
    )
    threat_vector: list[str] = Field(
        default_factory=list,
        description="Negative signals — competitors, topics to watch.",
    )
    source_snapshot_ids: list[str] = Field(
        default_factory=list,
        description="Provider snapshot fingerprints rolled into this vector.",
    )
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class Dossier(BaseModel):
    """Markdown-rendered dossier for a tenant — what the Glass tile reads."""

    model_config = ConfigDict(extra="forbid")

    tenant: str = Field(min_length=1)
    rendered_at: datetime
    summary: str = Field(description="One-paragraph executive summary.")
    opportunities: list[str] = Field(default_factory=list)
    threats: list[str] = Field(default_factory=list)
    markdown: str = Field(description="Full markdown dossier body.")
    vector_ref: dict[str, Any] = Field(
        default_factory=dict,
        description="Pointer to the BrandVector that drove this dossier (tenant + computed_at).",
    )
