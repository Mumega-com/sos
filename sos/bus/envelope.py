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
from typing import Any, Callable
from uuid import uuid4

CANONICAL_VERSION = "1.0"
# A signed envelope bumps the version so a 1.0↔1.1 swap invalidates the signature
# (version is inside the signed field set). Verification is OPT-IN and accept+flag:
# unsigned 1.0 envelopes remain valid (migration), receivers gate on the returned
# `signature` status rather than this module rejecting. Real per-agent keys arrive
# with #187 (Hadi-gated identity mint); #185 ships only the verify mechanism.
SIGNED_VERSION = "1.1"

# The fields covered by the signature. `payload` is already a canonical JSON string
# here, so the body is bound too. `project` (the TENANT-SCOPE slug) MUST be signed —
# else a captured signed message can be re-scoped to another tenant with the sig
# intact (cross-tenant injection, the S180 class). `sig` itself is necessarily
# excluded. Absent optional fields (no project) are simply omitted from the bytes.
_SIGNED_FIELDS = ("id", "type", "source", "target", "payload", "timestamp", "version", "project")


def signed_message_bytes(env: dict[str, str]) -> bytes:
    """Deterministic bytes a signature is computed/verified over.

    Sorted-key, whitespace-free JSON of the signed field subset, so the SENDER
    (build) and any RECEIVER (parse) derive the identical message from the same
    Redis-stringified fields. Stable across processes — no clock, no ordering deps.
    """
    subset = {k: env[k] for k in _SIGNED_FIELDS if k in env}
    return json.dumps(subset, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _verify_signature(
    fields: dict[str, str],
    resolver: Callable[[str], str | None] | None,
) -> str:
    """Classify an envelope's signature. Pure status — NEVER raises, never gates.

    Status: unsigned / unverified / no_key / valid / invalid / error. A resolver or
    crypto failure degrades to ``"error"`` (parse stays tolerant — a flaky key
    registry must not crash a receiver's message loop), never propagates.
    """
    sig = fields.get("sig")
    if not sig:
        return "unsigned"
    if resolver is None:
        return "unverified"
    try:
        # Resolve the key by the TOP-LEVEL source — that is the signed identity (the
        # payload.source mirror is convenience and is NOT what binds the signature).
        pub = resolver(fields.get("source", ""))
        if not pub:
            return "no_key"
        from sos.kernel import crypto  # lazy — unsigned/unverified paths stay zero-dep

        return "valid" if crypto.verify(pub, signed_message_bytes(fields), sig) else "invalid"
    except Exception:
        # Resolver raised (registry down/timeout) or crypto blew up on a malformed
        # key/sig — fail closed to a non-trusted status, do not kill the loop.
        return "error"


def build(
    *,
    msg_type: str,
    source: str,
    target: str,
    text: str,
    project: str | None = None,
    extras: dict[str, Any] | None = None,
    message_id: str | None = None,
    signing_key: str | None = None,
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
        signing_key: OPTIONAL Ed25519 private key (url-safe b64, from
            ``sos.kernel.crypto.generate_keypair``). When given, the envelope is
            signed: version → ``SIGNED_VERSION`` and a ``sig`` field is added over
            ``signed_message_bytes``. Omit it and the envelope is byte-identical to
            the legacy unsigned shape (no new dependency is imported).

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
        "version": SIGNED_VERSION if signing_key else CANONICAL_VERSION,
    }
    # project must be set BEFORE signing — it is a signed field (tenant scope).
    if project:
        envelope["project"] = project
    if signing_key:
        # Lazy import — the unsigned path keeps envelope.py dependency-free.
        from sos.kernel import crypto

        envelope["sig"] = crypto.sign(signing_key, signed_message_bytes(envelope))
    return envelope


def parse(
    fields: dict[str, str],
    verify_key_resolver: Callable[[str], str | None] | None = None,
) -> dict[str, Any]:
    """Parse a redis-stream fields dict into a flat, receiver-friendly shape.

    TOLERANT of three failure modes so drift degrades, not drops:
      1. ``payload`` is valid JSON dict       → standard path.
      2. ``payload`` is a raw string          → wrapped as ``{"text": payload}``.
      3. ``payload`` is missing / empty       → empty payload.
      4. ``payload`` is JSON non-dict         → coerced to ``{"text": str(x)}``.

    Signature (accept+flag — NEVER rejects here; the receiver decides):
      ``verify_key_resolver`` maps a top-level ``source`` ("agent:<name>") to that
      sender's Ed25519 PUBLIC key (url-safe b64) or None. The returned ``signature``
      field is one of:
        - ``"unsigned"``   — no ``sig`` on the envelope (legacy 1.0 / un-keyed sender).
        - ``"unverified"`` — signed, but no resolver was supplied to check it.
        - ``"no_key"``     — signed, but the resolver has no public key for the sender.
        - ``"valid"`` / ``"invalid"`` — checked against the sender's key.
    A receiver enforces policy on this status; this module does not gate traffic.
    Downgrade note for an ENFORCING receiver: a ``version`` >= ``SIGNED_VERSION``
    with ``signature == "unsigned"`` means the ``sig`` was stripped — treat it as a
    policy failure, NOT a legacy sender. ``"error"`` and ``"no_key"`` are likewise
    non-trusted.

    Returns a dict with keys:
      type, source, target, text, timestamp (float|None), id, project, version,
      signature, extras
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

    field_source = fields.get("source", "")
    payload_source = payload_obj.get("source")
    source = payload_source or field_source
    raw_ts = payload_obj.get("timestamp")
    try:
        ts_float: float | None = float(raw_ts) if raw_ts is not None else None
    except (TypeError, ValueError):
        ts_float = None

    reserved = {"text", "source", "timestamp"}
    extras = {k: v for k, v in payload_obj.items() if k not in reserved}

    signature = _verify_signature(fields, verify_key_resolver)
    if signature == "valid":
        # The signature binds the TOP-LEVEL source. A signed envelope whose payload
        # claims a DIFFERENT source is an identity-spoof (valid sig for sender A,
        # payload says sender B) → reject. Otherwise the authenticated identity is
        # the signed source — never surface the payload mirror as authenticated.
        if payload_source is not None and payload_source != field_source:
            signature = "invalid"
        else:
            source = field_source

    return {
        "type": fields.get("type", ""),
        "source": source,
        "target": fields.get("target", ""),
        "text": payload_obj.get("text", ""),
        "timestamp": ts_float,
        "id": fields.get("id") or fields.get("message_id"),
        "project": fields.get("project"),
        "version": fields.get("version"),
        "signature": signature,
        "extras": extras,
    }


__all__ = [
    "build",
    "parse",
    "CANONICAL_VERSION",
    "SIGNED_VERSION",
    "signed_message_bytes",
]
