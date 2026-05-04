"""Unit tests for sos.services.bus.outbox_stats.

S025 Phase A-1 — covers the SOS branch of the F-17 ``outbox.status``
MCP tool.

Strategy: structural-typed stub Redis client. The helper is intentionally
small enough that a stub keeps the entire contract under test without a
running Redis. Integration coverage (real XPENDING numbers from a live
bus) is owned by ``tests/integration/test_bus_dlq.py`` +
``tests/integration/test_bus_retry.py``; this file owns the aggregation
shape contract.
"""
from __future__ import annotations

from typing import Any

import pytest

from sos.services.bus.outbox_stats import (
    BUS_STREAM_PREFIX,
    collect_bus_outbox_stats_sync,
)
from sos.services.bus.dlq import DLQ_STREAM_PREFIX


# ---------------------------------------------------------------------------
# Stub client
# ---------------------------------------------------------------------------


class _StubRedis:
    """Minimal sync-Redis stub matching :class:`SyncRedisLike`.

    ``streams`` maps stream name → list of group dicts (each with a
    ``pending`` field). ``dlq`` maps DLQ stream name → XLEN integer.
    """

    def __init__(
        self,
        streams: dict[str, list[dict[str, Any]]] | None = None,
        dlq: dict[str, int] | None = None,
        scan_raises: BaseException | None = None,
        xinfo_raises_for: set[str] | None = None,
    ):
        self.streams = streams or {}
        self.dlq = dlq or {}
        self._scan_raises = scan_raises
        self._xinfo_raises_for = xinfo_raises_for or set()

    def scan_iter(self, match: str | None = None, count: int | None = None):
        if self._scan_raises is not None:
            raise self._scan_raises
        # mimic redis-py: only yield keys with the requested prefix
        prefix = (match or "").rstrip("*")
        for key in list(self.streams) + list(self.dlq):
            if key.startswith(prefix):
                yield key

    def xinfo_groups(self, name: str):
        if name in self._xinfo_raises_for:
            raise RuntimeError(f"no groups on {name}")
        return list(self.streams.get(name, []))

    def xlen(self, name: str) -> int:
        return self.dlq.get(name, 0)


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_empty_substrate_reports_zero_zero():
    client = _StubRedis()
    out = collect_bus_outbox_stats_sync(client)
    assert out == {"pending_count": 0, "dlq_count": 0}


def test_single_group_pending_summed():
    client = _StubRedis(
        streams={
            f"{BUS_STREAM_PREFIX}global:agent:kasra": [{"pending": 3}],
        }
    )
    out = collect_bus_outbox_stats_sync(client)
    assert out == {"pending_count": 3, "dlq_count": 0}


def test_pending_summed_across_groups_and_streams():
    client = _StubRedis(
        streams={
            f"{BUS_STREAM_PREFIX}global:agent:kasra": [
                {"pending": 2},
                {"pending": 5},
            ],
            f"{BUS_STREAM_PREFIX}project:gaf:agent:athena": [{"pending": 1}],
        }
    )
    out = collect_bus_outbox_stats_sync(client)
    assert out == {"pending_count": 8, "dlq_count": 0}


def test_dlq_xlen_summed():
    client = _StubRedis(
        dlq={
            f"{DLQ_STREAM_PREFIX}sos:stream:global:agent:kasra": 4,
            f"{DLQ_STREAM_PREFIX}sos:stream:project:gaf:agent:athena": 7,
        }
    )
    out = collect_bus_outbox_stats_sync(client)
    assert out == {"pending_count": 0, "dlq_count": 11}


def test_dlq_streams_excluded_from_pending_walk():
    """DLQ stream keys must NOT be processed as live consumer-group streams.

    The retry worker writes raw payload fields onto the DLQ — they have
    no consumer groups. Treating them as "pending" would double-count
    DLQ rows as pending work and false-page F-17.
    """
    client = _StubRedis(
        streams={
            f"{BUS_STREAM_PREFIX}global:agent:kasra": [{"pending": 2}],
        },
        dlq={
            f"{DLQ_STREAM_PREFIX}sos:stream:global:agent:kasra": 9,
        },
    )
    out = collect_bus_outbox_stats_sync(client)
    assert out == {"pending_count": 2, "dlq_count": 9}


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_stream_with_no_groups_skipped_silently():
    """A bus stream that exists but has zero consumer groups raises on
    XINFO GROUPS — must not propagate. Counted as zero pending."""
    target = f"{BUS_STREAM_PREFIX}global:agent:nobody"
    client = _StubRedis(
        streams={
            target: [{"pending": 99}],  # if iterated, would inflate count
            f"{BUS_STREAM_PREFIX}global:agent:kasra": [{"pending": 4}],
        },
        xinfo_raises_for={target},
    )
    out = collect_bus_outbox_stats_sync(client)
    assert out == {"pending_count": 4, "dlq_count": 0}


def test_bytes_keys_normalised_to_str():
    """SCAN under non-decode-responses clients yields bytes — normalise."""

    class _BytesScanRedis(_StubRedis):
        def scan_iter(self, match=None, count=None):
            for k in super().scan_iter(match=match, count=count):
                yield k.encode()

    client = _BytesScanRedis(
        streams={f"{BUS_STREAM_PREFIX}global:agent:kasra": [{"pending": 2}]},
        dlq={f"{DLQ_STREAM_PREFIX}sos:stream:global:agent:kasra": 1},
    )
    # Stub yields bytes; xinfo_groups + xlen still take str — the helper
    # has to decode before dispatching. xinfo_groups in the stub takes
    # string names, so this only works because the helper decodes first.
    out = collect_bus_outbox_stats_sync(client)
    assert out == {"pending_count": 2, "dlq_count": 1}


def test_pending_field_zero_or_missing_is_zero():
    client = _StubRedis(
        streams={
            f"{BUS_STREAM_PREFIX}global:agent:kasra": [
                {"pending": 0},
                {},  # missing field
                {"pending": None},  # null
            ],
        }
    )
    out = collect_bus_outbox_stats_sync(client)
    assert out == {"pending_count": 0, "dlq_count": 0}


def test_redis_failure_propagates_to_caller():
    """The F-17 wrapper catches and converts to backend='error'. The
    helper itself must not swallow — that would mask substrate outages."""

    class _BoomRedis(_StubRedis):
        def scan_iter(self, match=None, count=None):
            raise ConnectionError("redis down")

    with pytest.raises(ConnectionError):
        collect_bus_outbox_stats_sync(_BoomRedis())
