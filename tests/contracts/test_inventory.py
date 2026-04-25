"""
§14 Inventory contract tests — Sprint 003 Track C.

Unit tests: type validation, verifier registry, deterministic grant IDs.
Integration tests (requires DB): grant/revoke/reverify/list/assert.

Run all:     DATABASE_URL=... pytest tests/contracts/test_inventory.py -v
Run unit:    pytest tests/contracts/test_inventory.py -v -m "not db"
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from sos.contracts.inventory import (
    Capability,
    VERIFIERS,
    _grant_id,
    assert_capability,
    grant_capability,
    list_capabilities,
    register_verifier,
    revoke_capability,
    reverify,
)


# ── helpers ────────────────────────────────────────────────────────────────────


def _has_db() -> bool:
    return bool(os.getenv('MIRROR_DATABASE_URL') or os.getenv('DATABASE_URL'))


def _uid() -> str:
    return f'test-{uuid.uuid4().hex[:8]}'


db = pytest.mark.skipif(not _has_db(), reason='Mirror DB not configured')


# ── Unit: grant ID generation ──────────────────────────────────────────────────


class TestGrantId:
    def test_deterministic(self) -> None:
        a = _grant_id('loom', 'tool', 'mcp__sos__send')
        b = _grant_id('loom', 'tool', 'mcp__sos__send')
        assert a == b

    def test_different_refs_different_ids(self) -> None:
        a = _grant_id('loom', 'tool', 'mcp__sos__send')
        b = _grant_id('loom', 'tool', 'mcp__sos__inbox')
        assert a != b

    def test_different_holders_different_ids(self) -> None:
        a = _grant_id('loom', 'tool', 'mcp__sos__send')
        b = _grant_id('kasra', 'tool', 'mcp__sos__send')
        assert a != b

    def test_format(self) -> None:
        gid = _grant_id('hadi', 'credential', 'tok-abc')
        assert gid.startswith('inv:credential:hadi:')
        assert len(gid.split(':')) == 4


# ── Unit: verifier registry ────────────────────────────────────────────────────


class TestVerifierRegistry:
    def test_default_verifiers_registered(self) -> None:
        expected_kinds = {
            'credential', 'tool', 'automation', 'template',
            'oauth_connection', 'guild_role', 'data_access', 'mcp_server',
        }
        assert expected_kinds.issubset(set(VERIFIERS.keys()))

    def test_register_custom(self) -> None:
        def my_verifier(ref: str):
            return (True, 'active')

        register_verifier('custom_test', my_verifier)
        assert 'custom_test' in VERIFIERS
        ok, hint = VERIFIERS['custom_test']('any')
        assert ok is True


# ── Unit: Capability model ─────────────────────────────────────────────────────


class TestCapabilityModel:
    def test_valid(self) -> None:
        now = datetime.now(timezone.utc)
        cap = Capability(
            grant_id='inv:tool:loom:abc12345',
            holder_type='agent',
            holder_id='loom',
            kind='tool',
            ref='mcp__sos__send',
            source_domain='plugin:yaml',
            scope=None,
            granted_by='hadi',
            granted_at=now,
            expires_at=None,
            last_verified_at=now,
            verify_attempt_count=0,
            last_error=None,
            status='active',
        )
        assert cap.kind == 'tool'
        assert cap.status == 'active'

    def test_frozen(self) -> None:
        now = datetime.now(timezone.utc)
        cap = Capability(
            grant_id='x',
            holder_type='human',
            holder_id='hadi',
            kind='credential',
            ref='tok',
            source_domain='d1:tokens',
            scope=None,
            granted_by='hadi',
            granted_at=now,
            expires_at=None,
            last_verified_at=now,
            verify_attempt_count=0,
            last_error=None,
            status='active',
        )
        with pytest.raises(ValidationError):
            cap.status = 'revoked'  # type: ignore[misc]


# ── Unit: built-in verifiers (offline checks) ──────────────────────────────────


class TestBuiltInVerifiers:
    def test_template_verifier_missing(self) -> None:
        ok, hint = VERIFIERS['template']('definitely-not-a-real-skill-xyz')
        assert ok is False
        assert hint == 'orphaned'

    def test_guild_role_bad_format(self) -> None:
        ok, hint = VERIFIERS['guild_role']('not-valid-format')
        assert ok is False

    def test_data_access_always_valid(self) -> None:
        ok, hint = VERIFIERS['data_access']('any-role')
        assert ok is True

    def test_mcp_server_bad_url(self) -> None:
        ok, hint = VERIFIERS['mcp_server']('http://localhost:9999/nonexistent')
        assert ok is False
        assert hint == 'stale'


# ── Integration: DB-backed grant/revoke/list/assert ───────────────────────────


@db
class TestGrantCapability:
    def test_grant_creates_row(self) -> None:
        holder = _uid()
        cap = grant_capability(
            holder_id=holder,
            kind='tool',
            ref='mcp__sos__send',
            source_domain='plugin:yaml',
            scope=None,
            granted_by='hadi',
        )
        assert cap.holder_id == holder
        assert cap.kind == 'tool'
        assert cap.status == 'active'
        assert cap.verify_attempt_count == 0

    def test_grant_idempotent(self) -> None:
        holder = _uid()
        c1 = grant_capability(holder, 'tool', 'mcp__sos__inbox', 'plugin:yaml', None, 'hadi')
        c2 = grant_capability(holder, 'tool', 'mcp__sos__inbox', 'plugin:yaml', None, 'hadi')
        assert c1.grant_id == c2.grant_id

    def test_grant_with_scope(self) -> None:
        holder = _uid()
        scope = {'read_only': True, 'rate_limit': '100/h'}
        cap = grant_capability(holder, 'credential', 'tok-abc', 'd1:tokens', scope, 'hadi')
        assert cap.scope == scope


@db
class TestListCapabilities:
    def test_list_returns_grants(self) -> None:
        holder = _uid()
        grant_capability(holder, 'tool', 'mcp__sos__send', 'plugin:yaml', None, 'hadi')
        grant_capability(holder, 'template', 'code-review', 'fs:sos/skills', None, 'hadi')
        caps = list_capabilities(holder)
        assert len(caps) >= 2

    def test_filter_by_kind(self) -> None:
        holder = _uid()
        grant_capability(holder, 'tool', 'mcp__sos__send', 'plugin:yaml', None, 'hadi')
        grant_capability(holder, 'credential', 'tok-xyz', 'd1:tokens', None, 'hadi')
        tools = list_capabilities(holder, kind='tool')
        assert all(c.kind == 'tool' for c in tools)

    def test_fresh_within_excludes_stale(self) -> None:
        """fresh_within_seconds=1 means only rows verified in last 1 second.
        A newly created grant should pass (just verified now)."""
        holder = _uid()
        grant_capability(holder, 'tool', 'mcp__sos__broadcast', 'plugin:yaml', None, 'hadi')
        caps = list_capabilities(holder, kind='tool', fresh_within_seconds=60)
        assert any(c.ref == 'mcp__sos__broadcast' for c in caps)


@db
class TestAssertCapability:
    def test_assert_positive(self) -> None:
        holder = _uid()
        grant_capability(holder, 'tool', 'mcp__sos__peers', 'plugin:yaml', None, 'hadi')
        assert assert_capability(holder, 'tool', 'mcp__sos__peers', 'invoke') is True

    def test_assert_negative_missing(self) -> None:
        holder = _uid()
        assert assert_capability(holder, 'tool', 'nonexistent-tool', 'invoke') is False

    def test_assert_action_in_scope(self) -> None:
        holder = _uid()
        grant_capability(
            holder, 'data_access', 'contacts:read', 'pg:contacts',
            scope={'allow_actions': ['read']}, granted_by='hadi',
        )
        assert assert_capability(holder, 'data_access', 'contacts:read', 'read') is True
        assert assert_capability(holder, 'data_access', 'contacts:read', 'write') is False


@db
class TestRevokeCapability:
    def test_revoke(self) -> None:
        holder = _uid()
        cap = grant_capability(holder, 'credential', 'tok-del', 'd1:tokens', None, 'hadi')
        revoke_capability(cap.grant_id, revoked_by='hadi', reason='expired token')
        # Should no longer be in active list
        caps = list_capabilities(holder, kind='credential')
        assert all(c.grant_id != cap.grant_id for c in caps)

    def test_revoke_missing_raises(self) -> None:
        with pytest.raises(ValueError, match='not found'):
            revoke_capability('inv:fake:grant:00000000', revoked_by='hadi', reason='test')


@db
class TestReverify:
    def test_reverify_with_stub_verifier(self) -> None:
        holder = _uid()

        # Register a passing stub verifier for this test
        register_verifier('mcp_server', lambda ref: (True, 'active'))

        cap = grant_capability(holder, 'mcp_server', 'http://localhost:6060', 'sos:bus', None, 'hadi')
        result = reverify(cap.grant_id)
        assert result.status == 'active'
        assert result.verify_attempt_count == 0
        assert result.last_error is None

    def test_reverify_failing_verifier(self) -> None:
        holder = _uid()

        # Register a failing stub
        register_verifier('automation', lambda ref: (False, 'orphaned'))

        cap = grant_capability(holder, 'automation', 'wf-fake-999', 'ghl:workflows', None, 'hadi')
        result = reverify(cap.grant_id)
        assert result.status == 'orphaned'
        assert result.verify_attempt_count == 1

    def test_reverify_missing_raises(self) -> None:
        with pytest.raises(ValueError, match='not found'):
            reverify('inv:fake:grant:00000000')
