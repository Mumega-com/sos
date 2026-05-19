"""
§6.11 PIPEDA erasure tests — deactivate_principal() contract.

Gate: Athena G11

These tests verify the Python-side contract only. The DB function
(SECURITY DEFINER deactivate_principal) is exercised via integration tests
that require MIRROR_DATABASE_URL. Unit tests stub the DB.

Integration tests (marked @pytest.mark.integration) hit the real DB and
assert the actual schema state after erasure.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from sos.contracts.principals import (
    Principal,
    PrincipalStatus,
    deactivate_principal,
    get_principal,
    upsert_principal,
)


# ── Model tests ────────────────────────────────────────────────────────────────


class TestPrincipalStatusEnum:
    def test_deactivated_is_valid_status(self) -> None:
        p = Principal(
            id='pid-test',
            tenant_id='default',
            email=None,
            display_name=None,
            principal_type='human',
            status='deactivated',
            mfa_required=False,
            last_login_at=None,
            created_at=datetime.now(timezone.utc),
            deactivated_at=datetime.now(timezone.utc),
        )
        assert p.status == 'deactivated'

    def test_deactivated_at_defaults_none(self) -> None:
        p = Principal(
            id='pid-test',
            tenant_id='default',
            email=None,
            display_name=None,
            principal_type='agent',
            status='active',
            mfa_required=False,
            last_login_at=None,
            created_at=datetime.now(timezone.utc),
        )
        assert p.deactivated_at is None

    def test_deactivated_at_preserved_when_set(self) -> None:
        ts = datetime(2026, 4, 25, 12, 0, 0, tzinfo=timezone.utc)
        p = Principal(
            id='pid-test',
            tenant_id='default',
            email=None,
            display_name=None,
            principal_type='human',
            status='deactivated',
            mfa_required=False,
            last_login_at=None,
            created_at=datetime.now(timezone.utc),
            deactivated_at=ts,
        )
        assert p.deactivated_at == ts

    def test_invalid_status_raises(self) -> None:
        with pytest.raises(Exception):
            Principal(
                id='pid-test',
                tenant_id='default',
                email=None,
                display_name=None,
                principal_type='human',
                status='erased',  # type: ignore[arg-type]
                mfa_required=False,
                last_login_at=None,
                created_at=datetime.now(timezone.utc),
            )


# ── Unit tests — DB stubbed ────────────────────────────────────────────────────


def _make_mock_conn(cursor_mock: MagicMock) -> MagicMock:
    """Return a context-manager-compatible connection mock wrapping cursor_mock."""
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor_mock)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return conn


class TestDeactivatePrincipalUnit:
    """Unit tests: verify the Python contract calls the DB function correctly."""

    def test_calls_db_function_with_correct_args(self) -> None:
        cur = MagicMock()
        conn = _make_mock_conn(cur)

        with patch('sos.contracts.principals._connect', return_value=conn):
            deactivate_principal('pid-abc123', requested_by='hadi@digid.ca')

        cur.execute.assert_called_once_with(
            'SELECT deactivate_principal(%s, %s)',
            ('pid-abc123', 'hadi@digid.ca'),
        )
        conn.commit.assert_called_once()

    def test_commits_after_db_call(self) -> None:
        executed: list[bool] = []
        committed: list[bool] = []

        cur = MagicMock()
        orig_execute = cur.execute

        def tracking_execute(*args: object, **kwargs: object) -> None:
            executed.append(True)
            return orig_execute(*args, **kwargs)

        cur.execute = tracking_execute
        conn = _make_mock_conn(cur)

        original_commit = conn.commit

        def tracking_commit() -> None:
            committed.append(True)
            assert executed, 'commit called before execute'
            return original_commit()

        conn.commit = tracking_commit

        with patch('sos.contracts.principals._connect', return_value=conn):
            deactivate_principal('pid-xyz', requested_by='system')

        assert executed, 'execute was never called'
        assert committed, 'commit was never called'

    def test_db_exception_propagates(self) -> None:
        cur = MagicMock()
        cur.execute.side_effect = Exception('principal not found')
        conn = _make_mock_conn(cur)

        with patch('sos.contracts.principals._connect', return_value=conn):
            with pytest.raises(Exception, match='principal not found'):
                deactivate_principal('pid-missing', requested_by='admin')

        # commit must NOT have been called
        conn.commit.assert_not_called()


# ── Integration tests — require real DB ───────────────────────────────────────

_skip_no_db = pytest.mark.skipif(
    not os.getenv('MIRROR_DATABASE_URL') and not os.getenv('DATABASE_URL'),
    reason='Requires MIRROR_DATABASE_URL or DATABASE_URL',
)


@pytest.mark.integration
@_skip_no_db
class TestDeactivatePrincipalIntegration:
    """
    Integration tests that hit the real DB.
    Each test creates a fresh principal so tests are independent.
    """

    def _create_principal(self, suffix: str) -> Principal:
        return upsert_principal(
            email=f'erasure-test-{suffix}@example.com',
            display_name=f'Erasure Test {suffix}',
            principal_type='human',
            tenant_id='test',
        )

    def test_status_set_to_deactivated(self) -> None:
        p = self._create_principal('status-01')
        deactivate_principal(p.id, requested_by='test-runner')

        result = get_principal(p.id)
        assert result is not None
        assert result.status == 'deactivated'

    def test_deactivated_at_is_set(self) -> None:
        p = self._create_principal('ts-01')
        before = datetime.now(timezone.utc)
        deactivate_principal(p.id, requested_by='test-runner')
        after = datetime.now(timezone.utc)

        result = get_principal(p.id)
        assert result is not None
        assert result.deactivated_at is not None
        assert before <= result.deactivated_at <= after

    def test_email_nulled(self) -> None:
        p = self._create_principal('email-01')
        assert p.email is not None  # sanity

        deactivate_principal(p.id, requested_by='test-runner')

        result = get_principal(p.id)
        assert result is not None
        assert result.email is None

    def test_display_name_nulled(self) -> None:
        p = self._create_principal('name-01')
        assert p.display_name is not None  # sanity

        deactivate_principal(p.id, requested_by='test-runner')

        result = get_principal(p.id)
        assert result is not None
        assert result.display_name is None

    def test_principal_id_preserved(self) -> None:
        """Profile ID must survive — it's the reactivation token carrier."""
        p = self._create_principal('id-01')
        original_id = p.id

        deactivate_principal(p.id, requested_by='test-runner')

        result = get_principal(original_id)
        assert result is not None
        assert result.id == original_id

    def test_tenant_and_type_preserved(self) -> None:
        p = self._create_principal('tenant-01')
        deactivate_principal(p.id, requested_by='test-runner')

        result = get_principal(p.id)
        assert result is not None
        assert result.tenant_id == 'test'
        assert result.principal_type == 'human'

    def test_idempotent_on_already_deactivated(self) -> None:
        """DB function raises on unknown principal — but a second call on the same
        (now-deactivated) principal should succeed because the row still exists."""
        p = self._create_principal('idem-01')
        deactivate_principal(p.id, requested_by='test-runner')
        # Second call — anonymize_profile() will find no email/display_name to null,
        # and the status UPDATE + DELETE + DISABLE are all idempotent.
        deactivate_principal(p.id, requested_by='test-runner')

        result = get_principal(p.id)
        assert result is not None
        assert result.status == 'deactivated'

    def test_missing_principal_raises(self) -> None:
        with pytest.raises(Exception):
            deactivate_principal('pid-does-not-exist-xyz', requested_by='test-runner')
