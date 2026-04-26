"""MFA — TOTP enrollment + verification — Sprint 011 OmniA.

TOTP via pyotp (30s window, SHA1 default per RFC 6238).
Backup codes: 10 single-use, hashed at rest (SHA256), consumed atomically.
AC-7: backup code consumption satisfies MFA for that login only,
does NOT bypass MFA enrollment requirement.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import secrets
from typing import Any

import pyotp
import qrcode

log = logging.getLogger("sos.auth.mfa")

_MFA_PREFIX = "sos:mfa:"
_BACKUP_PREFIX = "sos:mfa_backup:"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class MfaNotEnrolledError(RuntimeError):
    """User has not enrolled in MFA."""


class MfaVerificationFailedError(RuntimeError):
    """TOTP code or backup code invalid."""


class MfaAlreadyEnrolledError(RuntimeError):
    """User already has MFA enrolled."""


# ---------------------------------------------------------------------------
# Redis helpers
# ---------------------------------------------------------------------------


def _get_redis():
    import redis
    pw = os.environ.get("REDIS_PASSWORD", "")
    return redis.Redis(host="localhost", port=6379, password=pw, decode_responses=True)


def _hash_backup_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------


def enroll_totp(user_id: str, issuer: str = "Mumega") -> dict[str, Any]:
    """Enroll user in TOTP MFA. Returns secret + QR code PNG bytes.

    Raises MfaAlreadyEnrolledError if user already enrolled.
    """
    r = _get_redis()
    key = f"{_MFA_PREFIX}{user_id}"

    if r.exists(key):
        raise MfaAlreadyEnrolledError(f"User {user_id} already has MFA enrolled")

    # Generate TOTP secret
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=user_id, issuer_name=issuer)

    # Generate QR code as PNG bytes
    qr = qrcode.make(uri)
    buf = io.BytesIO()
    qr.save(buf, format="PNG")
    qr_bytes = buf.getvalue()

    # Generate 10 backup codes (single-use, hashed at rest)
    backup_codes_plain: list[str] = []
    backup_codes_hashed: list[str] = []
    for _ in range(10):
        code = secrets.token_hex(4).upper()  # 8-char hex code
        backup_codes_plain.append(code)
        backup_codes_hashed.append(_hash_backup_code(code))

    # Store secret (NOT backup codes plaintext — hashes only)
    mfa_data = {
        "secret": secret,
        "enrolled_at": "",  # set on confirm
        "confirmed": False,
    }
    r.set(key, json.dumps(mfa_data))

    # Store hashed backup codes
    backup_key = f"{_BACKUP_PREFIX}{user_id}"
    r.delete(backup_key)
    for h in backup_codes_hashed:
        r.sadd(backup_key, h)

    log.info("mfa: TOTP enrollment initiated for user=%s", user_id)
    return {
        "secret": secret,
        "uri": uri,
        "qr_png_bytes": qr_bytes,
        "backup_codes": backup_codes_plain,  # shown ONCE to user, never stored plaintext
    }


def confirm_enrollment(user_id: str, totp_code: str) -> bool:
    """Confirm TOTP enrollment by verifying first code.

    User must enter a valid TOTP code to prove they've set up their authenticator.
    """
    r = _get_redis()
    key = f"{_MFA_PREFIX}{user_id}"
    raw = r.get(key)
    if not raw:
        raise MfaNotEnrolledError(f"No MFA enrollment found for {user_id}")

    data = json.loads(raw)
    secret = data["secret"]

    totp = pyotp.TOTP(secret)
    if not totp.verify(totp_code, valid_window=1):  # 30s window ± 1 step
        raise MfaVerificationFailedError("Invalid TOTP code during enrollment confirmation")

    data["confirmed"] = True
    from datetime import datetime, timezone
    data["enrolled_at"] = datetime.now(timezone.utc).isoformat()
    r.set(key, json.dumps(data))

    log.info("mfa: TOTP enrollment confirmed for user=%s", user_id)
    return True


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_totp(user_id: str, code: str) -> bool:
    """Verify a TOTP code for login.

    Raises MfaNotEnrolledError if not enrolled.
    Raises MfaVerificationFailedError if code is invalid.
    """
    r = _get_redis()
    key = f"{_MFA_PREFIX}{user_id}"
    raw = r.get(key)
    if not raw:
        raise MfaNotEnrolledError(f"No MFA enrolled for {user_id}")

    data = json.loads(raw)
    if not data.get("confirmed"):
        raise MfaNotEnrolledError(f"MFA enrollment not confirmed for {user_id}")

    secret = data["secret"]
    totp = pyotp.TOTP(secret)
    if totp.verify(code, valid_window=1):  # 30s window ± 1 step
        return True

    raise MfaVerificationFailedError("Invalid TOTP code")


def verify_backup_code(user_id: str, code: str) -> bool:
    """Verify and consume a backup code.

    AC-7: single-use, consumed atomically. Satisfies MFA for this login only.
    Does NOT bypass MFA enrollment requirement.

    Raises MfaVerificationFailedError if code invalid or already used.
    """
    r = _get_redis()
    backup_key = f"{_BACKUP_PREFIX}{user_id}"
    code_hash = _hash_backup_code(code)

    # Atomic: SREM returns 1 if member was present and removed, 0 if not present
    removed = r.srem(backup_key, code_hash)
    if removed:
        log.info("mfa: backup code consumed for user=%s (codes remaining: %d)",
                 user_id, r.scard(backup_key))
        return True

    raise MfaVerificationFailedError("Invalid or already-used backup code")


def is_mfa_enrolled(user_id: str) -> bool:
    """Check if user has confirmed MFA enrollment."""
    r = _get_redis()
    key = f"{_MFA_PREFIX}{user_id}"
    raw = r.get(key)
    if not raw:
        return False
    data = json.loads(raw)
    return data.get("confirmed", False)


def unenroll_mfa(user_id: str) -> bool:
    """Remove MFA enrollment (admin action only)."""
    r = _get_redis()
    r.delete(f"{_MFA_PREFIX}{user_id}")
    r.delete(f"{_BACKUP_PREFIX}{user_id}")
    log.info("mfa: unenrolled for user=%s", user_id)
    return True
