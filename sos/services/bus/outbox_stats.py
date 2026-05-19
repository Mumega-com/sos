"""Compatibility re-export for SOS bus outbox status helpers.

The implementation moved to ``sos.kernel.bus_outbox_stats`` so MCP boundary
code can read substrate status without importing ``sos.services.*``.
"""
from __future__ import annotations

from sos.kernel.bus_outbox_stats import (  # noqa: F401
    BUS_STREAM_PREFIX,
    DLQ_STREAM_PREFIX,
    SCAN_BATCH_HINT,
    SyncRedisLike,
    collect_bus_outbox_stats_sync,
)
