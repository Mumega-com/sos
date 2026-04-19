"""Apify actor adapter.

Trigger an actor run, poll for readiness, return the dataset items.

Endpoints:
  POST https://api.apify.com/v2/acts/<actor_id>/runs
  GET  https://api.apify.com/v2/actor-runs/<run_id>
  GET  https://api.apify.com/v2/datasets/<dataset_id>/items
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from sos.contracts.ports.integrations import ProviderParams, ProviderSnapshot

RUNS_URL = "https://api.apify.com/v2/acts/{actor_id}/runs"
RUN_STATUS_URL = "https://api.apify.com/v2/actor-runs/{run_id}"
DATASET_URL = "https://api.apify.com/v2/datasets/{dataset_id}/items"


class ApifyAdapter:
    """Apify actor runs + dataset reads."""

    def __init__(
        self,
        *,
        api_token: Optional[str] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._api_token = api_token or os.environ.get("APIFY_API_TOKEN", "")
        self._http = http_client or httpx.AsyncClient(timeout=60.0)

    async def trigger(self, tenant: str, params: ProviderParams) -> str:
        """Kick off an actor run. Returns the run_id."""
        actor_input = params.extra.get("actor_input") or {}
        resp = await self._http.post(
            RUNS_URL.format(actor_id=params.source_id),
            json=actor_input,
            headers={
                "Authorization": f"Bearer {self._api_token}",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        run_id = data.get("data", {}).get("id")
        if not run_id:
            raise ValueError(f"Apify trigger returned no run id: {data!r}")
        return str(run_id)

    async def fetch_result(
        self,
        tenant: str,
        run_id: str,
        params: ProviderParams,
    ) -> ProviderSnapshot:
        """Fetch dataset items for the run. Raises NotReady if not finished."""
        from sos.services.integrations.adapters import NotReady

        status_resp = await self._http.get(
            RUN_STATUS_URL.format(run_id=run_id),
            headers={"Authorization": f"Bearer {self._api_token}"},
        )
        status_resp.raise_for_status()
        status_data = status_resp.json().get("data", {})
        run_status = status_data.get("status")
        if run_status in {"READY", "RUNNING"}:
            raise NotReady(f"apify run {run_id} status={run_status}")
        if run_status != "SUCCEEDED":
            raise RuntimeError(f"apify run {run_id} finished with status={run_status!r}")

        dataset_id = status_data.get("defaultDatasetId")
        if not dataset_id:
            raise ValueError(f"apify run {run_id} has no default dataset")

        items_resp = await self._http.get(
            DATASET_URL.format(dataset_id=dataset_id),
            headers={"Authorization": f"Bearer {self._api_token}"},
        )
        items_resp.raise_for_status()
        items: list[Any] = items_resp.json()

        return ProviderSnapshot(
            tenant=tenant,
            kind="apify",
            captured_at=datetime.now(timezone.utc),
            source_id=params.source_id,
            payload={"items": items, "run_id": run_id, "dataset_id": dataset_id},
            metadata={"range_days": params.range_days},
        )
