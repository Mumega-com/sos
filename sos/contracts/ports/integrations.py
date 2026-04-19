"""Intelligence provider port — growth-intel data sources.

A provider pulls signals from an external data source (GA4, GSC, Google Ads,
BrightData, Apify) and returns a `ProviderSnapshot`. The narrative-synth
agent clusters snapshots into a BrandVector (Step 7.3); the dossier-writer
renders a markdown dossier (Step 7.3); the Glass tile reads the latest
dossier from memory (Step 7.5).

Phase 7 ships with `fake` providers wired by default. Live providers
activate when `SOS_INTEGRATIONS_PROVIDER=live` + per-tenant OAuth creds
are present.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


SnapshotKind = Literal["ga4", "gsc", "google_ads", "brightdata", "apify", "fake"]


class ProviderSnapshot(BaseModel):
    """One pull from an external data source, tagged for the synth agent."""

    model_config = ConfigDict(extra="forbid")

    tenant: str = Field(min_length=1)
    kind: SnapshotKind
    captured_at: datetime
    source_id: str = Field(
        description="Provider-specific resource ID (property_id, site, customer_id, dataset_id, ...)",
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Raw provider response, shape defined per-kind.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Caller context (date range, query params, etc.).",
    )


class ProviderParams(BaseModel):
    """Input to `IntelligenceProvider.pull`.

    Providers pick the fields they care about and ignore the rest — this
    keeps the port signature stable as new providers land.
    """

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(
        description="property_id (GA4) / site URL (GSC) / customer_id (Ads) / dataset_id (BD/Apify)",
    )
    range_days: int = Field(default=30, ge=1, le=365)
    extra: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class IntelligenceProvider(Protocol):
    """Pull a snapshot for a tenant."""

    kind: SnapshotKind

    async def pull(self, tenant: str, params: ProviderParams) -> ProviderSnapshot: ...
