"""Session cookie helpers for the dashboard.

These functions only deal with the cookie session: reading the cookie,
verifying the token inside it via sos.services.auth, and returning the
tenant dict that templates expect.  Raw token verification lives in
sos.services.auth — not here.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sos.services.auth import verify_bearer as _auth_verify_bearer

logger = logging.getLogger("dashboard")


def _auth_ctx_to_entry(token: str) -> dict[str, Any] | None:
    """Convert an AuthContext into the legacy dict shape the templates expect.

    Preserves the ``token`` key so the cookie round-trip and _tenant_from_cookie
    keep working without changes. Delegates all token verification to the
    canonical sos.services.auth module.
    """
    ctx = _auth_verify_bearer(f"Bearer {token}")
    if ctx is None:
        return None
    return {
        "token": token,
        "project": ctx.project,
        "tenant_slug": ctx.tenant_slug,
        "agent": ctx.agent,
        "label": ctx.label,
        "is_system": ctx.is_system,
        "is_admin": ctx.is_admin,
        "active": True,
    }


def _verify_token(token: str) -> dict[str, Any] | None:
    """Thin wrapper — delegates to sos.services.auth.verify_bearer.

    Kept for backwards compatibility: any caller that missed this migration
    still works unchanged. Internals now go through the canonical auth module.
    """
    return _auth_ctx_to_entry(token)


def _tenant_from_cookie(cookie_val: str | None) -> dict[str, Any] | None:
    if not cookie_val:
        return None
    try:
        data = json.loads(cookie_val)
        # Re-verify token is still active
        entry = _verify_token(data.get("token", ""))
        if entry:
            return data
    except Exception:
        pass
    return None


def _is_admin(tenant: dict[str, Any] | None) -> bool:
    """A tenant record is admin if it has no project scope (system token)."""
    if not tenant:
        return False
    return not tenant.get("project")
