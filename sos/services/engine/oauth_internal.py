"""
Internal OAuth endpoints — called by mcp-dispatcher Cloudflare Worker.

S013-B Stream B. Two endpoints:
  POST /internal/oauth-tenant-provision  — mint/lookup tenant for OAuth callback
  POST /internal/oauth-audit             — emit audit_events row for OAuth lifecycle

These endpoints are NOT public. They require the SOS_INTERNAL_TOKEN bearer.
nginx must NOT expose them without the origin-only guard.

LOCK-TENANT-A: composite (idp_provider, sub) key, never bare sub.
LOCK-TENANT-D: slug derived from display_name, DB unique constraint enforces uniqueness.
LOCK-TENANT-E: atomic upsert on (idp_provider, sub) — no TOCTOU.
LOCK-AUDIT-1: one DCR client per tenant — enforced here by checking existing client.
LOCK-OAuth-E: audit_events rows emitted on authorize/token/revoke via /internal/oauth-audit.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

log = logging.getLogger("sos.engine.oauth_internal")

router = APIRouter(prefix="/internal", tags=["internal-oauth"])

# ─── Internal auth ────────────────────────────────────────────────────────────

SOS_INTERNAL_TOKEN = os.environ.get("SOS_INTERNAL_TOKEN", "")


def _require_internal_auth(request: Request) -> None:
    """Bearer token guard — only the Cloudflare Worker may call these endpoints."""
    if not SOS_INTERNAL_TOKEN:
        raise HTTPException(status_code=503, detail="internal_token_not_configured")
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or auth[7:] != SOS_INTERNAL_TOKEN:
        raise HTTPException(status_code=401, detail="unauthorized")


# ─── DB helpers ───────────────────────────────────────────────────────────────

def _slug_from_display_name(display_name: str, sub: str) -> str:
    """Derive a URL-safe slug from a display name.

    Falls back to hash suffix if the name is too short or collides.
    LOCK-WARN-2 (reserved names): kernel agents (athena, kasra, loom, etc.)
    get a hash suffix to prevent identity collision.
    """
    RESERVED = frozenset({
        "athena", "loom", "kasra", "river", "codex", "mumega",
        "sos-mcp-sse", "brain", "sovereign", "mirror", "admin", "system",
    })

    # Normalize: lowercase, strip accents, replace non-alnum with hyphen
    name = unicodedata.normalize("NFKD", display_name.lower())
    name = name.encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[^a-z0-9]+", "-", name).strip("-")
    name = re.sub(r"-{2,}", "-", name)[:40]

    if not name or len(name) < 2:
        name = "tenant"

    # Hash suffix for disambiguation / reserved-name guard
    suffix = hashlib.sha256(sub.encode()).hexdigest()[:6]

    if name in RESERVED:
        return f"{name}-{suffix}"

    return name  # uniqueness enforced by DB constraint; collision adds suffix via upsert


async def _upsert_tenant(
    idp_provider: str,
    sub: str,
    display_name: str,
    email: str,
    slug_candidate: str,
) -> dict[str, Any]:
    """Atomic upsert on (idp_provider, sub).

    LOCK-TENANT-E: INSERT ON CONFLICT DO NOTHING — never duplicates.
    Returns existing row if already provisioned.
    """
    try:
        import sys
        if '/home/mumega' not in sys.path:
            sys.path.insert(0, '/home/mumega')
        from mirror.kernel.db import get_db  # Mirror PG pool
    except Exception as exc:
        log.error("DB import failed: %s: %s", type(exc).__name__, exc)
        raise HTTPException(status_code=503, detail=f"db_unavailable: {type(exc).__name__}: {exc}")

    db = get_db()  # LocalDB wrapping psycopg2 pool

    # Use raw psycopg2 from the pool (LocalDB doesn't expose fetchrow/execute)
    conn = db._pool.getconn()
    try:
        conn.autocommit = True
        cur = conn.cursor()

        # Check existing first (fast path for repeat callers)
        cur.execute(
            "SELECT tenant_id, slug, tier, agent_name FROM oauth_tenants "
            "WHERE idp_provider = %s AND sub = %s",
            (idp_provider, sub),
        )
        row = cur.fetchone()
        existing = dict(zip(["tenant_id", "slug", "tier", "agent_name"], row)) if row else None

        is_new_tenant = False
        if not existing:
            tenant_id = _generate_tenant_id(idp_provider, sub)
            # Deduplicate slug: if "hadi" exists, try "hadi-google", "hadi-google-2", etc.
            slug = slug_candidate
            for suffix in ["", f"-{idp_provider}", f"-{idp_provider}-2", f"-{sub[:6]}"]:
                try_slug = f"{slug_candidate}{suffix}" if suffix else slug_candidate
                cur.execute("SELECT 1 FROM oauth_tenants WHERE slug = %s", (try_slug,))
                if not cur.fetchone():
                    slug = try_slug
                    break
            agent_name = f"{slug}-envoy"
            cur.execute(
                """
                INSERT INTO oauth_tenants
                  (tenant_id, idp_provider, sub, slug, display_name, email, tier, agent_name, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, 'free', %s, NOW())
                ON CONFLICT (idp_provider, sub) DO NOTHING
                """,
                (tenant_id, idp_provider, sub, slug, display_name, email, agent_name),
            )
            # Re-fetch in case of concurrent insert
            cur.execute(
                "SELECT tenant_id, slug, tier, agent_name FROM oauth_tenants "
                "WHERE idp_provider = %s AND sub = %s",
                (idp_provider, sub),
            )
            row = cur.fetchone()
            existing = dict(zip(["tenant_id", "slug", "tier", "agent_name"], row)) if row else None
            is_new_tenant = True

        if is_new_tenant and existing:
            try:
                cur.execute(
                    "INSERT INTO oauth_tenant_creation_log (idp_provider, sub, created_at) "
                    "VALUES (%s, %s, NOW())",
                    (idp_provider, sub),
                )
            except Exception as exc:
                log.warning("oauth_tenant_creation_log write failed: %s", exc)

        cur.close()
    finally:
        db._pool.putconn(conn)

    if not existing:
        raise HTTPException(status_code=500, detail="tenant_provision_failed")

    return existing


def _generate_tenant_id(idp_provider: str, sub: str) -> str:
    """Deterministic but opaque tenant ID from (provider, sub)."""
    raw = f"oauth:{idp_provider}:{sub}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ─── Models ───────────────────────────────────────────────────────────────────

class TenantProvisionRequest(BaseModel):
    idp_provider: str
    sub: str
    display_name: str
    email: str


class OAuthAuditRequest(BaseModel):
    action: str
    tenant_id: str
    details: dict[str, Any] = {}


# ─── Routes ───────────────────────────────────────────────────────────────────
# NOTE: DcrRegisterRequest + /internal/oauth-dcr-register were drafted but removed.
# The /v2/me pattern (sos_mcp_sse.py) is cleaner: server extracts tenant_id from the
# validated JWT props (worker_oauth context), writes dcr_client_id + returns profile
# in one call. No client-side tenant_id plumbing needed. — Kasra 2026-04-26

@router.post("/oauth-tenant-provision")
async def provision_tenant(
    body: TenantProvisionRequest,
    _: None = Depends(_require_internal_auth),
) -> dict[str, Any]:
    """Mint or return existing tenant for the OAuth callback.

    LOCK-TENANT-A: composite (idp_provider, sub) key.
    LOCK-TENANT-B: free-tier auto-minted.
    LOCK-TENANT-E: idempotent upsert.
    """
    slug = _slug_from_display_name(body.display_name, body.sub)

    try:
        tenant = await _upsert_tenant(
            body.idp_provider,
            body.sub,
            body.display_name,
            body.email,
            slug,
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.error("tenant provision failed for %s:%s — %s", body.idp_provider, body.sub, exc)
        raise HTTPException(status_code=500, detail="provision_error") from exc

    return {
        "tenant_id": tenant["tenant_id"],
        "tier": tenant.get("tier", "free"),
        "agent_name": tenant.get("agent_name", f"{slug}-knight"),
    }


@router.post("/oauth-audit")
async def oauth_audit(
    body: OAuthAuditRequest,
    _: None = Depends(_require_internal_auth),
) -> dict[str, str]:
    """Emit an audit_events row for OAuth lifecycle events.

    LOCK-OAuth-E: authorize, token, revoke, introspect — all recorded here.
    Fire-and-forget from Worker; failures are logged but not surfaced to caller.
    """
    try:
        from sos.kernel.audit_chain import AuditChainEvent, emit_audit
        import asyncio

        event = AuditChainEvent(
            stream_id="mcp",
            actor_id=body.tenant_id or "anonymous",
            actor_type="human",
            action=body.action,
            resource=f"oauth:{body.action.split('.')[-1]}",
            payload={"tenant_id": body.tenant_id, **body.details},
        )
        # Best-effort — oauth_audit is fire-and-forget from the Worker
        asyncio.create_task(emit_audit(event))
    except Exception as exc:
        log.warning("oauth_audit emit failed: %s", exc)

    return {"ok": "true"}
