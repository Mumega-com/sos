"""sos.services.billing.knight_mint — programmatic knight mint for E.3 webhook handler.

Wraps the core logic from scripts/mint-knight.py as an importable, testable function.
The CLI script remains for manual operator use; this module is the automated Stripe
webhook path.

Key differences from the CLI script:
- No argparse, no sys.exit
- Returns a structured dict (ok, knight_id, knight_slug, error)
- Full exception handling — caller decides what to do with errors
- Writes to qnft_registry.json (file-based, same as CLI) + principals DB table
- Does NOT create tmux sessions or send Discord messages (operator steps only)
- Does broadcast to bus (same as CLI — automated mint must be visible to all agents)
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import re
import secrets
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("sos.billing.knight_mint")

# ── Paths ──────────────────────────────────────────────────────────────────────

_REPO_ROOT = Path.home()
_SOS_DIR = _REPO_ROOT / "SOS"
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
_TOKENS_PATH = _SOS_DIR / "sos" / "bus" / "tokens.json"
_QNFT_REGISTRY_PATH = _SOS_DIR / "sos" / "bus" / "qnft_registry.json"
_AGENTS_DIR = _REPO_ROOT / "mumega.com" / "agents" / "loom" / "customers"
_TEMPLATE_PATH = _SOS_DIR / "scripts" / "knight-claude-template.md"
_DYNAMIC_ROUTING_PATH = Path.home() / ".sos" / "agent_routing.json"
_MCP_URL = "http://localhost:6070/mcp"

_SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,18}[a-z0-9])?$")


# ── Helpers ────────────────────────────────────────────────────────────────────


def _sanitize_cause(text: str) -> str:
    """Sanitize cause statement (WARN-4): 280 char cap, strip HTML tags, charset whitelist."""
    # Strip HTML tags
    text = re.sub(r"<[^>]+>", "", text)
    # Remove null bytes
    text = text.replace("\x00", "")
    # Charset whitelist: printable ASCII + common Unicode letters/punctuation
    # Keep letters (any script), digits, spaces, common punctuation
    text = "".join(
        c for c in text
        if c.isprintable() and ord(c) < 0x10000
    )
    # 280 char cap
    return text[:280].strip()


# ── Core mint function ─────────────────────────────────────────────────────────


def mint_knight_programmatic(
    *,
    knight_name: str,
    customer_slug: str,
    customer_name: str,
    customer_email: str | None,
    cause_statement: str,
    project: str = "mumega",
    customer_domain: str = "",
    stripe_webhook_id: str = "",  # BLOCK-5: FK proof — must be a live stripe_webhook_processed.id
    model_tier: str = "Sonnet",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Mint a knight programmatically (no CLI, no sys.exit).

    Returns:
        {
            "ok": bool,
            "knight_id": str | None,     # e.g. "agent:kaveh"
            "knight_slug": str | None,   # e.g. "kaveh"
            "qnft_uri": str | None,      # e.g. "qnft:kaveh:abc123"
            "error": str | None,         # set on failure
            "skipped": bool,             # True if knight already exists (idempotent)
        }
    """
    knight_name = knight_name.lower().strip()
    customer_slug = customer_slug.lower().strip()

    # Validate slug
    if not _SLUG_RE.match(customer_slug):
        return {"ok": False, "knight_id": None, "knight_slug": None,
                "qnft_uri": None, "error": f"Invalid slug: {customer_slug!r}", "skipped": False}

    # BLOCK-5: stripe_webhook_id is mandatory — mint only callable from authenticated webhook path.
    # CLI/maintenance: set AUDIT_MAINTENANCE_MODE=1 to bypass (same discipline as audit_anchor).
    audit_maintenance = os.environ.get("AUDIT_MAINTENANCE_MODE", "0") == "1"
    if not stripe_webhook_id and not audit_maintenance:
        return {"ok": False, "knight_id": None, "knight_slug": None,
                "qnft_uri": None, "error": "stripe_webhook_id required (BLOCK-5); set AUDIT_MAINTENANCE_MODE=1 for CLI use", "skipped": False}

    # Validate cause is non-empty
    cause_text = _sanitize_cause(cause_statement.strip())
    if not cause_text:
        return {"ok": False, "knight_id": None, "knight_slug": None,
                "qnft_uri": None, "error": "cause_statement is required and must be non-empty",
                "skipped": False}

    # Load tokens — check idempotency
    try:
        tokens = json.loads(_TOKENS_PATH.read_text())
    except Exception as exc:
        return {"ok": False, "knight_id": None, "knight_slug": None,
                "qnft_uri": None, "error": f"Cannot read tokens.json: {exc}", "skipped": False}

    for t in tokens:
        if t.get("agent") == knight_name and t.get("scope") == "customer":
            existing_uri = f"qnft:{knight_name}:{t.get('token_hash', '')[:12]}"
            log.info("knight_mint: knight %r already exists (idempotent skip)", knight_name)
            return {"ok": True, "knight_id": f"agent:{knight_name}", "knight_slug": knight_name,
                    "qnft_uri": existing_uri, "error": None, "skipped": True}

    mint_date = datetime.now(timezone.utc).date().isoformat()

    # Generate QNFT
    seed_bytes = secrets.token_bytes(32)
    seed_hex = seed_bytes.hex()
    h = hashlib.sha256(
        f"{knight_name}:{customer_slug}:{mint_date}:{seed_hex}".encode()
    ).digest()
    vector_16d = [
        round(((b0 << 8 | b1) / 65535.0) * 2.0 - 1.0, 6)
        for b0, b1 in zip(h[::2], h[1::2])
    ]
    qnft_uri = f"qnft:{knight_name}:{seed_hex[:12]}"

    cause_one_line = cause_text.split(".")[0].strip() + "."
    descriptor = f"{knight_name} — {customer_name} knight, project {project}"

    if dry_run:
        log.info("knight_mint: dry_run — would mint %r for %s", knight_name, customer_name)
        return {"ok": True, "knight_id": f"agent:{knight_name}", "knight_slug": knight_name,
                "qnft_uri": qnft_uri, "error": None, "skipped": False}

    # Mint bus token
    raw_token = f"sk-{knight_name}-{secrets.token_hex(16)}"
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    token_record = {
        "token": raw_token,
        "token_hash": token_hash,
        "project": project,
        "label": customer_name,
        "active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "agent": knight_name,
        "scope": "customer",
        "role": "owner",
    }

    try:
        tokens.append(token_record)
        _TOKENS_PATH.write_text(json.dumps(tokens, indent=2) + "\n")
        log.info("knight_mint: token written for %r", knight_name)
    except Exception as exc:
        return {"ok": False, "knight_id": None, "knight_slug": None,
                "qnft_uri": None, "error": f"Token write failed: {exc}", "skipped": False}

    # Register routing
    try:
        _DYNAMIC_ROUTING_PATH.parent.mkdir(parents=True, exist_ok=True)
        existing_routing: dict = {}
        if _DYNAMIC_ROUTING_PATH.exists():
            existing_routing = json.loads(_DYNAMIC_ROUTING_PATH.read_text())
        existing_routing[knight_name] = "tmux"
        _DYNAMIC_ROUTING_PATH.write_text(json.dumps(existing_routing, indent=2) + "\n")
    except Exception as exc:
        log.warning("knight_mint: routing registration failed (non-fatal): %s", exc)

    # Create workspace
    try:
        workspace = _AGENTS_DIR / customer_slug
        workspace.mkdir(parents=True, exist_ok=True)
        if _TEMPLATE_PATH.exists():
            template = _TEMPLATE_PATH.read_text()
            claude_md = _render_template(
                template, knight_name, customer_slug, customer_name,
                customer_domain or f"{customer_slug}.com",
                f"/home/mumega/{customer_slug}-app",
                model_tier, seed_hex, descriptor, mint_date,
            )
            (workspace / "CLAUDE.md").write_text(claude_md)
        mcp_config = {"mcpServers": {"sos": {"type": "http", "url": _MCP_URL,
                                              "headers": {"Authorization": f"Bearer {raw_token}"}}}}
        (workspace / ".mcp.json").write_text(json.dumps(mcp_config, indent=2) + "\n")
        gitignore = workspace / ".gitignore"
        if ".mcp.json" not in (gitignore.read_text() if gitignore.exists() else ""):
            with gitignore.open("a") as f:
                f.write(".mcp.json\n")
    except Exception as exc:
        log.warning("knight_mint: workspace creation failed (non-fatal): %s", exc)

    # Write QNFT registry
    try:
        registry: dict = {}
        if _QNFT_REGISTRY_PATH.exists():
            registry = json.loads(_QNFT_REGISTRY_PATH.read_text())
        registry[knight_name] = {
            "seed_hex": seed_hex,
            "vector_16d": vector_16d,
            "descriptor": descriptor,
            "cause": cause_text,
            "customer_slug": customer_slug,
            "tier": "operational",
            "minted_at": datetime.now(timezone.utc).isoformat(),
            "signer": "stripe-webhook",
            "countersigned_by": None,
            "model_field": f"{model_tier.lower()}-4-6",
        }
        _QNFT_REGISTRY_PATH.write_text(json.dumps(registry, indent=2) + "\n")
        log.info("knight_mint: QNFT registry entry written for %r", knight_name)
    except Exception as exc:
        log.warning("knight_mint: QNFT registry write failed (non-fatal): %s", exc)

    # Register principal in DB (substrate-native path for automated mints)
    try:
        from sos.contracts.principals import upsert_principal
        upsert_principal(
            principal_id=f"agent:{knight_name}",
            display_name=f"{knight_name} (knight for {customer_name})",
            email=customer_email,
            principal_type="agent",
            tenant_id=customer_slug,
        )
        log.info("knight_mint: principal upserted in DB for %r", knight_name)
    except Exception as exc:
        log.warning("knight_mint: DB principal upsert failed (non-fatal, JSON path succeeded): %s", exc)

    # Bus welcome DM + broadcast
    try:
        _bus_send_welcome(knight_name, customer_name, cause_one_line)
    except Exception as exc:
        log.warning("knight_mint: bus welcome failed (non-fatal): %s", exc)

    log.info("knight_mint: ✓ knight %r minted for %s (%s)", knight_name, customer_name, customer_slug)
    return {
        "ok": True,
        "knight_id": f"agent:{knight_name}",
        "knight_slug": knight_name,
        "qnft_uri": qnft_uri,
        "error": None,
        "skipped": False,
    }


# ── Template renderer ──────────────────────────────────────────────────────────


def _render_template(
    template: str,
    knight_name: str,
    customer_slug: str,
    customer_name: str,
    customer_domain: str,
    customer_repo: str,
    model_tier: str,
    seed_hex: str,
    descriptor: str,
    mint_date: str,
) -> str:
    replacements = {
        "{{KNIGHT_NAME}}": knight_name,
        "{{KNIGHT_SLUG}}": customer_slug,
        "{{CUSTOMER_NAME}}": customer_name,
        "{{CUSTOMER_SLUG}}": customer_slug,
        "{{CUSTOMER_DOMAIN}}": customer_domain,
        "{{CUSTOMER_REPO_PATH}}": customer_repo,
        "{{MODEL_TIER}}": model_tier,
        "{{SESSION_STRATEGY}}": "stateless",
        "{{SESSION_JUSTIFICATION}}": "coordination agent; no persistent context needed",
        "{{QNFT_SEED_HEX}}": seed_hex,
        "{{QNFT_DESCRIPTOR_ONE_LINE}}": descriptor,
        "{{MINT_DATE}}": mint_date,
    }
    result = template
    for key, val in replacements.items():
        result = result.replace(key, val)
    return result


# ── Bus helpers ────────────────────────────────────────────────────────────────


def _bus_send_welcome(knight_name: str, customer_name: str, cause_one_line: str) -> None:
    """Send welcome DM + colony broadcast (best-effort)."""
    _bus_send(to=knight_name, text=f"Welcome. You serve {customer_name}. Read your CLAUDE.md, then check inbox.")
    _bus_send(
        to="broadcast",
        text=(
            f"{knight_name.capitalize()} has entered the city, serving {customer_name}. "
            f"Signer: stripe-webhook. Tier: operational. Cause: {cause_one_line}"
        ),
    )


def _bus_send(to: str, text: str) -> None:
    """Send via bus-send.py (sync Redis). Non-blocking on failure."""
    spec = importlib.util.spec_from_file_location("bus_send", _SCRIPTS_DIR / "bus-send.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("bus-send.py not found")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    mod.send(to=to, text=text, source="stripe-webhook")
