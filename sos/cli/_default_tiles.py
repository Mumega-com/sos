"""Default tile set seeded by ``sos init`` Step F.

Six tiles — the Phase 6 baseline plus the Phase 7 Brand Vector tile:

1. Health        — status_light, http → /registry/squad/<tenant>/status
2. Metabolism    — sparkline,    sql  → wallet_ledger (501 fallback until Phase 7)
3. Objectives    — progress_bar, http → /objectives/roots/<tenant>
4. Decisions     — event_log,    bus_tail → audit:decisions:<tenant>
5. Metrics       — chart,        http → /integrations/ga4/<tenant>
6. Brand Vector  — event_log,    http → /integrations/dossier/<tenant>/latest

Tile ids are slug-safe (``^[a-z0-9-]+$``) so they round-trip through the
URL path in ``GET /glass/payload/<tenant>/<tile_id>``.
"""
from __future__ import annotations

from sos.contracts.ports.glass import (
    BusTailQuery,
    HttpQuery,
    SqlQuery,
    Tile,
    TileTemplate,
)


def default_tiles(tenant: str) -> list[Tile]:
    """Return the Phase 6 baseline tile set for ``tenant``."""
    return [
        Tile(
            id="health",
            title="Squad Health",
            query=HttpQuery(
                kind="http",
                service="registry",
                path=f"/registry/squad/{tenant}/status",
            ),
            template=TileTemplate.STATUS_LIGHT,
            refresh_interval_s=30,
            tenant=tenant,
        ),
        Tile(
            id="metabolism",
            title="$MIND Balance",
            query=SqlQuery(
                kind="sql",
                service="economy",
                statement=(
                    "SELECT ts, balance FROM wallet_ledger "
                    "WHERE tenant = :tenant ORDER BY ts DESC LIMIT 30"
                ),
                params={"tenant": tenant},
            ),
            template=TileTemplate.SPARKLINE,
            refresh_interval_s=60,
            tenant=tenant,
        ),
        Tile(
            id="objectives",
            title="Objectives Progress",
            query=HttpQuery(
                kind="http",
                service="objectives",
                path=f"/objectives/roots/{tenant}",
            ),
            template=TileTemplate.PROGRESS_BAR,
            refresh_interval_s=120,
            tenant=tenant,
        ),
        Tile(
            id="decisions",
            title="Recent Decisions",
            query=BusTailQuery(
                kind="bus_tail",
                stream=f"audit:decisions:{tenant}",
                limit=20,
            ),
            template=TileTemplate.EVENT_LOG,
            refresh_interval_s=30,
            tenant=tenant,
        ),
        Tile(
            id="metrics",
            title="GA4 Metrics",
            query=HttpQuery(
                kind="http",
                service="integrations",
                path=f"/integrations/ga4/{tenant}",
            ),
            template=TileTemplate.CHART,
            refresh_interval_s=300,
            tenant=tenant,
        ),
        Tile(
            id="brand-vector",
            title="Brand Vector",
            query=HttpQuery(
                kind="http",
                service="integrations",
                path=f"/integrations/dossier/{tenant}/latest",
            ),
            template=TileTemplate.EVENT_LOG,
            refresh_interval_s=3600,
            tenant=tenant,
        ),
    ]


__all__ = ["default_tiles"]
