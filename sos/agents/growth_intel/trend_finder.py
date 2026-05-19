"""trend-finder agent — fan out pulls across all providers for a tenant."""

from __future__ import annotations

import asyncio
from typing import Sequence

from sos.contracts.ports.integrations import (
    IntelligenceProvider,
    ProviderParams,
    ProviderSnapshot,
)


class TrendFinder:
    """Fan out `pull()` across the configured providers, collect snapshots.

    Per-provider errors are captured into ``errors`` instead of raising —
    growth-intel should be best-effort on input side so one dead
    integration doesn't prevent the dossier from rendering.
    """

    def __init__(self, providers: Sequence[IntelligenceProvider]) -> None:
        if not providers:
            raise ValueError("TrendFinder requires at least one provider")
        self._providers = list(providers)

    async def run(
        self,
        tenant: str,
        params: ProviderParams,
    ) -> tuple[list[ProviderSnapshot], list[tuple[str, Exception]]]:
        snapshots: list[ProviderSnapshot] = []
        errors: list[tuple[str, Exception]] = []
        results = await asyncio.gather(
            *(self._safe_pull(p, tenant, params) for p in self._providers),
            return_exceptions=False,
        )
        for provider, result in zip(self._providers, results):
            if isinstance(result, Exception):
                errors.append((provider.kind, result))
            else:
                snapshots.append(result)
        return snapshots, errors

    @staticmethod
    async def _safe_pull(
        provider: IntelligenceProvider,
        tenant: str,
        params: ProviderParams,
    ) -> ProviderSnapshot | Exception:
        try:
            return await provider.pull(tenant, params)
        except Exception as exc:  # noqa: BLE001
            return exc
