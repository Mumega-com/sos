"""Read-only Redis Stream stats for SOS bus outbox visibility.

This module is kernel-level on purpose: MCP and other boundary surfaces may
read substrate status without importing service-layer modules.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Protocol

BUS_STREAM_PREFIX = "sos:stream:"
DLQ_STREAM_PREFIX = "sos:stream:dlq:"
SCAN_BATCH_HINT = 100


class SyncRedisLike(Protocol):
    """Structural subset of ``redis.Redis`` used by the stats walker."""

    def scan_iter(
        self, match: str | None = ..., count: int | None = ...
    ) -> Iterable[Any]: ...

    def xinfo_groups(self, name: str) -> list[Mapping[str, Any]]: ...

    def xlen(self, name: str) -> int: ...


def collect_bus_outbox_stats_sync(client: SyncRedisLike) -> dict[str, int]:
    """Walk all bus streams and DLQ streams and return aggregate counts."""
    pending = 0
    dlq = 0

    for raw_key in client.scan_iter(match=f"{BUS_STREAM_PREFIX}*", count=SCAN_BATCH_HINT):
        key = raw_key.decode() if isinstance(raw_key, bytes) else raw_key

        if key.startswith(DLQ_STREAM_PREFIX):
            dlq += int(client.xlen(key) or 0)
            continue

        try:
            groups = client.xinfo_groups(key) or []
        except Exception:
            continue

        for group in groups:
            pending += int(group.get("pending", 0) or 0)

    return {"pending_count": pending, "dlq_count": dlq}
