"""Dead-letter queue — schema, keys, and parse helpers for the bus DLQ.

Retry-exhausted messages land on ``sos:stream:dlq:{original_stream}``.
The retry worker (see :mod:`sos.services.bus.retry`) writes entries;
the dashboard reads them; consumers don't touch this path.

The DLQ is intentionally minimal — just enough metadata that an
operator can answer "did this message get stuck, and if so which
stream, group, and how many times did it try before giving up?".
Full replay / reprocessing belongs to W5+; today the DLQ is a
diagnostic surface, not an execution surface.

Schema stability: :class:`DLQEntry` is the on-wire shape and the
parse target. Fields are flat strings/ints because Redis stream
entries are ``field -> string`` maps; deeper structures (the
original payload) are JSON-encoded into ``payload`` and parsed on
read. Consumers that only need metadata (retry_count, group) avoid
paying the JSON cost.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

DLQ_STREAM_PREFIX = "sos:stream:dlq:"


def dlq_stream_for(original_stream: str) -> str:
    """Return the DLQ stream key for ``original_stream``.

    Centralising the key convention means the writer (retry worker)
    and readers (dashboard, ops scripts) never drift.
    """
    return f"{DLQ_STREAM_PREFIX}{original_stream}"


class DLQEntry(BaseModel):
    """One dead-letter entry, as stored on the DLQ stream.

    ``dlq_id`` is the entry's own stream ID in the DLQ (useful for
    paging); ``original_id`` is the stream ID it had in the source
    stream before retries were exhausted.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dlq_id: str = Field(description="XADD id on the DLQ stream itself.")
    dlq_stream: str = Field(description="The DLQ stream key (e.g. sos:stream:dlq:...).")
    original_stream: str = Field(description="Stream the message originated on.")
    original_id: str = Field(description="Stream ID the message had before exhaustion.")
    group: str = Field(description="Consumer group that failed to process the message.")
    retry_count: int = Field(
        ge=0,
        description="Delivery count recorded at the time of DLQ routing.",
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Original payload fields as a dict — decoded from the stored JSON blob.",
    )


def build_dlq_fields(
    *,
    original_stream: str,
    original_id: str,
    group: str,
    retry_count: int,
    payload: dict[str, Any],
) -> dict[str, str]:
    """Shape the field-map the retry worker XADDs onto the DLQ stream.

    Kept here (not in retry.py) so writers and readers share one
    source of truth for field names and encoding. Values are stringified
    because Redis stream entries are ``str -> str`` at the wire level.
    """
    return {
        "original_stream": original_stream,
        "original_id": original_id,
        "group": group,
        "retry_count": str(retry_count),
        "payload": json.dumps(payload),
    }


def parse_dlq_entry(dlq_stream: str, dlq_id: str, fields: dict[str, str]) -> DLQEntry:
    """Parse a single XRANGE/XREVRANGE tuple into a :class:`DLQEntry`.

    Tolerant of missing fields — an entry written by an older writer
    may lack newer keys, and a hard ``KeyError`` would brick the
    dashboard for everyone. Missing payload parses to ``{}``.
    """
    raw_payload = fields.get("payload", "")
    if raw_payload:
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            payload = {}
    else:
        payload = {}

    return DLQEntry(
        dlq_id=dlq_id,
        dlq_stream=dlq_stream,
        original_stream=fields.get("original_stream", ""),
        original_id=fields.get("original_id", ""),
        group=fields.get("group", ""),
        retry_count=int(fields.get("retry_count", "0") or 0),
        payload=payload,
    )


class _AsyncRedisLike(Protocol):
    """Structural subset of redis.asyncio.Redis we actually use here."""

    async def xrevrange(
        self, name: str, max: str = ..., min: str = ..., count: int | None = ...
    ) -> list[tuple[str, dict[str, str]]]: ...

    def scan_iter(self, match: str | None = ..., count: int | None = ...) -> Any: ...


async def read_dlq(
    client: _AsyncRedisLike, original_stream: str, limit: int = 100
) -> list[DLQEntry]:
    """Return up to ``limit`` DLQ entries for ``original_stream``, newest first.

    Returns ``[]`` when the DLQ stream doesn't exist — Redis treats an
    unknown stream as empty on XREVRANGE, which matches "no failures
    yet" better than an error.
    """
    dlq_stream = dlq_stream_for(original_stream)
    raw = await client.xrevrange(dlq_stream, count=limit)
    return [parse_dlq_entry(dlq_stream, entry_id, fields) for entry_id, fields in raw]


async def list_dlq_streams(client: _AsyncRedisLike) -> list[str]:
    """Return the ``original_stream`` names that currently have DLQ entries.

    Uses ``SCAN`` + ``MATCH`` so it's safe under production key
    volumes — no ``KEYS *`` blocking call. Sorted so the dashboard
    renders deterministically.
    """
    out: list[str] = []
    async for key in client.scan_iter(match=f"{DLQ_STREAM_PREFIX}*", count=100):
        # redis-py with decode_responses=True yields str; with bytes it
        # yields bytes. Normalise so callers don't have to care.
        if isinstance(key, bytes):
            key = key.decode()
        out.append(key[len(DLQ_STREAM_PREFIX) :])
    out.sort()
    return out
