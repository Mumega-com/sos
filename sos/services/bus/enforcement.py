"""Bus message enforcement — schema validation at the publish boundary.

Purpose: kill the flat-identity bug class structurally. Every bus message
that carries a known v1 type must validate against its JSON Schema before
reaching Redis. Unknown / legacy types pass through unchanged (tolerance
window during v0.4 migration — legacy "chat" and "broadcast" types will
migrate to "send" in a follow-up sprint).

Error codes emitted:
  SOS-4001  Schema validation failed (message dict doesn't parse as BusMessage)
  SOS-4002  Type known in our v1 catalog but envelope is malformed
  SOS-4003  Source missing / not in agent:<name> shape

Call sites (current):
  sos/mcp/sos_mcp_sse.py:845   — send handler
  sos/mcp/sos_mcp_sse.py:928   — broadcast handler
  sos/bus/bridge.py:272,287,306 — HTTP bridge handlers
  sos/mcp/sos_mcp.py:239,299   — deprecated MCP variants
  sos/mcp/redis_bus.py:204,236 — deprecated redis bus helpers

Wiring status:
  2026-04-17: sos_mcp_sse (primary MCP gateway) wired.
  Remaining: bridge.py, sos_mcp.py, redis_bus.py — wired in follow-up sprint
  alongside "chat" → "send" and "broadcast" → "send"-to-channel migration.
"""
from __future__ import annotations

import logging
from typing import Any

from sos.contracts.errors import (
    BusValidationError,
    EnvelopeError,
    SourcePatternError,
    UnknownTypeError,
)
from sos.contracts.messages import MessageType, parse_message

logger = logging.getLogger(__name__)


class MessageValidationError(ValueError):
    """Raised when a v1-typed message fails schema validation.

    Kept for backward compatibility with call sites that catch this class.
    New code should catch the typed SOSError subclasses directly
    (BusValidationError, EnvelopeError, SourcePatternError, UnknownTypeError).
    """

    def __init__(self, code: str, message: str, original_type: str | None = None) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.original_type = original_type


# Known v1 message types (must match the Literal in sos.contracts.messages).
# Squad/kernel event types use dot-separated names per SQUAD_EVENTS in
# sos/contracts/squad.py. Bus protocol types (announce, send, wake, ask,
# agent_joined) keep their original names.
_V1_TYPES: set[str] = {
    "announce",
    "send",
    "wake",
    "ask",
    "task.created",
    "task.claimed",
    "task.completed",
    "task.scored",
    "task.routed",
    "task.failed",
    "skill.executed",
    "agent_joined",
}


def is_v1_type(msg_type: str | None) -> bool:
    """True if this message type is in our v1 schema catalog."""
    return msg_type in _V1_TYPES


def enforce(msg_dict: dict[str, Any]) -> dict[str, Any]:
    """Validate a bus message dict before XADD.

    Contract (v0.4.0 — strict):
      - msg['type'] must be one of the 11 v1 types; otherwise raise
        MessageValidationError with SOS-4004 (unknown type).
      - The message must parse against its v1 schema via parse_message().
        On failure, raise MessageValidationError with SOS-4001.
      - If msg has no 'type' field at all, raise MessageValidationError
        with SOS-4002 (envelope malformed).

    The legacy-tolerant pass-through window ended with v0.4.0. Legacy
    callers must migrate to v1 types via sos_msg()'s built-in mapping
    ("chat" → "send", "broadcast" → "send" with channel target).

    Returns the input dict on success.
    Raises MessageValidationError on validation failure.
    """
    msg_type = msg_dict.get("type")

    if not msg_type:
        typed_exc = EnvelopeError(
            "message envelope has no 'type' field",
        )
        raise MessageValidationError(
            typed_exc.code,
            typed_exc.message,
        ) from typed_exc

    if not is_v1_type(msg_type):
        typed_exc = UnknownTypeError(
            f"unknown message type {msg_type!r}; expected one of: "
            + ", ".join(sorted(_V1_TYPES)),
            details={"original_type": msg_type},
        )
        err = MessageValidationError(
            typed_exc.code,
            typed_exc.message,
            original_type=msg_type,
        )
        raise err from typed_exc

    # Known v1 type — full schema validation.
    try:
        parsed = parse_message(msg_dict)
    except Exception as exc:
        typed_exc = BusValidationError(
            f"v1 type '{msg_type}' failed schema validation: {exc}",
            details={"original_type": msg_type},
        )
        raise MessageValidationError(
            typed_exc.code,
            typed_exc.message,
            original_type=msg_type,
        ) from typed_exc

    # Source sanity — pattern is enforced by Pydantic; defense in depth here.
    if not parsed.source or not parsed.source.startswith("agent:"):
        typed_exc = SourcePatternError(
            f"source field missing or malformed: {parsed.source!r}",
            details={"original_type": msg_type},
        )
        raise MessageValidationError(
            typed_exc.code,
            typed_exc.message,
            original_type=msg_type,
        ) from typed_exc

    return msg_dict


def enforce_or_log(msg_dict: dict[str, Any]) -> dict[str, Any]:
    """Soft-enforce variant for gradual rollout.

    Same as enforce() but on validation failure, logs a warning at ERROR level
    and returns the original dict instead of raising. Use this at call sites
    that cannot yet tolerate hard rejection (e.g. legacy producers during
    migration). Target: convert all call sites to enforce() by v0.4.0 release.
    """
    try:
        return enforce(msg_dict)
    except MessageValidationError as exc:
        logger.error(
            "bus message validation failed (soft): code=%s type=%s err=%s",
            exc.code,
            exc.original_type,
            exc.message,
        )
        return msg_dict
