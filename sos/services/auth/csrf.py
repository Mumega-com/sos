"""CSRF protection — Sprint 011 OmniB.

LOCK-3: SameSite=Strict + Origin allowlist + HMAC double-submit token.

Subdomain attack defense: SameSite alone doesn't prevent tenant-a.mumega.com
from making credentialed requests to api.mumega.com. Origin check + HMAC
token required.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from typing import Any

log = logging.getLogger("sos.auth.csrf")

_CSRF_SECRET = os.environ.get("CSRF_SECRET", "")
_ALLOWED_ORIGINS = frozenset(
    os.environ.get("CSRF_ALLOWED_ORIGINS", "https://mumega.com,https://api.mumega.com").split(",")
)


class CsrfError(RuntimeError):
    """CSRF validation failed."""


def generate_csrf_token(session_id: str) -> str:
    """Generate HMAC-signed CSRF token bound to session.

    Token = random_nonce.hmac(secret, session_id + nonce)
    Client sends this in X-CSRF-Token header on mutations.
    """
    secret = _CSRF_SECRET or os.environ.get("CSRF_SECRET", "")
    if not secret:
        raise RuntimeError("CSRF_SECRET not set")
    nonce = secrets.token_hex(16)
    sig = hmac.new(secret.encode(), f"{session_id}:{nonce}".encode(), hashlib.sha256).hexdigest()[:16]
    return f"{nonce}.{sig}"


def verify_csrf_token(session_id: str, token: str) -> bool:
    """Verify CSRF token matches session.

    Raises CsrfError on failure.
    """
    secret = _CSRF_SECRET or os.environ.get("CSRF_SECRET", "")
    if not secret:
        raise RuntimeError("CSRF_SECRET not set")

    if "." not in token:
        raise CsrfError("Malformed CSRF token")

    nonce, sig = token.rsplit(".", 1)
    expected_sig = hmac.new(
        secret.encode(), f"{session_id}:{nonce}".encode(), hashlib.sha256
    ).hexdigest()[:16]

    if not hmac.compare_digest(sig, expected_sig):
        raise CsrfError("Invalid CSRF token signature")

    return True


def check_origin(origin: str | None, referer: str | None = None) -> bool:
    """Verify Origin header is in allowlist.

    Raises CsrfError if Origin is present but not allowed.
    If Origin is absent, check Referer as fallback.
    """
    allowed = _ALLOWED_ORIGINS | frozenset(
        os.environ.get("CSRF_ALLOWED_ORIGINS", "").split(",")
    )

    if origin:
        if origin not in allowed:
            raise CsrfError(f"Origin {origin!r} not in allowed list")
        return True

    # No Origin header — check Referer as fallback
    if referer:
        from urllib.parse import urlparse
        parsed = urlparse(referer)
        ref_origin = f"{parsed.scheme}://{parsed.netloc}"
        if ref_origin not in allowed:
            raise CsrfError(f"Referer origin {ref_origin!r} not in allowed list")
        return True

    # Neither Origin nor Referer — allow for same-origin requests
    # (browsers always send Origin on cross-origin; absence = same-origin)
    return True
