"""Canonical SOS bus envelope — single source of truth for message shape.

Prior to this module, four senders re-implemented the envelope inline:

- ``sos/mcp/sos_mcp_sse.py::sos_msg()``
- ``scripts/bus-send.py::send()`` (``~/scripts/bus-send.py``)
- ``sos/bus/bridge.py`` (``/send`` route)
- ``~/.hermes/hermes-agent/gateway/sos_bus.py::post_reply()``

Drift meant one bad sender (raw-string ``payload``) silently dropped the body
at every receiver, because every receiver does ``json.loads(payload)`` and
returns ``""`` on parse failure. See ``feedback_bus_envelope_schema.md``.

Canonical shape — Redis stream fields (all values are str, per Redis):

    type       str   message type: chat / send / remember / broadcast / ack
    source     str   "agent:<name>" | "squad:<slug>"
    target     str   "agent:<name>" | "squad:<slug>"
    payload    str   JSON dict with keys:
                        text       body string
                        source     "agent:<name>"  (mirrors top-level)
                        timestamp  unix seconds (float)
                        ...extras  domain-specific keys

    # Optional top-level, present on newer senders:
    id         str   uuid4
    timestamp  str   ISO-8601 UTC (top-level — distinct from payload.timestamp)
    version    str   "1.0"
    project    str   tenant scope slug

Senders: call ``build()``. Receivers: call ``parse()`` — TOLERANT of legacy
raw-string payloads so a fourth-drifting sender degrades rather than silently
drops.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

CANONICAL_VERSION = "1.0"


def build(
    *,
    msg_type: str,
    source: str,
    target: str,
    text: str,
    project: str | None = None,
    extras: dict[str, Any] | None = None,
    message_id: str | None = None,
) -> dict[str, str]:
    """Build a canonical envelope ready for ``redis.xadd(stream, envelope)``.

    Args:
        msg_type: ``chat`` / ``send`` / ``remember`` / ``broadcast`` / ``ack`` / ...
        source:   ``"agent:<name>"`` or ``"squad:<slug>"`` — kind prefix required.
        target:   ``"agent:<name>"`` or ``"squad:<slug>"``.
        text:     body (may be empty, never None).
        project:  tenant scope slug. Omit for global scope.
        extras:   extra payload keys (``remember``, ``content_type``, ``trace_id``, ...).
        message_id: override the generated uuid4. Omit to auto-generate.

    Returns:
        Dict of str → str suitable for passing directly to ``redis.xadd``.
    """
    if text is None:
        raise ValueError("envelope.build: text must not be None (use '' for empty)")
    if ":" not in source:
        raise ValueError(f"envelope.build: source must be prefixed (e.g. 'agent:{source}')")
    if ":" not in target:
        raise ValueError(f"envelope.build: target must be prefixed (e.g. 'agent:{target}')")

    payload_obj: dict[str, Any] = {
        "text": text,
        "source": source,
        "timestamp": time.time(),
    }
    if extras:
        payload_obj.update(extras)

    envelope: dict[str, str] = {
        "id": message_id or str(uuid4()),
        "type": msg_type,
        "source": source,
        "target": target,
        "payload": json.dumps(payload_obj),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": CANONICAL_VERSION,
    }
    if project:
        envelope["project"] = project
    return envelope


def parse(fields: dict[str, str]) -> dict[str, Any]:
    """Parse a redis-stream fields dict into a flat, receiver-friendly shape.

    TOLERANT of three failure modes so drift degrades, not drops:
      1. ``payload`` is valid JSON dict       → standard path.
      2. ``payload`` is a raw string          → wrapped as ``{"text": payload}``.
      3. ``payload`` is missing / empty       → empty payload.
      4. ``payload`` is JSON non-dict         → coerced to ``{"text": str(x)}``.

    Returns a dict with keys:
      type, source, target, text, timestamp (float|None), id, project, version, extras
    """
    raw_payload = fields.get("payload", "")
    payload_obj: dict[str, Any]

    if not raw_payload:
        payload_obj = {}
    else:
        try:
            loaded = json.loads(raw_payload)
            if isinstance(loaded, dict):
                payload_obj = loaded
            else:
                payload_obj = {"text": str(loaded)}
        except (json.JSONDecodeError, TypeError):
            payload_obj = {"text": raw_payload}

    source = payload_obj.get("source") or fields.get("source", "")
    raw_ts = payload_obj.get("timestamp")
    try:
        ts_float: float | None = float(raw_ts) if raw_ts is not None else None
    except (TypeError, ValueError):
        ts_float = None

    reserved = {"text", "source", "timestamp"}
    extras = {k: v for k, v in payload_obj.items() if k not in reserved}

    return {
        "type": fields.get("type", ""),
        "source": source,
        "target": fields.get("target", ""),
        "text": payload_obj.get("text", ""),
        "timestamp": ts_float,
        "id": fields.get("id") or fields.get("message_id"),
        "project": fields.get("project"),
        "version": fields.get("version"),
        "extras": extras,
    }


__all__ = ["build", "parse", "CANONICAL_VERSION"]
