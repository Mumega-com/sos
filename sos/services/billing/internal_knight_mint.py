"""Internal knight-mint — Sprint 008 S008-A / G76.

Mints knights for internal sales reps (no Stripe payment flow).
Reuses core identity primitives from knight_mint.py:
  16D vector, QNFT registry, principals DB, bus token, AvatarGenerator image.

Adds: Discord channel binding, deterministic seed, signer enum, env gate.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("sos.billing.internal_knight_mint")

_SOS_DIR = Path("/home/mumega/SOS")
_TOKENS_PATH = _SOS_DIR / "sos" / "bus" / "tokens.json"
_QNFT_REGISTRY_PATH = _SOS_DIR / "sos" / "bus" / "qnft_registry.json"
_DYNAMIC_ROUTING_PATH = Path.home() / ".sos" / "agent_routing.json"

_VALID_SIGNERS = frozenset({"loom", "hadi"})
_SNOWFLAKE_RE = re.compile(r"^\d{17,20}$")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class InternalMintModeDisabled(RuntimeError):
    """AUDIT_INTERNAL_MINT_MODE env not set."""


class MissingMintArgError(ValueError):
    """Required argument missing for internal knight mint."""


class InvalidSignerError(ValueError):
    """Signer not in allowed set."""


class InvalidChannelIdError(ValueError):
    """Discord channel ID is not a valid snowflake."""


# ---------------------------------------------------------------------------
# Core: mint_internal_knight
# ---------------------------------------------------------------------------


def mint_internal_knight(
    name: str,
    role: str,
    discord_channel_id: str,
    signer: str,
) -> dict[str, Any]:
    """Mint an internal knight for a sales rep.

    Args:
        name: Knight name (e.g., "gavin"). Will be suffixed with "-knight".
        role: Role descriptor (e.g., "closer", "activator").
        discord_channel_id: Discord channel ID to bind the knight to.
        signer: Who authorized the mint ("loom" or "hadi").

    Returns:
        Dict with ok, knight_id, knight_slug, qnft_uri, reason, error.

    Raises:
        InternalMintModeDisabled: if AUDIT_INTERNAL_MINT_MODE env not set.
        MissingMintArgError: if required arg is missing/empty.
        InvalidSignerError: if signer not in allowed set.
    """
    # ── Gate: env check ──
    if os.environ.get("AUDIT_INTERNAL_MINT_MODE", "0") != "1":
        raise InternalMintModeDisabled(
            "AUDIT_INTERNAL_MINT_MODE=1 required for internal knight mint"
        )

    # ── Validate args ──
    if not name or not name.strip():
        raise MissingMintArgError("name is required")
    if not role or not role.strip():
        raise MissingMintArgError("role is required")
    if not discord_channel_id or not discord_channel_id.strip():
        raise MissingMintArgError("discord_channel_id is required")
    if not signer or not signer.strip():
        raise MissingMintArgError("signer is required")

    signer = signer.strip().lower()
    if signer not in _VALID_SIGNERS:
        raise InvalidSignerError(
            f"signer must be one of {_VALID_SIGNERS}, got {signer!r}"
        )

    # BLOCK-1: validate Discord channel ID is a snowflake
    discord_channel_id = discord_channel_id.strip()
    if not _SNOWFLAKE_RE.match(discord_channel_id):
        raise InvalidChannelIdError(
            f"discord_channel_id must be a Discord snowflake (17-20 digits), got {discord_channel_id!r}"
        )

    knight_name = f"{name.strip().lower()}-knight"
    knight_id = f"agent:{knight_name}"

    # ── Duplicate guard: check principals DB ──
    try:
        from sos.contracts.principals import get_principal
        existing = get_principal(knight_id)
        if existing:
            log.info("internal_knight_mint: %s already exists (idempotent skip)", knight_id)
            return {
                "ok": True,
                "reason": "already_minted",
                "knight_id": knight_id,
                "knight_slug": knight_name,
                "qnft_uri": None,
                "error": None,
                "skipped": True,
            }
    except Exception:
        pass  # get_principal may not exist yet; fall through to token check

    # Fallback duplicate check via tokens.json
    try:
        tokens = json.loads(_TOKENS_PATH.read_text()) if _TOKENS_PATH.exists() else []
        for t in tokens:
            if t.get("agent") == knight_name and t.get("scope") == "customer":
                log.info("internal_knight_mint: %s token exists (idempotent skip)", knight_name)
                return {
                    "ok": True,
                    "reason": "already_minted",
                    "knight_id": knight_id,
                    "knight_slug": knight_name,
                    "qnft_uri": None,
                    "error": None,
                    "skipped": True,
                }
    except Exception as exc:
        log.warning("internal_knight_mint: tokens.json read failed: %s", exc)

    # ── Deterministic 16D vector from sha256(name:role) ──
    seed_bytes = hashlib.sha256(f"{knight_name}:{role}".encode()).digest()
    seed_hex = seed_bytes.hex()
    vector_16d = [
        round(((b0 << 8 | b1) / 65535.0) * 2.0 - 1.0, 6)
        for b0, b1 in zip(seed_bytes[::2], seed_bytes[1::2])
    ]

    mint_date = datetime.now(timezone.utc).date().isoformat()
    cause = f"Internal knight for {name} ({role}). Signer: {signer}."
    descriptor = f"{knight_name} — {role}, project mumega-internal"

    # ── WARN-1 fix: Register principal in DB FIRST (hard stop) ──
    try:
        from sos.contracts.principals import upsert_principal
        upsert_principal(
            principal_id=knight_id,
            display_name=f"{knight_name} ({role} for {name})",
            email=None,
            principal_type="agent",
            tenant_id="mumega-internal",
        )
    except Exception as exc:
        return {
            "ok": False, "reason": "principal_upsert_failed", "knight_id": None,
            "knight_slug": None, "qnft_uri": None, "error": str(exc), "skipped": False,
        }

    # ── Mint bus token (BLOCK-2: file lock for atomicity) ──
    raw_token = f"sk-{knight_name}-{secrets.token_hex(16)}"
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    token_record = {
        "token": raw_token,
        "token_hash": token_hash,
        "project": "mumega-internal",
        "label": f"{name} ({role})",
        "active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "agent": knight_name,
        "scope": "customer",
        "role": "owner",
    }

    try:
        with open(_TOKENS_PATH, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                content = f.read()
                tokens = json.loads(content) if content.strip() else []
            except json.JSONDecodeError:
                tokens = []
            tokens.append(token_record)
            f.seek(0)
            f.truncate()
            f.write(json.dumps(tokens, indent=2) + "\n")
            fcntl.flock(f, fcntl.LOCK_UN)
    except FileNotFoundError:
        # First token — create file
        _TOKENS_PATH.write_text(json.dumps([token_record], indent=2) + "\n")
    except Exception as exc:
        return {
            "ok": False, "reason": "token_write_failed", "knight_id": None,
            "knight_slug": None, "qnft_uri": None, "error": str(exc), "skipped": False,
        }

    # ── Register routing ──
    try:
        _DYNAMIC_ROUTING_PATH.parent.mkdir(parents=True, exist_ok=True)
        existing_routing: dict = {}
        if _DYNAMIC_ROUTING_PATH.exists():
            existing_routing = json.loads(_DYNAMIC_ROUTING_PATH.read_text())
        existing_routing[knight_name] = "tmux"
        _DYNAMIC_ROUTING_PATH.write_text(json.dumps(existing_routing, indent=2) + "\n")
    except Exception as exc:
        log.warning("internal_knight_mint: routing registration failed (non-fatal): %s", exc)

    # ── Write QNFT registry ──
    try:
        registry: dict = {}
        if _QNFT_REGISTRY_PATH.exists():
            registry = json.loads(_QNFT_REGISTRY_PATH.read_text())
        registry[knight_name] = {
            "seed_hex": seed_hex,
            "vector_16d": vector_16d,
            "descriptor": descriptor,
            "cause": cause,
            "customer_slug": "mumega-internal",
            "tier": "operational",
            "minted_at": datetime.now(timezone.utc).isoformat(),
            "signer": signer,
            "countersigned_by": None,
            "model_field": "sonnet-4-6",
        }
        _QNFT_REGISTRY_PATH.write_text(json.dumps(registry, indent=2) + "\n")
    except Exception as exc:
        log.warning("internal_knight_mint: QNFT registry write failed (non-fatal): %s", exc)

    # ── Discord channel binding ──
    try:
        _bind_discord_channel(knight_id, discord_channel_id, signer)
    except Exception as exc:
        log.warning("internal_knight_mint: Discord binding failed (non-fatal): %s", exc)

    # ── Generate QNFT image + R2 upload (WARN-3 fix) ──
    qnft_r2_url: str | None = None
    try:
        from sos.services.billing.qnft_image import generate_qnft_image, upload_qnft_to_r2
        image_bytes = generate_qnft_image(knight_name, vector_16d, cause)
        log.info("internal_knight_mint: QNFT image generated (%d bytes)", len(image_bytes))
        # Upload to R2 (same bucket as webhook path)
        qnft_r2_url = upload_qnft_to_r2(knight_name, image_bytes)
        log.info("internal_knight_mint: QNFT uploaded to %s", qnft_r2_url)
    except Exception as exc:
        log.warning("internal_knight_mint: QNFT image/upload failed (non-fatal): %s", exc)

    qnft_uri = qnft_r2_url or f"qnft:{knight_name}:{seed_hex[:12]}"

    # ── Bus welcome ──
    try:
        from sos.services.billing.knight_mint import _bus_send_welcome
        _bus_send_welcome(knight_name, name, cause.split(".")[0] + ".")
    except Exception as exc:
        log.warning("internal_knight_mint: bus welcome failed (non-fatal): %s", exc)

    # ── BLOCK-3 fix: durable audit record BEFORE emit ──
    audit_payload = {
        "knight_id": knight_id,
        "signer": signer,
        "discord_channel_id": discord_channel_id,
        "action": "internal_knight_minted",
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    try:
        import psycopg2
        db_url = os.environ.get("MIRROR_DATABASE_URL") or os.environ.get("DATABASE_URL")
        if db_url:
            audit_conn = psycopg2.connect(db_url)
            try:
                with audit_conn:
                    with audit_conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO audit_events (stream_id, actor_id, actor_type, action, resource, payload) "
                            "VALUES (%s, %s, %s, %s, %s, %s)",
                            ("kernel", "billing", "service", "internal_knight_minted",
                             f"knight:{knight_id}", json.dumps(audit_payload)),
                        )
            finally:
                audit_conn.close()
    except Exception as exc:
        # Fallback: local file audit record
        marker_dir = _SOS_DIR / ".sprint_markers"
        marker_dir.mkdir(exist_ok=True)
        safe_kid = knight_id.replace(":", "_")
        ts = audit_payload["ts"][:19].replace(":", "-")
        (marker_dir / f"internal_knight_minted_{safe_kid}_{ts}.json").write_text(
            json.dumps(audit_payload, indent=2)
        )
        log.warning("internal_knight_mint: DB audit failed, wrote local marker: %s", exc)

    # ── Emit to bus (after durable audit) ──
    try:
        from sos.observability.sprint_telemetry import emit_internal_knight_minted
        emit_internal_knight_minted(knight_id, signer, discord_channel_id)
    except Exception as exc:
        log.warning("internal_knight_mint: emit failed (non-fatal, audit record exists): %s", exc)

    log.info(
        "internal_knight_mint: ✓ %s minted for %s (%s), channel=%s, signer=%s",
        knight_name, name, role, discord_channel_id, signer,
    )
    return {
        "ok": True,
        "reason": "minted",
        "knight_id": knight_id,
        "knight_slug": knight_name,
        "qnft_uri": qnft_uri,
        "vector_16d": vector_16d,
        "error": None,
        "skipped": False,
    }


def _bind_discord_channel(knight_id: str, discord_channel_id: str, bound_by: str) -> None:
    """Insert knight→Discord channel binding into mirror DB."""
    import psycopg2

    db_url = os.environ.get("MIRROR_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("MIRROR_DATABASE_URL not set")

    conn = psycopg2.connect(db_url)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO knight_discord_bindings (knight_id, discord_channel_id, bound_by) "
                    "VALUES (%s, %s, %s) ON CONFLICT (knight_id) DO NOTHING",
                    (knight_id, discord_channel_id, bound_by),
                )
    finally:
        conn.close()
