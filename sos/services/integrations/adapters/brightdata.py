"""BrightData DCA adapter.

Trigger a dataset run, poll for readiness, return the dataset payload.

Endpoints:
  POST https://api.brightdata.com/dca/trigger?collector=<collector_id>
  GET  https://api.brightdata.com/dca/dataset?id=<run_id>
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from sos.contracts.ports.integrations import ProviderParams, ProviderSnapshot

TRIGGER_URL = "https://api.brightdata.com/dca/trigger"
DATASET_URL = "https://api.brightdata.com/dca/dataset"


class BrightDataAdapter:
    """BrightData Data Collector API."""

    def __init__(
        self,
        *,
        api_token: Optional[str] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._api_token = api_token or os.environ.get("BRIGHTDATA_API_TOKEN", "")
        self._http = http_client or httpx.AsyncClient(timeout=60.0)

    async def trigger(self, tenant: str, params: ProviderParams) -> str:
        """Kick off a dataset collector run. Returns the run_id."""
        # Per-run input rows, defaulting to a single empty row if the caller
        # didn't supply any in `params.extra["rows"]`.
        rows = params.extra.get("rows") or [{}]
        resp = await self._http.post(
            TRIGGER_URL,
            params={"collector": params.source_id},
            json=rows,
            headers={
                "Authorization": f"Bearer {self._api_token}",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        run_id = data.get("collection_id") or data.get("response_id")
        if not run_id:
            raise ValueError(f"BrightData trigger returned no run id: {data!r}")
        return str(run_id)

    async def fetch_result(
        self,
        tenant: str,
        run_id: str,
        params: ProviderParams,
    ) -> ProviderSnapshot:
        """Fetch dataset rows. Raises NotReady if the run is still running."""
        from sos.services.integrations.adapters import NotReady

        resp = await self._http.get(
            DATASET_URL,
            params={"id": run_id, "format": "json"},
            headers={"Authorization": f"Bearer {self._api_token}"},
        )
        if resp.status_code == 202:
            raise NotReady(f"brightdata run {run_id} still running")
        resp.raise_for_status()
        payload: list[Any] = resp.json()
        return ProviderSnapshot(
            tenant=tenant,
            kind="brightdata",
            captured_at=datetime.now(timezone.utc),
            source_id=params.source_id,
            payload={"rows": payload, "run_id": run_id},
            metadata={"range_days": params.range_days},
        )
