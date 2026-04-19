"""Fake intelligence provider — canned data for dev + CI.

Lets the growth-intel squad loop run end-to-end without real creds.
`SOS_INTEGRATIONS_PROVIDER=fake` (the default) routes here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sos.contracts.ports.integrations import (
    IntelligenceProvider,
    ProviderParams,
    ProviderSnapshot,
    SnapshotKind,
)


class FakeIntelligenceProvider:
    """Returns deterministic canned data tagged as `fake`."""

    kind: SnapshotKind = "fake"

    def __init__(self, *, canned: Optional[dict] = None) -> None:
        self._canned = canned or {
            "top_pages": [
                {"pagePath": "/", "sessions": 1200},
                {"pagePath": "/blog/intro", "sessions": 340},
                {"pagePath": "/pricing", "sessions": 180},
            ],
            "top_queries": [
                {"query": "mumega organism", "impressions": 420, "clicks": 38},
                {"query": "self writing dashboard", "impressions": 260, "clicks": 19},
            ],
            "campaigns": [
                {"name": "Playbook launch", "status": "ENABLED", "average_cpc": 1.24},
            ],
        }

    async def pull(self, tenant: str, params: ProviderParams) -> ProviderSnapshot:
        return ProviderSnapshot(
            tenant=tenant,
            kind=self.kind,
            captured_at=datetime.now(timezone.utc),
            source_id=params.source_id,
            payload=dict(self._canned),
            metadata={"range_days": params.range_days, "fake": True},
        )


_: type[IntelligenceProvider] = FakeIntelligenceProvider  # type: ignore[assignment]
