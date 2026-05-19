"""
§2B.1 SSO / SCIM / MFA HTTP routes — Phase 2

Gate: Athena G6 Phase 2

Provides:
  IdP management (admin-gated)       — POST/GET/DELETE /tenants/{slug}/sso/idp
  SSO callbacks (public)             — POST /sso/{slug}/saml/callback
                                       GET  /sso/{slug}/oidc/callback
                                       GET  /sso/{slug}/oidc/authorize
  SCIM v2 Users (admin-key)          — GET/POST/PUT/DELETE /scim/v2/{slug}/Users
  MFA TOTP (customer Bearer)         — POST/GET/DELETE /my/mfa/totp/*

All SSO logic delegates to sos.contracts.sso and sos.contracts.principals.
This module is a thin HTTP adapter — no auth logic inline.

Auth note: MFA endpoints accept X-Principal-Id header (admin-trusted in v1).
When DISP-001 token issuance ships (Sprint 004), replace with session token decode.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

log = logging.getLogger('sos.saas.sso')

router = APIRouter(tags=['sso'])
_bearer = HTTPBearer(auto_error=False)


# ── Auth helpers ───────────────────────────────────────────────────────────────


def _admin_key() -> str:
    return os.environ.get('SOS_SAAS_ADMIN_KEY') or os.environ.get('MUMEGA_MASTER_KEY', '')


def _require_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    key = _admin_key()
    if not key:
        raise HTTPException(503, detail='Admin key not configured')
    if credentials is None or credentials.credentials != key:
        raise HTTPException(401, detail='unauthorized')


# SCIM uses the same admin key in v1.
# Sprint 004: issue per-IdP SCIM bearer tokens, validate via DISP-001.
_require_scim = _require_admin


# ── Request / response schemas ─────────────────────────────────────────────────


class IdpCreateRequest(BaseModel):
    protocol: str                      # 'saml' | 'oidc'
    display_name: str
    metadata_url: str | None = None
    entity_id: str | None = None
    acs_url: str | None = None
    client_id: str | None = None
    client_secret_ref: str | None = None
    authorization_url: str | None = None
    token_url: str | None = None
    userinfo_url: str | None = None
    jwks_url: str | None = None
    group_claim_path: str = 'groups'
    enabled: bool = True


class ScimUserRequest(BaseModel):
    """Minimal SCIM v2 User — enough for JIT provisioning."""
    userName: str
    displayName: str | None = None
    active: bool = True
    schemas: list[str] = ['urn:ietf:params:scim:schemas:core:2.0:User']


class TotpEnrollRequest(BaseModel):
    label: str = 'default'
    issuer: str = 'SOS'


class TotpVerifyRequest(BaseModel):
    label: str = 'default'
    code: str


# ── IdP management routes ──────────────────────────────────────────────────────


@router.post('/tenants/{slug}/sso/idp', status_code=201)
def create_idp_route(
    slug: str,
    req: IdpCreateRequest,
    _: None = Depends(_require_admin),
) -> dict[str, Any]:
    """Create an IdP configuration for a tenant. Idempotent on (tenant_id, display_name)."""
    from sos.contracts.sso import create_idp

    try:
        idp = create_idp(
            protocol=req.protocol,          # type: ignore[arg-type]
            display_name=req.display_name,
            tenant_id=slug,
            metadata_url=req.metadata_url,
            entity_id=req.entity_id,
            acs_url=req.acs_url,
            client_id=req.client_id,
            client_secret_ref=req.client_secret_ref,
            authorization_url=req.authorization_url,
            token_url=req.token_url,
            userinfo_url=req.userinfo_url,
            jwks_url=req.jwks_url,
            group_claim_path=req.group_claim_path,
            enabled=req.enabled,
        )
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc))

    return {
        'id': idp.id,
        'protocol': idp.protocol,
        'display_name': idp.display_name,
        'tenant_id': idp.tenant_id,
        'enabled': idp.enabled,
    }


@router.get('/tenants/{slug}/sso/idp')
def list_idps_route(
    slug: str,
    _: None = Depends(_require_admin),
) -> dict[str, Any]:
    """List all IdP configurations for a tenant."""
    from sos.contracts.sso import list_idps

    idps = list_idps(tenant_id=slug)
    return {
        'items': [
            {
                'id': idp.id,
                'protocol': idp.protocol,
                'display_name': idp.display_name,
                'enabled': idp.enabled,
            }
            for idp in idps
        ]
    }


@router.delete('/tenants/{slug}/sso/idp/{idp_id}', status_code=204)
def disable_idp_route(
    slug: str,
    idp_id: str,
    _: None = Depends(_require_admin),
) -> None:
    """Disable an IdP configuration (sets enabled=False). Does not delete the row."""
    from sos.contracts.sso import disable_idp, get_idp

    idp = get_idp(idp_id)
    if not idp or idp.tenant_id != slug:
        raise HTTPException(404, detail='IdP not found')
    disable_idp(idp_id)


# ── SAML ACS callback ──────────────────────────────────────────────────────────


@router.post('/sso/{slug}/saml/callback')
async def saml_callback(
    slug: str,
    saml_response: str = Query(..., alias='SAMLResponse'),
) -> dict[str, Any]:
    """
    SAML Assertion Consumer Service (ACS) endpoint.

    Validates the SAMLResponse against the tenant's active SAML IdP and returns
    a LoginResult. The frontend handles the session redirect.
    """
    from sos.contracts.sso import list_idps, process_saml_response

    saml_idps = [idp for idp in list_idps(tenant_id=slug) if idp.protocol == 'saml' and idp.enabled]
    if not saml_idps:
        raise HTTPException(400, detail=f'No active SAML IdP configured for tenant {slug!r}')

    last_error = 'No IdP accepted the assertion'
    for idp in saml_idps:
        try:
            result = process_saml_response(idp, saml_response, tenant_id=slug)
            log.info('SAML login OK: principal=%s tenant=%s', result.principal_id, slug)
            return {
                'principal_id': result.principal_id,
                'email': result.email,
                'display_name': result.display_name,
                'tenant_id': result.tenant_id,
                'roles': result.roles,
                'mfa_required': result.mfa_required,
            }
        except ValueError as exc:
            last_error = str(exc)
            log.warning('SAML IdP %s rejected assertion: %s', idp.id, exc)

    raise HTTPException(401, detail=f'SAML assertion rejected: {last_error}')


# ── OIDC authorize + callback ──────────────────────────────────────────────────


@router.get('/sso/{slug}/oidc/authorize')
def oidc_authorize(
    slug: str,
    idp_id: str = Query(...),
) -> RedirectResponse:
    """
    Build and return the OIDC authorization redirect URL.

    state encodes `{idp_id}:{nonce}` so the callback can recover both.
    In production the nonce must be bound to the user session (HttpOnly cookie).
    """
    import secrets as _secrets
    from sos.contracts.sso import build_oidc_auth_url, get_idp

    idp = get_idp(idp_id)
    if not idp or idp.tenant_id != slug or not idp.enabled:
        raise HTTPException(400, detail='IdP not found or disabled')
    if idp.protocol != 'oidc':
        raise HTTPException(400, detail='IdP is not an OIDC IdP')

    nonce = _secrets.token_urlsafe(16)
    state = f'{idp_id}:{nonce}'
    redirect_uri = f'https://api.mumega.com/sso/{slug}/oidc/callback'

    try:
        auth_url = build_oidc_auth_url(idp, state=state, nonce=nonce, redirect_uri=redirect_uri)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc))

    return RedirectResponse(url=auth_url, status_code=302)


@router.get('/sso/{slug}/oidc/callback')
async def oidc_callback(
    slug: str,
    code: str = Query(...),
    state: str = Query(...),
) -> dict[str, Any]:
    """
    OIDC authorization code callback.

    state must be `{idp_id}:{nonce}` as produced by /oidc/authorize.
    Exchanges code for tokens, validates id_token, JIT-provisions principal.
    """
    from sos.contracts.sso import exchange_oidc_code, get_idp

    parts = state.split(':', 1)
    if len(parts) != 2:
        raise HTTPException(400, detail='Invalid state — expected {idp_id}:{nonce}')
    idp_id, nonce = parts

    idp = get_idp(idp_id)
    if not idp or idp.tenant_id != slug or not idp.enabled:
        raise HTTPException(400, detail='IdP not found or disabled')
    if idp.protocol != 'oidc':
        raise HTTPException(400, detail='IdP is not an OIDC IdP')

    redirect_uri = f'https://api.mumega.com/sso/{slug}/oidc/callback'

    try:
        result = exchange_oidc_code(idp, code=code, redirect_uri=redirect_uri, state=state, nonce=nonce, tenant_id=slug)
    except ValueError as exc:
        log.warning('OIDC callback failed for idp %s: %s', idp_id, exc)
        raise HTTPException(401, detail=f'OIDC login failed: {exc}')

    log.info('OIDC login OK: principal=%s tenant=%s', result.principal_id, slug)
    return {
        'principal_id': result.principal_id,
        'email': result.email,
        'display_name': result.display_name,
        'tenant_id': result.tenant_id,
        'roles': result.roles,
        'mfa_required': result.mfa_required,
    }


# ── SCIM v2 Users ──────────────────────────────────────────────────────────────


def _scim_user_response(principal_id: str, email: str | None, display_name: str | None,
                         active: bool, created_at: str) -> dict[str, Any]:
    return {
        'schemas': ['urn:ietf:params:scim:schemas:core:2.0:User'],
        'id': principal_id,
        'userName': email or principal_id,
        'displayName': display_name,
        'active': active,
        'meta': {'resourceType': 'User', 'created': created_at},
    }


@router.get('/scim/v2/{slug}/Users')
def scim_list_users(
    slug: str,
    startIndex: int = Query(1, ge=1),
    count: int = Query(50, ge=1, le=200),
    _: None = Depends(_require_scim),
) -> dict[str, Any]:
    """SCIM v2 list Users for a tenant (reads from principals table)."""
    from sos.contracts.principals import list_principals_by_tenant

    try:
        principals = list_principals_by_tenant(slug, limit=count, offset=startIndex - 1)
    except Exception as exc:
        log.warning('SCIM list users failed: %s', exc)
        raise HTTPException(500, detail='Internal error listing users')

    resources = [
        _scim_user_response(
            p.id, p.email, p.display_name,
            p.status == 'active',
            p.created_at.isoformat(),
        )
        for p in principals
    ]

    return {
        'schemas': ['urn:ietf:params:scim:api:messages:2.0:ListResponse'],
        'totalResults': len(resources),
        'startIndex': startIndex,
        'itemsPerPage': count,
        'Resources': resources,
    }


@router.post('/scim/v2/{slug}/Users', status_code=201)
def scim_create_user(
    slug: str,
    req: ScimUserRequest,
    _: None = Depends(_require_scim),
) -> dict[str, Any]:
    """SCIM v2 provision a user. Delegates to scim_provision_user contract."""
    from sos.contracts.sso import list_idps, scim_provision_user

    # Find the first active SCIM-capable IdP for this tenant (use its id as idp_id)
    idps = [idp for idp in list_idps(tenant_id=slug) if idp.enabled]
    idp_id = idps[0].id if idps else f'scim:{slug}'

    try:
        principal_id = scim_provision_user(
            idp_id=idp_id,
            external_id=req.userName,
            email=req.userName if '@' in req.userName else None,  # type: ignore[arg-type]
            display_name=req.displayName,
            active=req.active,
            # G60 fix: tenant_id removed from scim_provision_user signature —
            # it is derived from idp_id, never accepted from caller (F-10).
        )
    except Exception as exc:
        log.error('SCIM create user failed: %s', exc)
        raise HTTPException(500, detail='Failed to provision user')

    from sos.contracts.principals import get_principal
    principal = get_principal(principal_id)
    if not principal:
        raise HTTPException(500, detail='Principal created but not found')

    return _scim_user_response(
        principal.id, principal.email, principal.display_name,
        principal.status == 'active',
        principal.created_at.isoformat(),
    )


@router.put('/scim/v2/{slug}/Users/{user_id}')
def scim_update_user(
    slug: str,
    user_id: str,
    req: ScimUserRequest,
    _: None = Depends(_require_scim),
) -> dict[str, Any]:
    """SCIM v2 update. active=False → deprovision (status=deprovisioned)."""
    from sos.contracts.principals import get_principal, set_principal_status, upsert_principal

    principal = get_principal(user_id)
    if not principal or principal.tenant_id != slug:
        raise HTTPException(404, detail='User not found')

    if not req.active and principal.status == 'active':
        set_principal_status(user_id, 'deprovisioned', updated_by='scim')
    elif req.active and principal.status in ('suspended', 'deprovisioned'):
        set_principal_status(user_id, 'active', updated_by='scim')

    # Upsert to pick up display_name change
    updated = upsert_principal(
        principal_id=user_id,
        display_name=req.displayName,
        principal_type=principal.principal_type,
        tenant_id=slug,
    )

    return _scim_user_response(
        updated.id, updated.email, updated.display_name,
        updated.status == 'active',
        updated.created_at.isoformat(),
    )


@router.delete('/scim/v2/{slug}/Users/{user_id}', status_code=204)
def scim_delete_user(
    slug: str,
    user_id: str,
    _: None = Depends(_require_scim),
) -> None:
    """
    SCIM v2 deprovision — triggers §6.11 PIPEDA erasure via deactivate_principal().

    Deactivated principals retain their id but all PII is nulled and
    sso_identity_links are hard-deleted per the nullify-and-confiscate model.
    """
    from sos.contracts.principals import deactivate_principal, get_principal

    principal = get_principal(user_id)
    if not principal or principal.tenant_id != slug:
        raise HTTPException(404, detail='User not found')

    if principal.status == 'deactivated':
        return  # Idempotent — already deactivated

    try:
        deactivate_principal(user_id, requested_by='scim')
    except Exception as exc:
        log.error('SCIM delete user failed for %s: %s', user_id, exc)
        raise HTTPException(500, detail='Deactivation failed')


# ── MFA TOTP routes ────────────────────────────────────────────────────────────


def _resolve_principal_id(x_principal_id: str | None, tenant_slug: str) -> str:
    """
    Resolve principal_id from X-Principal-Id header against tenant.

    Pre-DISP-001: accept the header directly (admin-trusted in v1).
    Sprint 004: replace with DISP-001 session token decode.

    TODO(B.7/G62-P1): With header-supplied identity, a caller within the same
    tenant can submit requests claiming any valid principal_id. For MFA endpoints
    specifically, this means a caller could submit TOTP verify requests against
    another user's principal_id slot. In practice, the flood quota (G62) can only
    be incremented by valid TOTP codes — which require the target's TOTP secret —
    so quota DoS against a victim without their secret is NOT possible. However,
    the broader identity impersonation gap (any action gated on principal_id)
    remains real until DISP-001 ships. Track as Sprint 006 B.7.
    """
    if not x_principal_id:
        raise HTTPException(
            400,
            detail='X-Principal-Id header required (DISP-001 session tokens ship in Sprint 004)',
        )
    from sos.contracts.principals import get_principal
    principal = get_principal(x_principal_id)
    if not principal or principal.tenant_id != tenant_slug:
        raise HTTPException(404, detail='Principal not found for this tenant')
    return principal.id


@router.post('/my/mfa/totp/enroll')
def mfa_totp_enroll(
    req: TotpEnrollRequest,
    x_principal_id: str | None = Header(None),
    x_tenant_slug: str | None = Header(None),
) -> dict[str, Any]:
    """
    Enroll a TOTP second factor for a principal.

    Returns the otpauth:// URI for QR code display. Enrollment is provisional
    until verify_totp() succeeds once.
    """
    from sos.contracts.sso import enroll_totp

    tenant_slug = x_tenant_slug or 'default'
    principal_id = _resolve_principal_id(x_principal_id, tenant_slug)

    try:
        otpauth_uri, _secret_ref = enroll_totp(principal_id, label=req.label, issuer=req.issuer)
    except RuntimeError as exc:
        # Vault not available in dev — surface clearly
        raise HTTPException(503, detail=str(exc))
    except Exception as exc:
        log.error('TOTP enroll failed for %s: %s', principal_id, exc)
        raise HTTPException(500, detail=f'TOTP enrollment failed: {exc}')

    return {
        'principal_id': principal_id,
        'label': req.label,
        'otpauth_uri': otpauth_uri,
    }


@router.post('/my/mfa/totp/verify')
def mfa_totp_verify(
    req: TotpVerifyRequest,
    x_principal_id: str | None = Header(None),
    x_tenant_slug: str | None = Header(None),
) -> dict[str, Any]:
    """Verify a TOTP code. Returns {ok: true} or 401."""
    from sos.contracts.sso import verify_totp

    tenant_slug = x_tenant_slug or 'default'
    principal_id = _resolve_principal_id(x_principal_id, tenant_slug)

    ok = verify_totp(principal_id, req.code, label=req.label)
    if not ok:
        raise HTTPException(401, detail='TOTP code invalid or expired')

    return {'ok': True, 'principal_id': principal_id, 'label': req.label}


@router.get('/my/mfa')
def mfa_list_methods(
    x_principal_id: str | None = Header(None),
    x_tenant_slug: str | None = Header(None),
) -> dict[str, Any]:
    """List enrolled MFA methods for a principal."""
    from sos.contracts.sso import list_mfa_methods

    tenant_slug = x_tenant_slug or 'default'
    principal_id = _resolve_principal_id(x_principal_id, tenant_slug)

    methods = list_mfa_methods(principal_id)
    return {
        'principal_id': principal_id,
        'methods': [
            {
                'method': m.method,
                'label': m.label,
                'enabled': m.enabled,
                'enrolled_at': m.created_at.isoformat(),
            }
            for m in methods
        ],
    }


@router.delete('/my/mfa/{method}/{label}', status_code=204)
def mfa_disable_method(
    method: str,
    label: str,
    x_principal_id: str | None = Header(None),
    x_tenant_slug: str | None = Header(None),
) -> None:
    """Disable a named MFA method for a principal. method: 'totp' | 'webauthn'."""
    from sos.contracts.sso import disable_mfa_method

    tenant_slug = x_tenant_slug or 'default'
    principal_id = _resolve_principal_id(x_principal_id, tenant_slug)

    disabled = disable_mfa_method(principal_id, label, method)  # type: ignore[arg-type]
    if not disabled:
        raise HTTPException(404, detail=f'MFA method {method}/{label} not found for principal')
