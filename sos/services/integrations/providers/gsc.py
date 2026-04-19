"""Google Search Console provider.

Pulls top 50 search queries + impressions + clicks for a tenant's site
over the requested range. Uses the tenant's stored Google OAuth
access_token (fetched via `TenantIntegrations.get_credentials('google_search_console')`).

Live endpoint: `POST https://searchconsole.googleapis.com/webmasters/v3/sites/<site>/searchAnalytics/query`
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import quote

import httpx

from sos.contracts.ports.integrations import (
    IntelligenceProvider,
    ProviderParams,
    ProviderSnapshot,
    SnapshotKind,
)

GSC_QUERY_URL = "https://searchconsole.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"


class GSCProvider:
    """GSC `searchAnalytics/query` → ProviderSnapshot."""

    kind: SnapshotKind = "gsc"

    def __init__(
        self,
        *,
        access_token_lookup,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._access_token_lookup = access_token_lookup
        self._http = http_client or httpx.AsyncClient(timeout=30.0)

    async def pull(self, tenant: str, params: ProviderParams) -> ProviderSnapshot:
        access_token = await self._access_token_lookup(tenant, "google_search_console")
        body = self._build_query_body(params.range_days)
        url = GSC_QUERY_URL.format(site=quote(params.source_id, safe=""))
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
    def _build_query_body(range_days: int) -> dict[str, Any]:
        end = date.today()
        start = end - timedelta(days=range_days)
        return {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dimensions": ["query"],
            "rowLimit": 50,
        }


_: type[IntelligenceProvider] = GSCProvider  # type: ignore[assignment]
