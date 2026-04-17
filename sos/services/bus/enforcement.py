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

from sos.contracts.messages import MessageType, parse_message

logger = logging.getLogger(__name__)


class MessageValidationError(ValueError):
    """Raised when a v1-typed message fails schema validation."""

    def __init__(self, code: str, message: str, original_type: str | None = None) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.original_type = original_type


# Known v1 message types (must match the Literal in sos.contracts.messages)
_V1_TYPES: set[str] = {
    "announce",
    "send",
    "wake",
    "ask",
    "task_created",
    "task_claimed",
    "task_completed",
    "agent_joined",
}


def is_v1_type(msg_type: str | None) -> bool:
    """True if this message type is in our v1 schema catalog."""
    return msg_type in _V1_TYPES


def enforce(msg_dict: dict[str, Any]) -> dict[str, Any]:
    """Validate a bus message dict before XADD.

    Contract:
      - If msg['type'] is one of the 8 v1 types, parse against schema.
        On failure, raise MessageValidationError with SOS-4001.
      - If msg['type'] is NOT in our v1 catalog (e.g. legacy "chat",
        "broadcast"), return unchanged. Tolerance window.
      - If msg has no 'type' field at all, raise MessageValidationError
        with SOS-4002 (envelope malformed).

    Returns the input dict on success (possibly normalized by Pydantic).

    Raises MessageValidationError on validation failure for known types.
    """
    msg_type = msg_dict.get("type")

    if not msg_type:
        raise MessageValidationError(
            "SOS-4002",
            "message envelope has no 'type' field",
        )

    if not is_v1_type(msg_type):
        # Legacy / unknown type — pass through for now.
        return msg_dict

    # Known v1 type — full schema validation.
    try:
        parsed = parse_message(msg_dict)
    except Exception as exc:
        # Re-raise as our typed error with a stable code.
        raise MessageValidationError(
            "SOS-4001",
            f"v1 type '{msg_type}' failed schema validation: {exc}",
            original_type=msg_type,
        ) from exc

    # Source sanity — the pattern is enforced by Pydantic, but we also want
    # defense in depth at this boundary.
    if not parsed.source or not parsed.source.startswith("agent:"):
        raise MessageValidationError(
            "SOS-4003",
            f"source field missing or malformed: {parsed.source!r}",
            original_type=msg_type,
        )

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
