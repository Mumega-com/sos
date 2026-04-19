"""GA4 Data API provider.

Pulls 30-day (configurable) session + top-pages metrics for a tenant's
GA4 property. Uses the tenant's stored Google OAuth access_token
(fetched via `TenantIntegrations.get_credentials('google_analytics')`).

Live endpoint: `POST https://analyticsdata.googleapis.com/v1beta/properties/<id>:runReport`
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from sos.contracts.ports.integrations import (
    IntelligenceProvider,
    ProviderParams,
    ProviderSnapshot,
    SnapshotKind,
)

GA4_REPORT_URL = "https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport"


class GA4Provider:
    """GA4 Data API `runReport` → ProviderSnapshot."""

    kind: SnapshotKind = "ga4"

    def __init__(
        self,
        *,
        access_token_lookup,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._access_token_lookup = access_token_lookup
        self._http = http_client or httpx.AsyncClient(timeout=30.0)

    async def pull(self, tenant: str, params: ProviderParams) -> ProviderSnapshot:
        access_token = await self._access_token_lookup(tenant, "google_analytics")
        body = self._build_report_body(params.range_days)
        url = GA4_REPORT_URL.format(property_id=params.source_id)
        resp = await self._http.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        payload: dict[str, Any] = resp.json()
        return ProviderSnapshot(
            tenant=tenant,
            kind=self.kind,
            captured_at=datetime.now(timezone.utc),
            source_id=params.source_id,
            payload=payload,
            metadata={"range_days": params.range_days},
        )

    @staticmethod
    def _build_report_body(range_days: int) -> dict[str, Any]:
        return {
            "dateRanges": [{"startDate": f"{range_days}daysAgo", "endDate": "today"}],
            "metrics": [{"name": "sessions"}, {"name": "activeUsers"}],
            "dimensions": [{"name": "pagePath"}],
            "limit": 50,
        }


# Assert conformance — mypy/runtime_checkable Protocol.
_: type[IntelligenceProvider] = GA4Provider  # type: ignore[assignment]
