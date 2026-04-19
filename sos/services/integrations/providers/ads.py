"""Google Ads API provider.

Pulls active campaigns + average CPC for a tenant's customer account.
Uses the tenant's stored Google OAuth access_token (fetched via
`TenantIntegrations.get_credentials('google_ads')`) + the platform
developer token (read from env at call time).

Live endpoint: `POST https://googleads.googleapis.com/v16/customers/<id>/googleAds:search`
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from sos.contracts.ports.integrations import (
    IntelligenceProvider,
    ProviderParams,
    ProviderSnapshot,
    SnapshotKind,
)

ADS_SEARCH_URL = "https://googleads.googleapis.com/v16/customers/{customer_id}/googleAds:search"

ADS_GAQL_CAMPAIGNS = """
SELECT campaign.id, campaign.name, campaign.status, metrics.average_cpc
FROM campaign
WHERE campaign.status = 'ENABLED'
LIMIT 50
""".strip()


class GoogleAdsProvider:
    """Google Ads `googleAds:search` → ProviderSnapshot."""

    kind: SnapshotKind = "google_ads"

    def __init__(
        self,
        *,
        access_token_lookup,
        developer_token: Optional[str] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._access_token_lookup = access_token_lookup
        self._developer_token = developer_token or os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", "")
        self._http = http_client or httpx.AsyncClient(timeout=30.0)

    async def pull(self, tenant: str, params: ProviderParams) -> ProviderSnapshot:
        access_token = await self._access_token_lookup(tenant, "google_ads")
        url = ADS_SEARCH_URL.format(customer_id=params.source_id)
        resp = await self._http.post(
            url,
            json={"query": ADS_GAQL_CAMPAIGNS},
            headers={
                "Authorization": f"Bearer {access_token}",
                "developer-token": self._developer_token,
            },
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


_: type[IntelligenceProvider] = GoogleAdsProvider  # type: ignore[assignment]
