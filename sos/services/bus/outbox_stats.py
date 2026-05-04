"""Read-only stream stats for the F-17 SOS bus outbox status branch.

S025 Phase A-1 — promotes the SOS branch of the substrate-monitor
``outbox.status`` MCP tool from ``best_effort`` placeholder to ``native``
durable counts backed by Redis Streams.

Substrate shape:

* The SOS bus uses Redis Streams (``XADD`` / ``XREADGROUP``) with
  at-least-once delivery via consumer groups. Persistence is owned by
  Redis (AOF / RDB) — the substrate IS durable; only the visibility
  surface was missing.
* The :class:`sos.services.bus.retry.RetryWorker` scans ``XPENDING`` for
  every registered (stream, group), routes claimed-but-unacked messages
  back to the head of the stream after backoff, and DLQ-routes anything
  past the retry budget.
* The DLQ stream prefix is ``sos:stream:dlq:`` per
  :mod:`sos.services.bus.dlq`.

What we report (matches v0.5 brief §6.6 F-17 component shape):

* ``pending_count`` — total ``pending`` field across every consumer
  group on every non-DLQ bus stream. Operationally analogous to the
  Mirror outbox ``pending`` row count (claimed-but-unacked work).
* ``dlq_count``     — total ``XLEN`` across every ``sos:stream:dlq:*``
  stream. Same semantic as Mirror outbox DLQ row count.

Backend label (set by the F-17 wrapper, not here):

* ``native`` when Redis is reachable AND enumeration succeeded.
* ``error``  when Redis raises during enumeration; counts default to 0.

Why a Protocol instead of importing ``redis.Redis``: this helper has to
work both against the real sync ``redis-py`` client and against a stub
client used in unit tests. Structural typing keeps test plumbing trivial
(no monkeypatching required) and decouples from ``redis-py`` major-version
churn. (See `feedback_explicit_emit_over_parsing.md` — read live state
from the substrate, never parse a string proxy.)
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Protocol

from sos.services.bus.dlq import DLQ_STREAM_PREFIX

BUS_STREAM_PREFIX = "sos:stream:"
SCAN_BATCH_HINT = 100


class SyncRedisLike(Protocol):
    """Structural subset of ``redis.Redis`` we actually use here."""

    def scan_iter(
        self, match: str | None = ..., count: int | None = ...
    ) -> Iterable[Any]: ...

    def xinfo_groups(self, name: str) -> list[Mapping[str, Any]]: ...

    def xlen(self, name: str) -> int: ...


def collect_bus_outbox_stats_sync(client: SyncRedisLike) -> dict[str, int]:
    """Walk all bus streams + DLQ streams; return aggregate counts.

    Sum semantics:

    * For each ``sos:stream:*`` key that is NOT a DLQ stream, query
      ``XINFO GROUPS`` and sum the ``pending`` field across every group.
    * For each ``sos:stream:dlq:*`` key, add ``XLEN``.

    Notes on edge cases:

    * Streams that exist but have no consumer groups raise on
      ``XINFO GROUPS``. We catch and continue — a never-consumed stream
      reports zero pending, not an error. Mirror's outbox doesn't false-
      page on empty queues; this branch must hold the same line.
    * ``decode_responses=True`` clients yield ``str``; some configurations
      yield ``bytes``. We normalise so callers don't care.
    * Exceptions other than the per-stream ``xinfo_groups`` swallow above
      bubble up to the caller — the F-17 wrapper converts those to
      ``backend='error'`` with ``last_error`` populated.
    """
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
            # Stream exists but has no groups (XINFO GROUPS raises) or
            # the key was deleted between SCAN and the lookup. Either
            # way, nothing pending — skip.
            continue

        for group in groups:
            pending += int(group.get("pending", 0) or 0)

    return {"pending_count": pending, "dlq_count": dlq}
