"""Fake snapshot adapter — canned BrightData/Apify-style data for dev + CI."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sos.contracts.ports.integrations import ProviderParams, ProviderSnapshot


class FakeSnapshotAdapter:
    """Deterministic snapshots tagged as `brightdata` or `apify`."""

    def __init__(
        self,
        *,
        kind: str = "brightdata",
        canned_items: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        if kind not in {"brightdata", "apify"}:
            raise ValueError(f"kind must be brightdata|apify, got {kind!r}")
        self._kind = kind
        self._canned = canned_items or [
            {"competitor": "acme.ai", "keyword": "ai automation", "rank": 3},
            {"competitor": "beta.io", "keyword": "ai automation", "rank": 7},
            {"competitor": "acme.ai", "keyword": "brand vector", "rank": 1},
        ]

    async def trigger(self, tenant: str, params: ProviderParams) -> str:
        return f"fake-run-{tenant}-{params.source_id}"

    async def fetch_result(
        self,
        tenant: str,
        run_id: str,
        params: ProviderParams,
    ) -> ProviderSnapshot:
        rows_key = "rows" if self._kind == "brightdata" else "items"
        return ProviderSnapshot(
            tenant=tenant,
            kind=self._kind,  # type: ignore[arg-type]
            captured_at=datetime.now(timezone.utc),
            source_id=params.source_id,
            payload={rows_key: list(self._canned), "run_id": run_id},
            metadata={"range_days": params.range_days, "fake": True},
        )
