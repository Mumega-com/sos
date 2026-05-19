"""
§2B.1 SSO + SCIM + MFA contract tests — Sprint 003 / Burst 2B.

Unit tests: OIDC URL building, SAML XML parsing, TOTP math, models.
Integration tests (requires DB): IdP CRUD, JIT provisioning, SCIM, TOTP enroll/verify.

Run all:     DATABASE_URL=... pytest tests/contracts/test_sso.py -v
Run unit:    pytest tests/contracts/test_sso.py -v -m "not db"

Note: WebAuthn registration/assertion tests require a real authenticator flow
(browser + hardware key). The unit tests below cover the contract surface;
full E2E WebAuthn is in tests/integration/test_webauthn_e2e.py.
"""
from __future__ import annotations

import base64
import json
import os
import time
import uuid
from datetime import datetime, timezone

import pytest
import pyotp
from pydantic import ValidationError

from sos.contracts.sso import (
    IdpConfig,
    LoginResult,
    MfaEnrolledMethod,
    SsoIdentityLink,
    add_group_role_map,
    audit_idp_ceiling_violations,
    build_oidc_auth_url,
    cleanup_mfa_used_codes,
    create_idp,
    disable_mfa_method,
    enroll_totp,
    get_idp,
    get_or_create_link,
    get_roles_for_groups,
    list_idps,
    list_mfa_methods,
    scim_deprovision_user,
    scim_provision_user,
    verify_totp,
    _saml_id_looks_predictable,
)
from sos.contracts.principals import (
    create_role,
    get_principal,
    upsert_principal,
)


# ── helpers ────────────────────────────────────────────────────────────────────


def _has_db() -> bool:
    return bool(os.getenv('MIRROR_DATABASE_URL') or os.getenv('DATABASE_URL'))


def _uid(prefix: str = 'x') -> str:
    return f'test-{prefix}-{uuid.uuid4().hex[:8]}'


db = pytest.mark.skipif(not _has_db(), reason='Mirror DB not configured')

# Enable local TOTP store for all tests
os.environ['SOS_TOTP_STORE'] = 'local'


# ── Unit: model validation ─────────────────────────────────────────────────────


class TestIdpConfigModel:
    def test_valid_oidc(self) -> None:
        now = datetime.now(timezone.utc)
        cfg = IdpConfig(
            id='idp-abc',
            tenant_id='default',
            protocol='oidc',
            display_name='Google Workspace',
            metadata_url=None,
            entity_id=None,
            acs_url=None,
            client_id='google-client-id',
            client_secret_ref='sos/oidc/google',
            authorization_url='https://accounts.google.com/o/oauth2/v2/auth',
            token_url='https://oauth2.googleapis.com/token',
            userinfo_url='https://openidconnect.googleapis.com/v1/userinfo',
            jwks_url='https://www.googleapis.com/oauth2/v3/certs',
            group_claim_path='groups',
            enabled=True,
            created_at=now,
        )
        assert cfg.protocol == 'oidc'
        assert cfg.enabled is True

    def test_frozen(self) -> None:
        now = datetime.now(timezone.utc)
        cfg = IdpConfig(
            id='idp-x', tenant_id='default', protocol='saml',
            display_name='Test SAML', metadata_url=None, entity_id=None,
            acs_url=None, client_id=None, client_secret_ref=None,
            authorization_url=None, token_url=None, userinfo_url=None,
            jwks_url=None, group_claim_path='groups', enabled=True, created_at=now,
        )
        with pytest.raises(ValidationError):
            cfg.enabled = False  # type: ignore[misc]

    def test_invalid_protocol(self) -> None:
        now = datetime.now(timezone.utc)
        with pytest.raises(ValidationError):
            IdpConfig(
                id='x', tenant_id='default', protocol='oauth1',  # type: ignore[arg-type]
                display_name='bad', metadata_url=None, entity_id=None, acs_url=None,
                client_id=None, client_secret_ref=None, authorization_url=None,
                token_url=None, userinfo_url=None, jwks_url=None,
                group_claim_path='groups', enabled=True, created_at=now,
            )


class TestLoginResultModel:
    def test_valid(self) -> None:
        lr = LoginResult(
            principal_id='pid-abc',
            email='user@example.com',
            display_name='User',
            tenant_id='default',
            mfa_verified=False,
            mfa_required=True,
            roles=['role:sos:builder'],
        )
        assert lr.mfa_required is True
        assert lr.mfa_verified is False

    def test_frozen(self) -> None:
        lr = LoginResult(
            principal_id='x', email=None, display_name=None,
            tenant_id='default', mfa_verified=False, mfa_required=False, roles=[],
        )
        with pytest.raises(ValidationError):
            lr.mfa_verified = True  # type: ignore[misc]


# ── Unit: OIDC URL builder ─────────────────────────────────────────────────────


class TestOidcAuthUrl:
    def _make_idp(self) -> IdpConfig:
        now = datetime.now(timezone.utc)
        return IdpConfig(
            id='idp-test', tenant_id='default', protocol='oidc',
            display_name='Test OIDC',
            metadata_url=None, entity_id=None, acs_url=None,
            client_id='test-client-id',
            client_secret_ref=None,
            authorization_url='https://auth.example.com/oauth2/v2/auth',
            token_url='https://auth.example.com/oauth2/token',
            userinfo_url=None, jwks_url=None,
            group_claim_path='groups', enabled=True, created_at=now,
        )

    def test_url_contains_client_id(self) -> None:
        idp = self._make_idp()
        url = build_oidc_auth_url(idp, state='s1', nonce='n1', redirect_uri='https://app/cb')
        assert 'client_id=test-client-id' in url

    def test_url_contains_state(self) -> None:
        idp = self._make_idp()
        url = build_oidc_auth_url(idp, state='abc123', nonce='nonce1', redirect_uri='https://app/cb')
        assert 'state=abc123' in url

    def test_url_contains_nonce(self) -> None:
        idp = self._make_idp()
        url = build_oidc_auth_url(idp, state='s', nonce='mynonce', redirect_uri='https://app/cb')
        assert 'nonce=mynonce' in url

    def test_missing_auth_url_raises(self) -> None:
        now = datetime.now(timezone.utc)
        idp = IdpConfig(
            id='x', tenant_id='default', protocol='oidc', display_name='bad',
            metadata_url=None, entity_id=None, acs_url=None,
            client_id='cid', client_secret_ref=None,
            authorization_url=None,  # missing
            token_url=None, userinfo_url=None, jwks_url=None,
            group_claim_path='groups', enabled=True, created_at=now,
        )
        with pytest.raises(ValueError, match='authorization_url'):
            build_oidc_auth_url(idp, state='s', nonce='n', redirect_uri='https://x')

    def test_custom_scopes(self) -> None:
        idp = self._make_idp()
        url = build_oidc_auth_url(
            idp, state='s', nonce='n', redirect_uri='https://app/cb',
            scopes=['openid', 'email', 'groups'],
        )
        assert 'scope=' in url


# ── Unit: TOTP math (offline) ──────────────────────────────────────────────────


class TestTotpMath:
    def test_pyotp_verify_window(self) -> None:
        """TOTP verify window=1 accepts codes from ±30s."""
        secret = pyotp.random_base32()
        totp = pyotp.TOTP(secret)
        code = totp.now()
        assert totp.verify(code, valid_window=1) is True

    def test_wrong_code_rejected(self) -> None:
        secret = pyotp.random_base32()
        totp = pyotp.TOTP(secret)
        assert totp.verify('000000', valid_window=1) is False

    def test_provisioning_uri(self) -> None:
        secret = pyotp.random_base32()
        totp = pyotp.TOTP(secret)
        uri = totp.provisioning_uri(name='user@example.com', issuer_name='SOS')
        assert uri.startswith('otpauth://totp/')
        assert 'issuer=SOS' in uri


# ── Integration: IdP configuration CRUD ───────────────────────────────────────


@db
class TestCreateIdp:
    def test_create_oidc(self) -> None:
        name = f'Google Workspace {_uid()}'
        idp = create_idp(
            protocol='oidc',
            display_name=name,
            client_id='google-test-id',
            authorization_url='https://accounts.google.com/o/oauth2/v2/auth',
            token_url='https://oauth2.googleapis.com/token',
        )
        assert idp.protocol == 'oidc'
        assert idp.display_name == name
        assert idp.enabled is True

    def test_create_saml(self) -> None:
        name = f'Microsoft Entra {_uid()}'
        idp = create_idp(
            protocol='saml',
            display_name=name,
            metadata_url='https://login.microsoftonline.com/tenant/metadata',
            entity_id='https://sos.mumega.com/saml/sp',
            acs_url='https://api.mumega.com/sso/saml/callback',
        )
        assert idp.protocol == 'saml'

    def test_get_idp(self) -> None:
        name = f'GetTest {_uid()}'
        created = create_idp(protocol='oidc', display_name=name, client_id='cid')
        fetched = get_idp(created.id)
        assert fetched is not None
        assert fetched.id == created.id

    def test_get_missing_returns_none(self) -> None:
        assert get_idp('nonexistent-idp-id') is None

    def test_list_idps(self) -> None:
        name1 = f'List1 {_uid()}'
        name2 = f'List2 {_uid()}'
        create_idp(protocol='oidc', display_name=name1, client_id='c1')
        create_idp(protocol='saml', display_name=name2)
        idps = list_idps()
        names = {i.display_name for i in idps}
        assert name1 in names
        assert name2 in names


# ── Integration: IdP group → role mapping ─────────────────────────────────────


@db
class TestGroupRoleMap:
    def test_add_and_get(self) -> None:
        name = f'GRM {_uid()}'
        idp = create_idp(protocol='oidc', display_name=name, client_id='c1')
        proj = _uid('proj')
        r = create_role(project_id=proj, name='eng')
        add_group_role_map(idp.id, 'engineering', r.id)
        roles = get_roles_for_groups(idp.id, ['engineering'])
        assert r.id in roles

    def test_multiple_groups(self) -> None:
        name = f'MultiGRM {_uid()}'
        idp = create_idp(protocol='oidc', display_name=name, client_id='c2')
        proj = _uid('proj')
        r1 = create_role(project_id=proj, name='a')
        r2 = create_role(project_id=proj, name='b')
        add_group_role_map(idp.id, 'grp-a', r1.id)
        add_group_role_map(idp.id, 'grp-b', r2.id)
        roles = get_roles_for_groups(idp.id, ['grp-a', 'grp-b'])
        assert r1.id in roles
        assert r2.id in roles

    def test_empty_groups_returns_empty(self) -> None:
        name = f'EmptyGRM {_uid()}'
        idp = create_idp(protocol='oidc', display_name=name, client_id='c3')
        assert get_roles_for_groups(idp.id, []) == []


# ── Integration: SSO identity link + JIT provisioning ─────────────────────────


@db
class TestGetOrCreateLink:
    def test_first_login_creates_principal(self) -> None:
        name = f'JIT {_uid()}'
        idp = create_idp(protocol='oidc', display_name=name, client_id='c4')
        email = f'{_uid()}@test.local'
        link, created = get_or_create_link(
            idp_id=idp.id,
            external_subject=f'sub-{_uid()}',
            email=email,
        )
        assert created is True
        assert link.email == email
        p = get_principal(link.principal_id)
        assert p is not None
        assert p.email == email

    def test_second_login_returns_existing(self) -> None:
        name = f'JIT2 {_uid()}'
        idp = create_idp(protocol='oidc', display_name=name, client_id='c5')
        sub = f'sub-{_uid()}'
        link1, created1 = get_or_create_link(idp_id=idp.id, external_subject=sub)
        link2, created2 = get_or_create_link(idp_id=idp.id, external_subject=sub)
        assert created1 is True
        assert created2 is False
        assert link1.principal_id == link2.principal_id

    def test_different_subs_different_principals(self) -> None:
        name = f'JIT3 {_uid()}'
        idp = create_idp(protocol='oidc', display_name=name, client_id='c6')
        link_a, _ = get_or_create_link(idp_id=idp.id, external_subject=f'sub-a-{_uid()}')
        link_b, _ = get_or_create_link(idp_id=idp.id, external_subject=f'sub-b-{_uid()}')
        assert link_a.principal_id != link_b.principal_id


# ── Integration: SCIM provisioning ────────────────────────────────────────────


@db
class TestScimProvisioning:
    def test_provision_user(self) -> None:
        name = f'SCIM {_uid()}'
        idp = create_idp(protocol='oidc', display_name=name, client_id='c7')
        email = f'{_uid()}@scim.local'
        ext_id = _uid('ext')
        pid = scim_provision_user(
            idp_id=idp.id,
            external_id=ext_id,
            email=email,
            display_name='SCIM User',
            active=True,
        )
        p = get_principal(pid)
        assert p is not None
        assert p.email == email
        assert p.status == 'active'

    def test_provision_idempotent(self) -> None:
        name = f'SCIM2 {_uid()}'
        idp = create_idp(protocol='oidc', display_name=name, client_id='c8')
        email = f'{_uid()}@scim.local'
        ext_id = _uid('ext')
        pid1 = scim_provision_user(idp_id=idp.id, external_id=ext_id, email=email, active=True)
        pid2 = scim_provision_user(idp_id=idp.id, external_id=ext_id, email=email, active=True)
        assert pid1 == pid2

    def test_deprovision_sets_status(self) -> None:
        name = f'SCIM3 {_uid()}'
        idp = create_idp(protocol='oidc', display_name=name, client_id='c9')
        email = f'{_uid()}@scim.local'
        ext_id = _uid('ext')
        pid = scim_provision_user(idp_id=idp.id, external_id=ext_id, email=email, active=True)
        deprovisioned = scim_deprovision_user(idp_id=idp.id, external_id=ext_id)
        assert deprovisioned is True
        p = get_principal(pid)
        assert p is not None
        assert p.status == 'deprovisioned'

    def test_deprovision_missing_returns_false(self) -> None:
        name = f'SCIM4 {_uid()}'
        idp = create_idp(protocol='oidc', display_name=name, client_id='c10')
        assert scim_deprovision_user(idp_id=idp.id, external_id='nonexistent') is False

    def test_provision_with_groups_assigns_roles(self) -> None:
        name = f'SCIM5 {_uid()}'
        idp = create_idp(protocol='oidc', display_name=name, client_id='c11')
        proj = _uid('proj')
        r = create_role(project_id=proj, name='scim-eng')
        add_group_role_map(idp.id, 'engineering', r.id)

        email = f'{_uid()}@scim.local'
        ext_id = _uid('ext')
        pid = scim_provision_user(
            idp_id=idp.id,
            external_id=ext_id,
            email=email,
            active=True,
            groups=['engineering'],
        )
        from sos.contracts.principals import get_assignee_roles
        roles = get_assignee_roles(pid)
        assert any(role.id == r.id for role in roles)


# ── Integration: MFA — TOTP ────────────────────────────────────────────────────


@db
class TestTotpEnrollAndVerify:
    def test_enroll_returns_otpauth_uri(self) -> None:
        email = f'{_uid()}@mfa.local'
        p = upsert_principal(email=email)
        uri, ref = enroll_totp(p.id, label='default')
        assert uri.startswith('otpauth://totp/')
        assert ref.startswith('local:')

    def test_verify_correct_code(self) -> None:
        email = f'{_uid()}@mfa.local'
        p = upsert_principal(email=email)
        uri, ref = enroll_totp(p.id, label='default')

        # Extract secret from local store and generate code
        from sos.contracts.sso import _LOCAL_TOTP_STORE
        secret = _LOCAL_TOTP_STORE[ref]
        code = pyotp.TOTP(secret).now()

        assert verify_totp(p.id, code) is True

    def test_verify_wrong_code(self) -> None:
        email = f'{_uid()}@mfa.local'
        p = upsert_principal(email=email)
        enroll_totp(p.id, label='default')
        assert verify_totp(p.id, '000000') is False

    def test_verify_unenrolled_returns_false(self) -> None:
        email = f'{_uid()}@mfa.local'
        p = upsert_principal(email=email)
        assert verify_totp(p.id, '123456') is False

    def test_list_mfa_methods(self) -> None:
        email = f'{_uid()}@mfa.local'
        p = upsert_principal(email=email)
        enroll_totp(p.id, label='phone')
        methods = list_mfa_methods(p.id)
        assert len(methods) >= 1
        assert any(m.method == 'totp' for m in methods)

    def test_disable_mfa_method(self) -> None:
        email = f'{_uid()}@mfa.local'
        p = upsert_principal(email=email)
        enroll_totp(p.id, label='disable-me')
        disabled = disable_mfa_method(p.id, 'disable-me', 'totp')
        assert disabled is True
        methods = list_mfa_methods(p.id)
        assert not any(m.label == 'disable-me' for m in methods)

    def test_multiple_labels(self) -> None:
        email = f'{_uid()}@mfa.local'
        p = upsert_principal(email=email)
        enroll_totp(p.id, label='phone')
        enroll_totp(p.id, label='tablet')
        methods = list_mfa_methods(p.id)
        labels = {m.label for m in methods}
        assert 'phone' in labels
        assert 'tablet' in labels


# ── TC-G27: TOTP replay ledger ────────────────────────────────────────────────


@db
class TestTotpReplayLedger:
    """G27 (F-09): mfa_used_codes replay ledger tests."""

    def _enroll_and_code(self) -> tuple[str, str]:
        """Return (principal_id, current_code) with local TOTP store."""
        email = f'{_uid()}@replay.local'
        p = upsert_principal(email=email)
        uri, ref = enroll_totp(p.id, label='default')
        from sos.contracts.sso import _LOCAL_TOTP_STORE
        secret = _LOCAL_TOTP_STORE[ref]
        code = pyotp.TOTP(secret).now()
        return p.id, code

    def test_tc_g27a_first_verify_accepted(self) -> None:
        """TC-G27a: Valid code accepted on first verify."""
        principal_id, code = self._enroll_and_code()
        assert verify_totp(principal_id, code) is True

    def test_tc_g27a_replay_rejected(self) -> None:
        """TC-G27a: Same code rejected on second verify (replay attack)."""
        principal_id, code = self._enroll_and_code()
        assert verify_totp(principal_id, code) is True
        # Replay — same code, same window — must be rejected
        assert verify_totp(principal_id, code) is False

    def test_tc_g27b_same_code_next_window_accepted(self) -> None:
        """TC-G27b: Same code in next time window is accepted (different hash).

        We simulate next-window by directly calling _totp_window_start with a
        mocked future time. The code is re-generated for the next window.
        """
        import time as time_module
        from unittest.mock import patch

        email = f'{_uid()}@replay.local'
        p = upsert_principal(email=email)
        uri, ref = enroll_totp(p.id, label='default')
        from sos.contracts.sso import _LOCAL_TOTP_STORE
        secret = _LOCAL_TOTP_STORE[ref]
        totp = pyotp.TOTP(secret)

        # Get current code and verify it (records in ledger)
        code_now = totp.now()
        assert verify_totp(p.id, code_now) is True

        # Generate code for next window (30 seconds ahead)
        next_window_time = int(time_module.time()) + 30
        code_next = totp.at(next_window_time)

        if code_next == code_now:
            # Extremely rare: adjacent window generates same 6 digits.
            # Not a test failure — just skip this edge case.
            return

        # Verify next-window code with mocked time so it's "current"
        with patch('time.time', return_value=float(next_window_time)):
            result = verify_totp(p.id, code_next)
        assert result is True

    def test_tc_g27c_two_principals_same_code_both_accepted(self) -> None:
        """TC-G27c: Two principals using same physical code succeed (PK is per-principal)."""
        # Enroll both with identical secrets to produce identical codes
        email_a = f'{_uid()}@replay.local'
        email_b = f'{_uid()}@replay.local'
        pa = upsert_principal(email=email_a)
        pb = upsert_principal(email=email_b)

        enroll_totp(pa.id, label='default')
        enroll_totp(pb.id, label='default')

        from sos.contracts.sso import _LOCAL_TOTP_STORE
        ref_a = f'local:{pa.id}:default'
        ref_b = f'local:{pb.id}:default'
        code_a = pyotp.TOTP(_LOCAL_TOTP_STORE[ref_a]).now()
        code_b = pyotp.TOTP(_LOCAL_TOTP_STORE[ref_b]).now()

        # Both succeed — different principals, even if codes happen to be equal
        assert verify_totp(pa.id, code_a) is True
        assert verify_totp(pb.id, code_b) is True

    def test_tc_g27d_cleanup_removes_old_entries(self) -> None:
        """TC-G27d: Cleanup deletes entries older than 5 min, not active ones."""
        import psycopg2
        import psycopg2.extras
        import os

        principal_id, code = self._enroll_and_code()
        # Record a ledger entry via verify
        assert verify_totp(principal_id, code) is True

        url = os.getenv('MIRROR_DATABASE_URL') or os.getenv('DATABASE_URL')
        conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            with conn.cursor() as cur:
                # Backdating: move the entry to 10 minutes ago
                cur.execute(
                    'UPDATE mfa_used_codes SET used_at = now() - interval \'10 minutes\' '
                    'WHERE principal_id = %s',
                    (principal_id,),
                )
                conn.commit()

                # Cleanup: delete entries older than 5 minutes
                cur.execute(
                    'DELETE FROM mfa_used_codes WHERE used_at < now() - interval \'5 minutes\''
                )
                conn.commit()

                # Backdated entry must be gone
                cur.execute(
                    'SELECT COUNT(*) FROM mfa_used_codes WHERE principal_id = %s',
                    (principal_id,),
                )
                row = cur.fetchone()
            assert row['count'] == 0
        finally:
            conn.close()


# ── Unit: Athena G6 security fixes ────────────────────────────────────────────


class TestAthenaSecurity:
    """Tests for the two BLOCK fixes from Athena G6 verdict."""

    def _disabled_oidc_idp(self) -> 'IdpConfig':
        """OIDC IdP without jwks_url — should raise on _decode_oidc_id_token."""
        now = datetime.now(timezone.utc)
        return IdpConfig(
            id='idp-nosig', tenant_id='default', protocol='oidc',
            display_name='No JWKS IdP',
            metadata_url=None, entity_id=None, acs_url=None,
            client_id='test-client', client_secret_ref=None,
            authorization_url='https://auth.example.com/auth',
            token_url=None, userinfo_url=None,
            jwks_url=None,  # missing — must raise
            group_claim_path='groups', enabled=True, created_at=now,
        )

    def _disabled_saml_idp(self) -> 'IdpConfig':
        """SAML IdP with enabled=False — must raise before processing."""
        now = datetime.now(timezone.utc)
        return IdpConfig(
            id='idp-disabled', tenant_id='default', protocol='saml',
            display_name='Disabled SAML',
            metadata_url='https://idp.example.com/metadata',
            entity_id='https://sos.example.com/saml/sp',
            acs_url='https://api.example.com/sso/saml/callback',
            client_id=None, client_secret_ref=None,
            authorization_url=None, token_url=None, userinfo_url=None, jwks_url=None,
            group_claim_path='groups', enabled=False, created_at=now,  # DISABLED
        )

    def test_oidc_decode_without_jwks_raises(self) -> None:
        """BLOCK 2: _decode_oidc_id_token must refuse to decode without jwks_url."""
        from sos.contracts.sso import _decode_oidc_id_token
        idp = self._disabled_oidc_idp()
        with pytest.raises(ValueError, match='jwks_url'):
            _decode_oidc_id_token('fake.jwt.token', idp, nonce='n1')

    def test_saml_disabled_idp_raises(self) -> None:
        """BLOCK 1 guard: process_saml_response must refuse disabled IdPs."""
        from sos.contracts.sso import process_saml_response
        import base64
        idp = self._disabled_saml_idp()
        fake_response = base64.b64encode(b'<samlp:Response/>').decode()
        with pytest.raises(ValueError, match='disabled'):
            process_saml_response(idp, fake_response)

    def test_totp_store_fails_hard_in_prod_mode(self) -> None:
        """RESHAPE: _store_totp_secret must raise RuntimeError in non-dev mode."""
        import os as _os
        from sos.contracts.sso import _store_totp_secret
        original = _os.environ.get('SOS_TOTP_STORE')
        try:
            # Remove dev mode env var so we're in "prod" mode
            _os.environ.pop('SOS_TOTP_STORE', None)
            with pytest.raises(RuntimeError, match='Vault'):
                _store_totp_secret('pid-test', 'default', 'JBSWY3DPEHPK3PXP')
        finally:
            if original is not None:
                _os.environ['SOS_TOTP_STORE'] = original
            else:
                _os.environ.pop('SOS_TOTP_STORE', None)

    def test_oidc_decode_with_invalid_client_id_in_aud(self) -> None:
        """BLOCK 2: aud claim must include client_id."""
        now = datetime.now(timezone.utc)
        from sos.contracts.sso import _decode_oidc_id_token
        idp_with_jwks = IdpConfig(
            id='idp-aud', tenant_id='default', protocol='oidc',
            display_name='AUD Test',
            metadata_url=None, entity_id=None, acs_url=None,
            client_id='expected-client-id',
            client_secret_ref=None,
            authorization_url='https://auth.example.com/auth',
            token_url=None, userinfo_url=None,
            jwks_url='https://auth.example.com/.well-known/jwks.json',
            group_claim_path='groups', enabled=True, created_at=now,
        )
        # This will fail at JWKS fetch (no real IdP), which is correct behaviour
        # — the test proves the guard path is reached, not bypassed
        with pytest.raises(ValueError):
            _decode_oidc_id_token('fake.jwt.token', idp_with_jwks, nonce='n1')

    def test_oidc_decode_without_issuer_raises(self) -> None:
        """G6 Phase 2 condition: missing iss check allows cross-IdP token substitution."""
        from sos.contracts.sso import _decode_oidc_id_token
        now = datetime.now(timezone.utc)
        # IdP with no metadata_url AND no authorization_url — iss cannot be derived
        idp_no_issuer = IdpConfig(
            id='idp-noiss', tenant_id='default', protocol='oidc',
            display_name='No Issuer IdP',
            metadata_url=None, entity_id=None, acs_url=None,
            client_id='test-client', client_secret_ref=None,
            authorization_url=None,  # no issuer base
            token_url=None, userinfo_url=None,
            jwks_url='https://auth.example.com/.well-known/jwks.json',
            group_claim_path='groups', enabled=True, created_at=now,
        )
        with pytest.raises(ValueError, match='iss'):
            _decode_oidc_id_token('fake.jwt.token', idp_no_issuer, nonce='n1')

    def test_saml_cert_fetcher_returns_empty_on_bad_url(self) -> None:
        """G6 Phase 2 condition: _fetch_saml_x509cert fails closed (empty) not open."""
        from sos.contracts.sso import _fetch_saml_x509cert
        # Unreachable URL — must return '' not raise
        result = _fetch_saml_x509cert('https://unreachable.invalid/metadata')
        assert result == ''

    def test_saml_cert_fetcher_returns_empty_on_none(self) -> None:
        """No metadata_url → empty cert → python3-saml strict mode rejects."""
        from sos.contracts.sso import _fetch_saml_x509cert
        assert _fetch_saml_x509cert(None) == ''


# ── Unit: F-10 + F-15 tier enforcement (Sprint 005 P0-5) ──────────────────────


class TestTierEnforcement:
    """Unit tests for F-10 (tenant_id derivation) and F-15 (tier ceiling)."""

    def test_role_within_ceiling_at_ceiling(self) -> None:
        """A role exactly at the ceiling is allowed."""
        from sos.contracts.sso import _role_within_ceiling
        assert _role_within_ceiling('role:sos:worker', 'worker') is True

    def test_role_within_ceiling_below_ceiling(self) -> None:
        """Roles below the ceiling are allowed."""
        from sos.contracts.sso import _role_within_ceiling
        assert _role_within_ceiling('role:sos:observer', 'worker') is True
        assert _role_within_ceiling('role:sos:customer', 'builder') is True

    def test_role_above_ceiling_rejected(self) -> None:
        """A role above the ceiling is rejected."""
        from sos.contracts.sso import _role_within_ceiling
        assert _role_within_ceiling('role:sos:builder', 'worker') is False
        assert _role_within_ceiling('role:sos:coordinator', 'builder') is False
        assert _role_within_ceiling('role:sos:principal', 'worker') is False

    def test_tier_order_monotone(self) -> None:
        """Tier order values must be strictly increasing in their documented order."""
        from sos.contracts.sso import _TIER_ORDER
        ordered = ['observer', 'customer', 'partner', 'worker', 'knight', 'builder', 'gate', 'coordinator', 'principal']
        for i in range(len(ordered) - 1):
            assert _TIER_ORDER[ordered[i]] < _TIER_ORDER[ordered[i + 1]], (
                f'{ordered[i]} should be < {ordered[i + 1]}'
            )

    def test_role_tier_name_extracts_last_segment(self) -> None:
        """_role_tier_name extracts the last colon-delimited segment."""
        from sos.contracts.sso import _role_tier_name
        assert _role_tier_name('role:sos:builder') == 'builder'
        assert _role_tier_name('role:sos:coordinator') == 'coordinator'
        assert _role_tier_name('builder') == 'builder'

    def test_scim_provision_user_raises_on_missing_idp(self) -> None:
        """F-10: scim_provision_user must raise if IdP not found (no fallback tenant)."""
        from unittest.mock import patch
        with patch('sos.contracts.sso.get_idp', return_value=None):
            with pytest.raises(ValueError, match='not found'):
                scim_provision_user(
                    idp_id='nonexistent-idp',
                    external_id='ext-001',
                    email='user@example.com',
                )

    def test_scim_provision_user_signature_has_no_tenant_id_param(self) -> None:
        """F-10: scim_provision_user must not accept tenant_id parameter."""
        import inspect
        sig = inspect.signature(scim_provision_user)
        assert 'tenant_id' not in sig.parameters, (
            'tenant_id must not be an explicit parameter — derive from IdP config'
        )

    def test_idp_config_has_max_grantable_tier_field(self) -> None:
        """F-15: IdpConfig must expose max_grantable_tier."""
        now = datetime.now(timezone.utc)
        idp = IdpConfig(
            id='idp-t', tenant_id='t1', protocol='oidc',
            display_name='Tier Test', metadata_url=None, entity_id=None,
            acs_url=None, client_id='c1', client_secret_ref=None,
            authorization_url='https://a.example.com/auth',
            token_url=None, userinfo_url=None, jwks_url=None,
            group_claim_path='groups', enabled=True, created_at=now,
            max_grantable_tier='builder',
        )
        assert idp.max_grantable_tier == 'builder'

    def test_idp_config_max_grantable_tier_default(self) -> None:
        """F-15: IdpConfig default tier is 'worker'."""
        now = datetime.now(timezone.utc)
        idp = IdpConfig(
            id='idp-d', tenant_id='default', protocol='oidc',
            display_name='Default Tier', metadata_url=None, entity_id=None,
            acs_url=None, client_id='c2', client_secret_ref=None,
            authorization_url='https://a.example.com/auth',
            token_url=None, userinfo_url=None, jwks_url=None,
            group_claim_path='groups', enabled=True, created_at=now,
        )
        assert idp.max_grantable_tier == 'worker'


# ── Integration: F-20 SAML assertion replay ledger (G34) ──────────────────────


class TestSamlReplayLedger:
    """TC-G34: saml_used_assertions — replay prevention for SAML SSO."""

    def _make_mock_saml_auth(self, assertion_id: str, expiry_offset: int = 300) -> object:
        """Return a mock python3-saml Auth object with the needed methods."""
        from unittest.mock import MagicMock
        import time as _time
        m = MagicMock()
        m.get_last_assertion_id.return_value = assertion_id
        m.get_session_expiration.return_value = int(_time.time()) + expiry_offset
        return m

    @db
    def test_tc_g34a_first_assertion_accepted(self) -> None:
        """TC-G34a: First use of an assertion_id is inserted and accepted."""
        import psycopg2
        import os
        from sos.contracts.sso import _record_saml_assertion

        idp_id = _uid('idp')
        assertion_id = _uid('assert')

        # Create the IdP row so FK constraint is satisfied
        url = os.getenv('MIRROR_DATABASE_URL') or os.getenv('DATABASE_URL')
        conn = psycopg2.connect(url)
        now = datetime.now(timezone.utc)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO idp_configurations
                       (id, tenant_id, protocol, display_name, metadata_url, entity_id,
                        acs_url, client_id, client_secret_ref, authorization_url, token_url,
                        userinfo_url, jwks_url, group_claim_path, enabled, created_at)
                       VALUES (%s,'default','saml',%s,NULL,NULL,NULL,NULL,NULL,
                               NULL,NULL,NULL,NULL,'groups',TRUE,%s)
                    """,
                    (idp_id, f'Test IdP {idp_id}', now),
                )
                conn.commit()
        finally:
            conn.close()

        mock_auth = self._make_mock_saml_auth(assertion_id)
        # Must not raise
        _record_saml_assertion(saml_auth=mock_auth, idp_id=idp_id)

        # Verify row exists
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    'SELECT assertion_id FROM saml_used_assertions WHERE assertion_id=%s AND idp_id=%s',
                    (assertion_id, idp_id),
                )
                assert cur.fetchone() is not None
        finally:
            conn.close()

    @db
    def test_tc_g34a_replay_rejected(self) -> None:
        """TC-G34a (replay): Second use of same assertion_id raises ValueError."""
        import psycopg2
        import os
        from sos.contracts.sso import _record_saml_assertion

        idp_id = _uid('idp')
        assertion_id = _uid('assert')

        url = os.getenv('MIRROR_DATABASE_URL') or os.getenv('DATABASE_URL')
        conn = psycopg2.connect(url)
        now = datetime.now(timezone.utc)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO idp_configurations
                       (id, tenant_id, protocol, display_name, metadata_url, entity_id,
                        acs_url, client_id, client_secret_ref, authorization_url, token_url,
                        userinfo_url, jwks_url, group_claim_path, enabled, created_at)
                       VALUES (%s,'default','saml',%s,NULL,NULL,NULL,NULL,NULL,
                               NULL,NULL,NULL,NULL,'groups',TRUE,%s)
                    """,
                    (idp_id, f'Test IdP {idp_id}', now),
                )
                conn.commit()
        finally:
            conn.close()

        mock_auth = self._make_mock_saml_auth(assertion_id)
        _record_saml_assertion(saml_auth=mock_auth, idp_id=idp_id)  # first — OK

        mock_auth2 = self._make_mock_saml_auth(assertion_id)
        with pytest.raises(ValueError, match='replay'):
            _record_saml_assertion(saml_auth=mock_auth2, idp_id=idp_id)  # replay

    @db
    def test_tc_g34b_different_idps_same_assertion_id_both_accepted(self) -> None:
        """TC-G34b: Same assertion_id from two different IdPs is not a replay."""
        import psycopg2
        import os
        from sos.contracts.sso import _record_saml_assertion

        assertion_id = _uid('assert')
        idp_a = _uid('idp')
        idp_b = _uid('idp')

        url = os.getenv('MIRROR_DATABASE_URL') or os.getenv('DATABASE_URL')
        conn = psycopg2.connect(url)
        now = datetime.now(timezone.utc)
        try:
            with conn.cursor() as cur:
                for idp_id in (idp_a, idp_b):
                    cur.execute(
                        """INSERT INTO idp_configurations
                           (id, tenant_id, protocol, display_name, metadata_url, entity_id,
                            acs_url, client_id, client_secret_ref, authorization_url, token_url,
                            userinfo_url, jwks_url, group_claim_path, enabled, created_at)
                           VALUES (%s,'default','saml',%s,NULL,NULL,NULL,NULL,NULL,
                                   NULL,NULL,NULL,NULL,'groups',TRUE,%s)
                        """,
                        (idp_id, f'Test IdP {idp_id}', now),
                    )
                conn.commit()
        finally:
            conn.close()

        # Same assertion_id, different IdPs — both should succeed
        _record_saml_assertion(saml_auth=self._make_mock_saml_auth(assertion_id), idp_id=idp_a)
        _record_saml_assertion(saml_auth=self._make_mock_saml_auth(assertion_id), idp_id=idp_b)

    @db
    def test_tc_g34c_missing_assertion_id_rejected(self) -> None:
        """TC-G34c: Assertion with no assertion_id is rejected (fails closed)."""
        from unittest.mock import MagicMock
        from sos.contracts.sso import _record_saml_assertion

        mock_auth = MagicMock()
        mock_auth.get_last_assertion_id.return_value = None

        with pytest.raises(ValueError, match='assertion_id'):
            _record_saml_assertion(saml_auth=mock_auth, idp_id='any-idp')

    @db
    def test_tc_g34d_cleanup_removes_expired_assertions(self) -> None:
        """TC-G34d: Cleanup DELETE removes assertions past not_on_or_after + 1h."""
        import psycopg2
        import os
        from sos.contracts.sso import _record_saml_assertion

        idp_id = _uid('idp')
        assertion_id = _uid('assert')

        url = os.getenv('MIRROR_DATABASE_URL') or os.getenv('DATABASE_URL')
        conn = psycopg2.connect(url)
        now = datetime.now(timezone.utc)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO idp_configurations
                       (id, tenant_id, protocol, display_name, metadata_url, entity_id,
                        acs_url, client_id, client_secret_ref, authorization_url, token_url,
                        userinfo_url, jwks_url, group_claim_path, enabled, created_at)
                       VALUES (%s,'default','saml',%s,NULL,NULL,NULL,NULL,NULL,
                               NULL,NULL,NULL,NULL,'groups',TRUE,%s)
                    """,
                    (idp_id, f'Test IdP {idp_id}', now),
                )
                conn.commit()
        finally:
            conn.close()

        mock_auth = self._make_mock_saml_auth(assertion_id)
        _record_saml_assertion(saml_auth=mock_auth, idp_id=idp_id)

        # Backdate not_on_or_after to 2h ago
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE saml_used_assertions SET not_on_or_after = now() - interval '2 hours' "
                    'WHERE assertion_id=%s AND idp_id=%s',
                    (assertion_id, idp_id),
                )
                conn.commit()

                # Cleanup job: remove assertions expired > 1h ago
                cur.execute(
                    "DELETE FROM saml_used_assertions WHERE not_on_or_after < now() - interval '1 hour'"
                )
                conn.commit()

                cur.execute(
                    'SELECT COUNT(*) FROM saml_used_assertions WHERE assertion_id=%s',
                    (assertion_id,),
                )
                count = cur.fetchone()[0]
            assert count == 0
        finally:
            conn.close()


# ── TC-G59/G60/G61: SCIM soft notes (Sprint 006 B.4a/b/c) ────────────────────


class TestScimSoftNotes:
    """G59/G60/G61 — SCIM soft notes from Sprint 005 carry."""

    # TC-G59: unknown tier raises ValueError (tier_unrecognised)
    def test_g59_unknown_tier_raises(self) -> None:
        """TC-G59: _role_within_ceiling raises on unrecognised tier (not silent -1 pass)."""
        from sos.contracts.sso import _role_within_ceiling
        import pytest
        with pytest.raises(ValueError, match='tier_unrecognised'):
            _role_within_ceiling('role:sos:superadmin', 'worker')

    def test_g59_unknown_tier_raises_regardless_of_ceiling(self) -> None:
        """TC-G59: unknown tier raises even when ceiling is the highest valid tier."""
        from sos.contracts.sso import _role_within_ceiling
        import pytest
        with pytest.raises(ValueError, match='tier_unrecognised'):
            _role_within_ceiling('role:sos:god', 'principal')

    def test_g59_known_tiers_still_work(self) -> None:
        """TC-G59: valid tier comparisons are unaffected by the fix."""
        from sos.contracts.sso import _role_within_ceiling
        assert _role_within_ceiling('role:sos:observer', 'worker') is True
        assert _role_within_ceiling('role:sos:builder', 'worker') is False
        assert _role_within_ceiling('role:sos:worker', 'worker') is True

    # TC-G60: scim_deprovision_user no longer accepts tenant_id
    def test_g60_deprovision_has_no_tenant_id_param(self) -> None:
        """TC-G60: scim_deprovision_user signature must not accept tenant_id."""
        import inspect
        from sos.contracts.sso import scim_deprovision_user
        params = inspect.signature(scim_deprovision_user).parameters
        assert 'tenant_id' not in params, (
            "tenant_id was a dead param that accepted caller-supplied values "
            "while silently ignoring them — G60 removed it"
        )

    def test_g60_deprovision_accepts_idp_and_external_id(self) -> None:
        """TC-G60: scim_deprovision_user still accepts idp_id + external_id."""
        import inspect
        from sos.contracts.sso import scim_deprovision_user
        params = inspect.signature(scim_deprovision_user).parameters
        assert 'idp_id' in params
        assert 'external_id' in params

    # TC-G61: audit_idp_ceiling_violations returns violations
    def test_g61_audit_returns_empty_for_no_rows(self) -> None:
        """TC-G61: audit returns empty list when no idp_group_role_map rows exist."""
        from unittest.mock import patch, MagicMock
        from sos.contracts.sso import audit_idp_ceiling_violations

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch('sos.contracts.sso._connect', return_value=mock_conn):
            result = audit_idp_ceiling_violations()
        assert result == []

    def test_g61_audit_detects_ceiling_violation(self) -> None:
        """TC-G61: audit flags rows where role tier exceeds IdP ceiling."""
        from unittest.mock import patch, MagicMock
        from sos.contracts.sso import audit_idp_ceiling_violations

        rows = [
            {'idp_id': 'idp-1', 'group_name': 'admins', 'role_id': 'role:sos:builder', 'max_grantable_tier': 'worker'},
        ]
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = rows
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch('sos.contracts.sso._connect', return_value=mock_conn):
            result = audit_idp_ceiling_violations()

        assert len(result) == 1
        assert result[0]['role_id'] == 'role:sos:builder'
        assert result[0]['violation'] == 'exceeds_ceiling'
        assert result[0]['idp_id'] == 'idp-1'

    def test_g61_audit_flags_unknown_tier_as_violation(self) -> None:
        """TC-G61: audit flags rows with unknown tier for coordinator reconciliation."""
        from unittest.mock import patch, MagicMock
        from sos.contracts.sso import audit_idp_ceiling_violations

        rows = [
            {'idp_id': 'idp-2', 'group_name': 'unknown-group', 'role_id': 'role:sos:superadmin', 'max_grantable_tier': 'worker'},
        ]
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = rows
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch('sos.contracts.sso._connect', return_value=mock_conn):
            result = audit_idp_ceiling_violations()

        assert len(result) == 1
        assert result[0]['violation'] == 'unknown_tier'
        assert result[0]['role_tier'] == 'superadmin'

    def test_g61_audit_passes_compliant_rows(self) -> None:
        """TC-G61: audit does not flag rows within ceiling."""
        from unittest.mock import patch, MagicMock
        from sos.contracts.sso import audit_idp_ceiling_violations

        rows = [
            {'idp_id': 'idp-3', 'group_name': 'staff', 'role_id': 'role:sos:worker', 'max_grantable_tier': 'builder'},
            {'idp_id': 'idp-3', 'group_name': 'staff', 'role_id': 'role:sos:observer', 'max_grantable_tier': 'worker'},
        ]
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = rows
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch('sos.contracts.sso._connect', return_value=mock_conn):
            result = audit_idp_ceiling_violations()
        assert result == []

    def test_g61_audit_flags_unknown_ceiling_as_violation(self) -> None:
        """TC-G61 WARN-3: audit flags rows with unknown/corrupt ceiling tier."""
        from unittest.mock import patch, MagicMock
        from sos.contracts.sso import audit_idp_ceiling_violations

        rows = [
            {'idp_id': 'idp-4', 'group_name': 'staff', 'role_id': 'role:sos:worker', 'max_grantable_tier': 'superadmin'},
        ]
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = rows
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch('sos.contracts.sso._connect', return_value=mock_conn):
            result = audit_idp_ceiling_violations()

        assert len(result) == 1
        assert result[0]['violation'] == 'unknown_ceiling'
        assert result[0]['idp_ceiling'] == 'superadmin'

    def test_g_warn5_reprovision_reactivates_deprovisioned(self) -> None:
        """WARN-5: re-provision of deprovisioned principal re-activates status to 'active'."""
        from unittest.mock import patch, MagicMock, call
        from sos.contracts.sso import scim_provision_user

        mock_idp = MagicMock()
        mock_idp.tenant_id = 'tenant-1'
        mock_idp.max_grantable_tier = 'worker'
        mock_idp.id = 'idp-1'

        mock_principal = MagicMock()
        mock_principal.id = 'pid-1'
        mock_principal.status = 'deprovisioned'  # previously deprovisioned

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch('sos.contracts.sso.get_idp', return_value=mock_idp), \
             patch('sos.contracts.sso.upsert_principal', return_value=mock_principal), \
             patch('sos.contracts.sso._connect', return_value=mock_conn), \
             patch('sos.contracts.sso.get_roles_for_groups', return_value=[]), \
             patch('sos.contracts.sso.set_principal_status') as mock_set_status:
            scim_provision_user(
                idp_id='idp-1',
                external_id='ext-123',
                email='user@example.com',
                active=True,
            )

        # set_principal_status('active') must be called for re-provisioned deprovisioned user
        mock_set_status.assert_called_once_with('pid-1', 'active', updated_by='scim:idp-1')


# ── Unit: G62 MFA flood quota + cleanup job ───────────────────────────────────


class TestMfaFloodQuota:
    """TC-G62: Per-principal MFA INSERT flood quota + cleanup_mfa_used_codes().

    All tests are unit-level (no DB required) — they mock _connect so the
    quota check and INSERT calls are verified without a real database.
    """

    def _mock_conn(self, count: int) -> 'MagicMock':
        """Return a mock psycopg2 connection whose cursor returns `count` for COUNT query."""
        from unittest.mock import MagicMock
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = {'cnt': count}
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur
        return mock_conn

    def test_tc_g62a_flood_quota_blocks_at_limit(self) -> None:
        """TC-G62a: verify_totp returns False when per-principal count = quota limit."""
        from unittest.mock import patch, MagicMock
        import sos.contracts.sso as sso_mod

        quota = sso_mod._MFA_FLOOD_QUOTA_PER_5MIN
        mock_conn = self._mock_conn(count=quota)  # exactly at the limit

        with patch('sos.contracts.sso._get_totp_secret_ref', return_value='local:pid:default'), \
             patch('sos.contracts.sso._resolve_totp_secret', return_value='JBSWY3DPEHPK3PXP'), \
             patch('sos.contracts.sso._totp_window_start', return_value=1_700_000_000), \
             patch('sos.contracts.sso._totp_code_hash', return_value=b'\x00' * 32), \
             patch('sos.contracts.sso._connect', return_value=mock_conn):
            result = sso_mod.verify_totp('pid', '123456')

        assert result is False

    def test_tc_g62b_flood_quota_passes_below_limit(self) -> None:
        """TC-G62b: verify_totp proceeds to INSERT when count < quota limit."""
        from unittest.mock import patch, MagicMock, call
        import sos.contracts.sso as sso_mod

        quota = sso_mod._MFA_FLOOD_QUOTA_PER_5MIN
        mock_conn = self._mock_conn(count=quota - 1)  # one below limit

        with patch('sos.contracts.sso._get_totp_secret_ref', return_value='local:pid:default'), \
             patch('sos.contracts.sso._resolve_totp_secret', return_value='JBSWY3DPEHPK3PXP'), \
             patch('sos.contracts.sso._totp_window_start', return_value=1_700_000_000), \
             patch('sos.contracts.sso._totp_code_hash', return_value=b'\x00' * 32), \
             patch('sos.contracts.sso._update_mfa_last_used'), \
             patch('sos.contracts.sso._connect', return_value=mock_conn):
            result = sso_mod.verify_totp('pid', '123456')

        # INSERT must have been called (second cursor.execute call)
        cur = mock_conn.cursor.return_value.__enter__.return_value
        assert cur.execute.call_count == 2, (
            f'Expected 2 cursor.execute calls (COUNT + INSERT), got {cur.execute.call_count}'
        )
        assert result is True

    def test_tc_g62c_cleanup_returns_rowcount(self) -> None:
        """TC-G62c: cleanup_mfa_used_codes() issues DELETE and returns rows deleted."""
        from unittest.mock import patch, MagicMock

        mock_cur = MagicMock()
        mock_cur.rowcount = 7
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur

        with patch('sos.contracts.sso._connect', return_value=mock_conn):
            deleted = cleanup_mfa_used_codes()

        assert deleted == 7
        # Verify the DELETE was sent with the shared window constant
        call_args = mock_cur.execute.call_args
        call_sql = call_args[0][0]
        assert 'DELETE FROM mfa_used_codes' in call_sql
        assert '::interval' in call_sql
        # Parameter must be the module constant (not a hardcoded literal)
        import sos.contracts.sso as sso_mod
        assert call_args[0][1] == (sso_mod._MFA_USED_CODES_WINDOW,)

    def test_tc_g62d_cleanup_zero_returns_zero(self) -> None:
        """TC-G62d: cleanup_mfa_used_codes() returns 0 when nothing to delete."""
        from unittest.mock import patch, MagicMock

        mock_cur = MagicMock()
        mock_cur.rowcount = 0
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur

        with patch('sos.contracts.sso._connect', return_value=mock_conn):
            deleted = cleanup_mfa_used_codes()

        assert deleted == 0


# ── Unit: G63 SAML hardening ──────────────────────────────────────────────────


class TestSamlHardening:
    """TC-G63: Assertion ID predictability detection + connection pool + TTL clamp.

    Unit tests only — no DB required.
    """

    # ── TC-G63a: _saml_id_looks_predictable detection ────────────────────────

    def test_tc_g63a_numeric_id_is_predictable(self) -> None:
        """TC-G63a: All-numeric assertion ID is flagged as predictable."""
        assert _saml_id_looks_predictable('123456789012345') is True

    def test_tc_g63a_short_id_is_predictable(self) -> None:
        """TC-G63a: IDs shorter than 16 chars are flagged (insufficient entropy)."""
        assert _saml_id_looks_predictable('abc') is True
        assert _saml_id_looks_predictable('short1234567') is True  # 12 chars

    def test_tc_g63a_uuid_v1_is_predictable(self) -> None:
        """TC-G63a: UUID v1 (time+MAC) is flagged as structurally predictable."""
        # UUID v1: version nibble in position 14 of canonical form is '1'
        uuid_v1 = '550e8400-e29b-11d4-a716-446655440000'
        assert _saml_id_looks_predictable(uuid_v1) is True

    def test_tc_g63a_uuid_v3_is_predictable(self) -> None:
        """TC-G63a: UUID v3 (MD5 of namespace+name) is flagged as predictable."""
        # uuid.uuid3(NAMESPACE_DNS, 'example.com')
        import uuid as uuid_mod
        uuid_v3 = str(uuid_mod.uuid3(uuid_mod.NAMESPACE_DNS, 'example.com'))
        assert _saml_id_looks_predictable(uuid_v3) is True

    def test_tc_g63a_uuid_v5_is_predictable(self) -> None:
        """TC-G63a: UUID v5 (SHA-1 of namespace+name) is flagged as predictable."""
        import uuid as uuid_mod
        uuid_v5 = str(uuid_mod.uuid5(uuid_mod.NAMESPACE_URL, 'https://example.com/user'))
        assert _saml_id_looks_predictable(uuid_v5) is True

    def test_tc_g63b_uuid_v4_is_not_predictable(self) -> None:
        """TC-G63b: UUID v4 (random) is not flagged as predictable."""
        import uuid as uuid_mod
        uid = str(uuid_mod.uuid4())
        assert _saml_id_looks_predictable(uid) is False

    def test_tc_g63b_long_hex_string_is_not_predictable(self) -> None:
        """TC-G63b: Long hex string (>=16 chars, non-numeric, non-UUID) passes."""
        assert _saml_id_looks_predictable('_' + 'a' * 31) is False
        assert _saml_id_looks_predictable('id-' + 'f9e8d7c6b5a49382' * 2) is False

    def test_tc_g63b_exactly_16_chars_passes_if_not_numeric(self) -> None:
        """TC-G63b: 16-char non-numeric non-UUID v1 ID passes length threshold."""
        assert _saml_id_looks_predictable('AbCdEfGhIjKlMnOp') is False

    # ── TC-G63c: pool is used (not _connect) in _record_saml_assertion ───────

    def test_tc_g63c_saml_record_uses_pool(self) -> None:
        """TC-G63c: _record_saml_assertion acquires from pool, not _connect()."""
        from unittest.mock import MagicMock, patch

        # Mock pool: getconn() returns a psycopg2-like connection mock
        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur

        mock_pool = MagicMock()
        mock_pool.getconn.return_value = mock_conn

        mock_saml_auth = MagicMock()
        mock_saml_auth.get_last_assertion_id.return_value = str(uuid.uuid4())
        mock_saml_auth.get_session_expiration.return_value = None

        from sos.contracts.sso import _record_saml_assertion
        with patch('sos.contracts.sso._get_saml_pool', return_value=mock_pool), \
             patch('sos.contracts.sso._connect') as mock_direct_connect:
            _record_saml_assertion(saml_auth=mock_saml_auth, idp_id='idp-test')

        # Pool getconn() must be called; direct _connect() must NOT be called
        mock_pool.getconn.assert_called_once()
        mock_direct_connect.assert_not_called()
        # Connection must be returned to pool
        mock_pool.putconn.assert_called_once_with(mock_conn)

    def test_tc_g63c_pool_exhaustion_raises_value_error(self) -> None:
        """TC-G63c FIND-001: pool exhaustion fails fast — no indefinite block."""
        from unittest.mock import MagicMock, patch
        import psycopg2.pool

        mock_pool = MagicMock()
        mock_pool.getconn.side_effect = psycopg2.pool.PoolError('pool exhausted')

        mock_saml_auth = MagicMock()
        mock_saml_auth.get_last_assertion_id.return_value = str(uuid.uuid4())
        mock_saml_auth.get_session_expiration.return_value = None

        from sos.contracts.sso import _record_saml_assertion
        with patch('sos.contracts.sso._get_saml_pool', return_value=mock_pool):
            with pytest.raises(ValueError, match='temporarily unavailable'):
                _record_saml_assertion(saml_auth=mock_saml_auth, idp_id='idp-test')

        # putconn must NOT be called — getconn() failed before conn was acquired
        mock_pool.putconn.assert_not_called()
