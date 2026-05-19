"""
Tests for §2B.1 SSO / SCIM / MFA HTTP routes (Phase 2).

Gate: Athena G6 Phase 2

Strategy:
  - All contract calls are mocked — these are HTTP adapter tests, not contract tests
  - Tests verify: auth enforcement, request routing, response shape, error mapping
  - TestClient runs against the actual FastAPI app (integration-lite)
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Set admin key before importing app to avoid the 503-at-startup path
os.environ.setdefault('SOS_SAAS_ADMIN_KEY', 'test-admin-key')
os.environ.setdefault('SOS_TOTP_STORE', 'local')


@pytest.fixture(scope='module')
def client():
    from sos.services.saas.app import app
    with TestClient(app) as c:
        yield c


ADMIN_HEADERS = {'Authorization': 'Bearer test-admin-key'}
TENANT = 'acme'
NOW = datetime.now(timezone.utc)


def _make_idp(protocol: str = 'oidc', enabled: bool = True) -> MagicMock:
    idp = MagicMock()
    idp.id = f'idp-{protocol}-001'
    idp.tenant_id = TENANT
    idp.protocol = protocol
    idp.display_name = f'Test {protocol.upper()} IdP'
    idp.enabled = enabled
    idp.metadata_url = 'https://idp.example.com/metadata'
    idp.entity_id = 'https://sos.example.com/saml/sp'
    idp.acs_url = 'https://api.example.com/sso/acme/saml/callback'
    idp.client_id = 'test-client'
    idp.client_secret_ref = None
    idp.authorization_url = 'https://idp.example.com/oauth2/authorize'
    idp.token_url = 'https://idp.example.com/oauth2/token'
    idp.jwks_url = 'https://idp.example.com/.well-known/jwks.json'
    idp.group_claim_path = 'groups'
    idp.created_at = NOW
    return idp


def _make_principal(status: str = 'active') -> MagicMock:
    p = MagicMock()
    p.id = 'pid-test-001'
    p.tenant_id = TENANT
    p.email = 'user@acme.com'
    p.display_name = 'Test User'
    p.principal_type = 'human'
    p.status = status
    p.mfa_required = False
    p.last_login_at = None
    p.created_at = NOW
    p.deactivated_at = None
    return p


def _make_login_result() -> MagicMock:
    r = MagicMock()
    r.principal_id = 'pid-test-001'
    r.email = 'user@acme.com'
    r.display_name = 'Test User'
    r.tenant_id = TENANT
    r.roles = []
    r.mfa_required = False
    return r


# ── Auth enforcement ───────────────────────────────────────────────────────────


class TestAuthEnforcement:
    def test_create_idp_requires_admin(self, client: TestClient) -> None:
        resp = client.post(f'/tenants/{TENANT}/sso/idp', json={'protocol': 'oidc', 'display_name': 'x'})
        assert resp.status_code == 401

    def test_list_idps_requires_admin(self, client: TestClient) -> None:
        resp = client.get(f'/tenants/{TENANT}/sso/idp')
        assert resp.status_code == 401

    def test_disable_idp_requires_admin(self, client: TestClient) -> None:
        resp = client.delete(f'/tenants/{TENANT}/sso/idp/idp-001')
        assert resp.status_code == 401

    def test_scim_list_requires_admin(self, client: TestClient) -> None:
        resp = client.get(f'/scim/v2/{TENANT}/Users')
        assert resp.status_code == 401

    def test_scim_create_requires_admin(self, client: TestClient) -> None:
        resp = client.post(f'/scim/v2/{TENANT}/Users', json={'userName': 'user@acme.com'})
        assert resp.status_code == 401

    def test_saml_callback_is_public(self, client: TestClient) -> None:
        # No auth header — should reach handler (will return 400 due to no IdP, not 401)
        with patch('sos.contracts.sso.list_idps', return_value=[]):
            resp = client.post(f'/sso/{TENANT}/saml/callback', params={'SAMLResponse': 'fake'})
        assert resp.status_code == 400
        assert resp.status_code != 401


# ── IdP management ─────────────────────────────────────────────────────────────


class TestIdpManagement:
    def test_create_idp_returns_201(self, client: TestClient) -> None:
        idp = _make_idp('oidc')
        with patch('sos.contracts.sso.create_idp', return_value=idp):
            resp = client.post(
                f'/tenants/{TENANT}/sso/idp',
                headers=ADMIN_HEADERS,
                json={
                    'protocol': 'oidc',
                    'display_name': 'Google Workspace',
                    'client_id': 'gws-client',
                    'authorization_url': 'https://accounts.google.com/o/oauth2/auth',
                    'token_url': 'https://oauth2.googleapis.com/token',
                    'jwks_url': 'https://www.googleapis.com/oauth2/v3/certs',
                    'metadata_url': 'https://accounts.google.com',
                },
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body['id'] == idp.id
        assert body['protocol'] == 'oidc'
        assert body['enabled'] is True

    def test_list_idps_returns_items(self, client: TestClient) -> None:
        idp = _make_idp('saml')
        with patch('sos.contracts.sso.list_idps', return_value=[idp]):
            resp = client.get(f'/tenants/{TENANT}/sso/idp', headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        items = resp.json()['items']
        assert len(items) == 1
        assert items[0]['protocol'] == 'saml'

    def test_list_idps_empty(self, client: TestClient) -> None:
        with patch('sos.contracts.sso.list_idps', return_value=[]):
            resp = client.get(f'/tenants/{TENANT}/sso/idp', headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        assert resp.json()['items'] == []

    def test_disable_idp_returns_204(self, client: TestClient) -> None:
        idp = _make_idp('oidc')
        with patch('sos.contracts.sso.get_idp', return_value=idp), \
             patch('sos.contracts.sso.disable_idp') as mock_disable:
            resp = client.delete(f'/tenants/{TENANT}/sso/idp/{idp.id}', headers=ADMIN_HEADERS)
        assert resp.status_code == 204
        mock_disable.assert_called_once_with(idp.id)

    def test_disable_idp_wrong_tenant_returns_404(self, client: TestClient) -> None:
        idp = _make_idp('oidc')
        idp.tenant_id = 'other-tenant'
        with patch('sos.contracts.sso.get_idp', return_value=idp):
            resp = client.delete(f'/tenants/{TENANT}/sso/idp/{idp.id}', headers=ADMIN_HEADERS)
        assert resp.status_code == 404

    def test_disable_idp_missing_returns_404(self, client: TestClient) -> None:
        with patch('sos.contracts.sso.get_idp', return_value=None):
            resp = client.delete(f'/tenants/{TENANT}/sso/idp/idp-missing', headers=ADMIN_HEADERS)
        assert resp.status_code == 404


# ── SAML callback ──────────────────────────────────────────────────────────────


class TestSamlCallback:
    def test_no_active_saml_idp_returns_400(self, client: TestClient) -> None:
        with patch('sos.contracts.sso.list_idps', return_value=[]):
            resp = client.post(f'/sso/{TENANT}/saml/callback', params={'SAMLResponse': 'fake'})
        assert resp.status_code == 400

    def test_disabled_idp_skipped_returns_400(self, client: TestClient) -> None:
        idp = _make_idp('saml', enabled=False)
        with patch('sos.contracts.sso.list_idps', return_value=[idp]):
            resp = client.post(f'/sso/{TENANT}/saml/callback', params={'SAMLResponse': 'fake'})
        # Disabled IdP filtered out — no active IdPs
        assert resp.status_code == 400

    def test_valid_saml_response_returns_login_result(self, client: TestClient) -> None:
        idp = _make_idp('saml')
        login = _make_login_result()
        with patch('sos.contracts.sso.list_idps', return_value=[idp]), \
             patch('sos.contracts.sso.process_saml_response', return_value=login):
            resp = client.post(f'/sso/{TENANT}/saml/callback', params={'SAMLResponse': 'valid-b64'})
        assert resp.status_code == 200
        body = resp.json()
        assert body['principal_id'] == 'pid-test-001'
        assert body['email'] == 'user@acme.com'

    def test_saml_rejection_returns_401(self, client: TestClient) -> None:
        idp = _make_idp('saml')
        with patch('sos.contracts.sso.list_idps', return_value=[idp]), \
             patch('sos.contracts.sso.process_saml_response', side_effect=ValueError('sig invalid')):
            resp = client.post(f'/sso/{TENANT}/saml/callback', params={'SAMLResponse': 'bad'})
        assert resp.status_code == 401
        assert 'SAML assertion rejected' in resp.json()['detail']


# ── OIDC authorize + callback ──────────────────────────────────────────────────


class TestOidcFlow:
    def test_authorize_redirects_to_idp(self, client: TestClient) -> None:
        idp = _make_idp('oidc')
        with patch('sos.contracts.sso.get_idp', return_value=idp), \
             patch('sos.contracts.sso.build_oidc_auth_url', return_value='https://idp.example.com/auth?foo=bar'):
            resp = client.get(
                f'/sso/{TENANT}/oidc/authorize',
                params={'idp_id': idp.id},
                follow_redirects=False,
            )
        assert resp.status_code == 302
        assert resp.headers['location'].startswith('https://idp.example.com/auth')

    def test_authorize_missing_idp_returns_400(self, client: TestClient) -> None:
        with patch('sos.contracts.sso.get_idp', return_value=None):
            resp = client.get(f'/sso/{TENANT}/oidc/authorize', params={'idp_id': 'missing'})
        assert resp.status_code == 400

    def test_callback_invalid_state_returns_400(self, client: TestClient) -> None:
        resp = client.get(
            f'/sso/{TENANT}/oidc/callback',
            params={'code': 'abc', 'state': 'no-colon-here'},
        )
        assert resp.status_code == 400

    def test_callback_valid_returns_login_result(self, client: TestClient) -> None:
        idp = _make_idp('oidc')
        login = _make_login_result()
        with patch('sos.contracts.sso.get_idp', return_value=idp), \
             patch('sos.contracts.sso.exchange_oidc_code', return_value=login):
            resp = client.get(
                f'/sso/{TENANT}/oidc/callback',
                params={'code': 'auth-code', 'state': f'{idp.id}:test-nonce'},
            )
        assert resp.status_code == 200
        assert resp.json()['principal_id'] == 'pid-test-001'

    def test_callback_oidc_error_returns_401(self, client: TestClient) -> None:
        idp = _make_idp('oidc')
        with patch('sos.contracts.sso.get_idp', return_value=idp), \
             patch('sos.contracts.sso.exchange_oidc_code', side_effect=ValueError('iss mismatch')):
            resp = client.get(
                f'/sso/{TENANT}/oidc/callback',
                params={'code': 'bad', 'state': f'{idp.id}:nonce'},
            )
        assert resp.status_code == 401


# ── SCIM v2 Users ──────────────────────────────────────────────────────────────


class TestScimUsers:
    def test_list_users_returns_scim_response(self, client: TestClient) -> None:
        p = _make_principal()
        with patch('sos.contracts.principals.list_principals_by_tenant', return_value=[p]):
            resp = client.get(f'/scim/v2/{TENANT}/Users', headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body['schemas'] == ['urn:ietf:params:scim:api:messages:2.0:ListResponse']
        assert len(body['Resources']) == 1
        assert body['Resources'][0]['id'] == 'pid-test-001'

    def test_list_users_empty(self, client: TestClient) -> None:
        with patch('sos.contracts.principals.list_principals_by_tenant', return_value=[]):
            resp = client.get(f'/scim/v2/{TENANT}/Users', headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        assert body['totalResults'] == 0 if (body := resp.json()) else True

    def test_create_user_returns_201(self, client: TestClient) -> None:
        idp = _make_idp('oidc')
        p = _make_principal()
        with patch('sos.contracts.sso.list_idps', return_value=[idp]), \
             patch('sos.contracts.sso.scim_provision_user', return_value='pid-test-001'), \
             patch('sos.contracts.principals.get_principal', return_value=p):
            resp = client.post(
                f'/scim/v2/{TENANT}/Users',
                headers=ADMIN_HEADERS,
                json={'userName': 'user@acme.com', 'displayName': 'Test User'},
            )
        assert resp.status_code == 201
        assert resp.json()['id'] == 'pid-test-001'

    def test_delete_user_triggers_erasure(self, client: TestClient) -> None:
        p = _make_principal()
        with patch('sos.contracts.principals.get_principal', return_value=p), \
             patch('sos.contracts.principals.deactivate_principal') as mock_deactivate:
            resp = client.delete(f'/scim/v2/{TENANT}/Users/pid-test-001', headers=ADMIN_HEADERS)
        assert resp.status_code == 204
        mock_deactivate.assert_called_once_with('pid-test-001', requested_by='scim')

    def test_delete_already_deactivated_is_idempotent(self, client: TestClient) -> None:
        p = _make_principal(status='deactivated')
        with patch('sos.contracts.principals.get_principal', return_value=p), \
             patch('sos.contracts.principals.deactivate_principal') as mock_deactivate:
            resp = client.delete(f'/scim/v2/{TENANT}/Users/pid-test-001', headers=ADMIN_HEADERS)
        assert resp.status_code == 204
        mock_deactivate.assert_not_called()

    def test_delete_missing_user_returns_404(self, client: TestClient) -> None:
        with patch('sos.contracts.principals.get_principal', return_value=None):
            resp = client.delete(f'/scim/v2/{TENANT}/Users/pid-missing', headers=ADMIN_HEADERS)
        assert resp.status_code == 404


# ── MFA TOTP ──────────────────────────────────────────────────────────────────


class TestMfaTotp:
    MFA_HEADERS = {
        'X-Principal-Id': 'pid-test-001',
        'X-Tenant-Slug': TENANT,
    }

    def test_enroll_returns_otpauth_uri(self, client: TestClient) -> None:
        p = _make_principal()
        with patch('sos.contracts.principals.get_principal', return_value=p), \
             patch('sos.contracts.sso.enroll_totp', return_value=('otpauth://totp/test', 'local:pid:default')):
            resp = client.post(
                '/my/mfa/totp/enroll',
                headers=self.MFA_HEADERS,
                json={'label': 'default', 'issuer': 'SOS'},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body['otpauth_uri'].startswith('otpauth://')
        assert body['label'] == 'default'

    def test_enroll_missing_principal_id_returns_400(self, client: TestClient) -> None:
        resp = client.post('/my/mfa/totp/enroll', json={'label': 'default'})
        assert resp.status_code == 400

    def test_enroll_unknown_principal_returns_404(self, client: TestClient) -> None:
        with patch('sos.contracts.principals.get_principal', return_value=None):
            resp = client.post(
                '/my/mfa/totp/enroll',
                headers=self.MFA_HEADERS,
                json={'label': 'default'},
            )
        assert resp.status_code == 404

    def test_verify_valid_code_returns_ok(self, client: TestClient) -> None:
        p = _make_principal()
        with patch('sos.contracts.principals.get_principal', return_value=p), \
             patch('sos.contracts.sso.verify_totp', return_value=True):
            resp = client.post(
                '/my/mfa/totp/verify',
                headers=self.MFA_HEADERS,
                json={'label': 'default', 'code': '123456'},
            )
        assert resp.status_code == 200
        assert resp.json()['ok'] is True

    def test_verify_invalid_code_returns_401(self, client: TestClient) -> None:
        p = _make_principal()
        with patch('sos.contracts.principals.get_principal', return_value=p), \
             patch('sos.contracts.sso.verify_totp', return_value=False):
            resp = client.post(
                '/my/mfa/totp/verify',
                headers=self.MFA_HEADERS,
                json={'label': 'default', 'code': '000000'},
            )
        assert resp.status_code == 401

    def test_list_methods_returns_enrolled(self, client: TestClient) -> None:
        p = _make_principal()
        method = MagicMock()
        method.method = 'totp'
        method.label = 'default'
        method.enabled = True
        method.created_at = NOW
        with patch('sos.contracts.principals.get_principal', return_value=p), \
             patch('sos.contracts.sso.list_mfa_methods', return_value=[method]):
            resp = client.get('/my/mfa', headers=self.MFA_HEADERS)
        assert resp.status_code == 200
        methods = resp.json()['methods']
        assert len(methods) == 1
        assert methods[0]['method'] == 'totp'

    def test_disable_method_returns_204(self, client: TestClient) -> None:
        p = _make_principal()
        with patch('sos.contracts.principals.get_principal', return_value=p), \
             patch('sos.contracts.sso.disable_mfa_method', return_value=True):
            resp = client.delete('/my/mfa/totp/default', headers=self.MFA_HEADERS)
        assert resp.status_code == 204

    def test_disable_missing_method_returns_404(self, client: TestClient) -> None:
        p = _make_principal()
        with patch('sos.contracts.principals.get_principal', return_value=p), \
             patch('sos.contracts.sso.disable_mfa_method', return_value=False):
            resp = client.delete('/my/mfa/totp/nonexistent', headers=self.MFA_HEADERS)
        assert resp.status_code == 404
