"""Sprint 011 — Auth + security tests.

OmniA: sessions + MFA
OmniB: CSRF + rate limit + tenant RLS + audit coupling
"""
from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# OmniA: Sessions
# ---------------------------------------------------------------------------


def test_session_sign_verify() -> None:
    """Signed session_id verifies correctly; tampered does not."""
    from sos.services.auth.sessions import sign_session_id, verify_signed_session_id

    with patch.dict("os.environ", {"SESSION_COOKIE_SECRET": "test-secret-key-32chars!!!"}):
        signed = sign_session_id("session-abc-123")
        assert verify_signed_session_id(signed) == "session-abc-123"
        assert verify_signed_session_id(signed + "tampered") is None
        assert verify_signed_session_id("totally-fake.signature") is None


def test_session_create_get_invalidate() -> None:
    """Session CRUD lifecycle with mocked Redis."""
    from sos.services.auth.sessions import (
        SessionData,
        create_session,
        get_session,
        invalidate_session,
    )

    store: dict[str, str] = {}
    sets: dict[str, set] = {}

    mock_redis = MagicMock()
    mock_redis.setex = MagicMock(side_effect=lambda k, ttl, v: store.__setitem__(k, v))
    mock_redis.get = MagicMock(side_effect=lambda k: store.get(k))
    mock_redis.delete = MagicMock(side_effect=lambda k: int(store.pop(k, None) is not None))
    mock_redis.exists = MagicMock(side_effect=lambda k: k in store)
    mock_redis.sadd = MagicMock(side_effect=lambda k, v: sets.setdefault(k, set()).add(v))
    mock_redis.srem = MagicMock()
    mock_redis.smembers = MagicMock(return_value=set())
    mock_redis.expire = MagicMock()

    with patch("sos.services.auth.sessions._get_redis", return_value=mock_redis):
        sid = create_session("user-1", "tenant-1", roles=["admin"], mfa_verified=True)
        assert sid  # non-empty

        data = get_session(sid)
        assert data is not None
        assert data.user_id == "user-1"
        assert data.tenant_id == "tenant-1"
        assert data.mfa_verified is True

        assert invalidate_session(sid) is True
        assert get_session(sid) is None


# ---------------------------------------------------------------------------
# OmniA: MFA
# ---------------------------------------------------------------------------


def test_mfa_enroll_verify() -> None:
    """TOTP enrollment + verification round-trip."""
    from sos.services.auth.mfa import enroll_totp, verify_totp, MfaVerificationFailedError

    store: dict[str, str] = {}
    sets: dict[str, set] = {}

    mock_redis = MagicMock()
    mock_redis.set = MagicMock(side_effect=lambda k, v: store.__setitem__(k, v))
    mock_redis.get = MagicMock(side_effect=lambda k: store.get(k))
    mock_redis.exists = MagicMock(side_effect=lambda k: k in store)
    mock_redis.delete = MagicMock(side_effect=lambda k: store.pop(k, None))
    mock_redis.sadd = MagicMock(side_effect=lambda k, v: sets.setdefault(k, set()).add(v))
    mock_redis.srem = MagicMock(side_effect=lambda k, v: int(v in sets.get(k, set())))
    mock_redis.scard = MagicMock(return_value=9)

    with patch("sos.services.auth.mfa._get_redis", return_value=mock_redis):
        result = enroll_totp("user-1")
        assert "secret" in result
        assert "backup_codes" in result
        assert len(result["backup_codes"]) == 10

        # Manually confirm enrollment
        key = f"sos:mfa:user-1"
        data = json.loads(store[key])
        data["confirmed"] = True
        store[key] = json.dumps(data)

        # Generate valid TOTP code
        import pyotp
        totp = pyotp.TOTP(result["secret"])
        valid_code = totp.now()

        assert verify_totp("user-1", valid_code) is True

        with pytest.raises(MfaVerificationFailedError):
            verify_totp("user-1", "000000")


def test_mfa_backup_code_single_use() -> None:
    """Backup code consumed atomically — second use fails."""
    from sos.services.auth.mfa import verify_backup_code, MfaVerificationFailedError

    used: set[str] = set()
    code_hash = hashlib.sha256("ABCD1234".encode()).hexdigest()
    all_codes = {code_hash}

    mock_redis = MagicMock()

    def _srem(key, h):
        if h in all_codes:
            all_codes.discard(h)
            return 1
        return 0

    mock_redis.srem = MagicMock(side_effect=_srem)
    mock_redis.scard = MagicMock(return_value=9)

    with patch("sos.services.auth.mfa._get_redis", return_value=mock_redis):
        assert verify_backup_code("user-1", "ABCD1234") is True

        with pytest.raises(MfaVerificationFailedError):
            verify_backup_code("user-1", "ABCD1234")  # already consumed


# ---------------------------------------------------------------------------
# OmniB: CSRF
# ---------------------------------------------------------------------------


def test_csrf_token_lifecycle() -> None:
    """CSRF token generation + verification."""
    from sos.services.auth.csrf import generate_csrf_token, verify_csrf_token, CsrfError

    with patch.dict("os.environ", {"CSRF_SECRET": "csrf-test-secret-32!"}):
        token = generate_csrf_token("session-123")
        assert "." in token

        assert verify_csrf_token("session-123", token) is True

        with pytest.raises(CsrfError):
            verify_csrf_token("different-session", token)


def test_csrf_origin_check() -> None:
    """Origin allowlist check."""
    from sos.services.auth.csrf import check_origin, CsrfError

    assert check_origin("https://mumega.com") is True

    with pytest.raises(CsrfError):
        check_origin("https://evil.com")


# ---------------------------------------------------------------------------
# OmniB: Rate limiting
# ---------------------------------------------------------------------------


def test_rate_limit_allows_then_blocks() -> None:
    """Rate limiter allows requests up to limit, then blocks."""
    from sos.services.auth.rate_limit import check_rate_limit, RateLimitExceededError

    call_count = [0]

    mock_redis = MagicMock()
    pipe = MagicMock()

    def _pipe_execute():
        call_count[0] += 1
        return [None, None, call_count[0], None]  # zremrangebyscore, zadd, zcard, expire

    pipe.zremrangebyscore = MagicMock()
    pipe.zadd = MagicMock()
    pipe.zcard = MagicMock()
    pipe.expire = MagicMock()
    pipe.execute = MagicMock(side_effect=_pipe_execute)
    mock_redis.pipeline = MagicMock(return_value=pipe)

    with patch("sos.services.auth.rate_limit._get_redis", return_value=mock_redis):
        # First 3 should pass
        for _ in range(3):
            result = check_rate_limit("user-1", "tenant-1", "/api/test", max_requests=3)
            assert result["allowed"] is True

        # 4th should fail
        with pytest.raises(RateLimitExceededError):
            check_rate_limit("user-1", "tenant-1", "/api/test", max_requests=3)


def test_get_client_ip_cf() -> None:
    """CF-Connecting-IP preferred over X-Forwarded-For."""
    from sos.services.auth.rate_limit import get_client_ip

    assert get_client_ip({"cf-connecting-ip": "1.2.3.4", "x-forwarded-for": "5.6.7.8"}) == "1.2.3.4"
    assert get_client_ip({"x-real-ip": "9.10.11.12"}) == "9.10.11.12"


# ---------------------------------------------------------------------------
# OmniB: Tenant scope
# ---------------------------------------------------------------------------


def test_tenant_scope_violation() -> None:
    """Tenant scope mismatch raises."""
    from sos.services.auth.tenant_rls import verify_tenant_scope, TenantScopeViolation

    verify_tenant_scope("tenant-a", "tenant-a")  # no raise

    with pytest.raises(TenantScopeViolation):
        verify_tenant_scope("tenant-a", "tenant-b")

    with pytest.raises(TenantScopeViolation):
        verify_tenant_scope("", "tenant-b")


# ---------------------------------------------------------------------------
# OmniB: Audit coupling (LOCK-6)
# ---------------------------------------------------------------------------


def test_audit_mutation_rollback_on_failure() -> None:
    """Audit write failure raises → mutation should rollback."""
    from sos.services.auth.tenant_rls import audit_mutation

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.execute.side_effect = RuntimeError("audit_events table locked")
    mock_cursor.fetchone.return_value = (1,)
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    with pytest.raises(RuntimeError, match="Audit write failed"):
        audit_mutation(mock_conn, "user-1", "update_deal", "deal:123")
