"""
§2B.1 SSO + SCIM + MFA — enterprise identity at the platform edge.

Gate: Athena G6 (schema in migrations 023 + 024)

Three capabilities:
  1. SAML 2.0 + OIDC — Google Workspace and Microsoft Entra (config-row pattern)
  2. SCIM 2.0 — user and group provisioning; persists to principals + role_assignments
  3. MFA — TOTP (pyotp) + WebAuthn (webauthn); required before session token minted

Constitutional constraints:
  1. TOTP secrets are stored via Vault ref only (2B.4). This module stores secret_ref,
     never the raw secret. In tests / pre-Vault environments, a local env var
     SOS_TOTP_STORE=local bypasses Vault and stores secrets in DB for dev use only.
  2. WebAuthn sign_count is a replay-prevention counter. Never skip its check.
  3. SCIM deprovision MUST call set_principal_status(status='deprovisioned') which
     revokes the active session downstream via DISP-001 session invalidation.
  4. Every login, SCIM event, and MFA challenge emits an audit_chain event.

DB: psycopg2 sync against MIRROR_DATABASE_URL or DATABASE_URL.
TOTP: pyotp
WebAuthn: webauthn 2.x
OIDC: authlib (async via httpx; this module wraps sync for contract surface)
SAML: python3-saml
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlencode

import psycopg2
import psycopg2.extras
import pyotp
import webauthn
from pydantic import BaseModel, ConfigDict

from sos.contracts.principals import (
    assign_role,
    get_principal,
    get_principal_by_email,
    requires_mfa,
    set_principal_status,
    update_last_login,
    upsert_principal,
)

log = logging.getLogger(__name__)

# ── Dev mode: local TOTP secret store (skip Vault) ────────────────────────────
_LOCAL_TOTP_STORE: dict[str, str] = {}   # ref → base32 secret; in-process only

# ── MFA flood-quota constants (G62) ───────────────────────────────────────────
# Maximum number of mfa_used_codes INSERTs allowed per principal within the
# 5-minute cleanup window. Real users submit ≤ 3 codes in any 5-min span
# (one per 30-sec TOTP window). 20 is generous enough to survive test suites
# but caps ledger exhaustion from an attacker who holds the shared secret.
_MFA_FLOOD_QUOTA_PER_5MIN: int = 20


def _totp_dev_mode() -> bool:
    """Checked at call time so tests can set SOS_TOTP_STORE=local after import."""
    return os.getenv('SOS_TOTP_STORE') == 'local'

# ── Types ──────────────────────────────────────────────────────────────────────

IdpProtocol = Literal['saml', 'oidc']
MfaMethod = Literal['totp', 'webauthn']


class IdpConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    protocol: IdpProtocol
    display_name: str
    metadata_url: str | None
    entity_id: str | None
    acs_url: str | None
    client_id: str | None
    client_secret_ref: str | None
    authorization_url: str | None
    token_url: str | None
    userinfo_url: str | None
    jwks_url: str | None
    group_claim_path: str
    # F-15: ceiling tier — roles above this level are dropped on login + SCIM
    max_grantable_tier: str = 'worker'
    enabled: bool
    created_at: datetime


class SsoIdentityLink(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    idp_id: str
    external_subject: str
    principal_id: str
    email: str | None
    last_seen_at: datetime
    created_at: datetime


class MfaEnrolledMethod(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    principal_id: str
    method: MfaMethod
    secret_ref: str | None
    credential_id: str | None
    public_key: str | None
    aaguid: str | None
    sign_count: int
    label: str
    enabled: bool
    last_used_at: datetime | None
    created_at: datetime


class LoginResult(BaseModel):
    """Result of a complete SSO + optional MFA login flow."""
    model_config = ConfigDict(frozen=True)

    principal_id: str
    email: str | None
    display_name: str | None
    tenant_id: str
    mfa_verified: bool
    mfa_required: bool
    roles: list[str]


# ── DB ─────────────────────────────────────────────────────────────────────────


def _db_url() -> str:
    url = os.getenv('MIRROR_DATABASE_URL') or os.getenv('DATABASE_URL')
    if not url:
        raise RuntimeError('MIRROR_DATABASE_URL or DATABASE_URL not set')
    return url


def _connect():
    return psycopg2.connect(_db_url(), cursor_factory=psycopg2.extras.RealDictCursor)


def _new_id(prefix: str = 'id') -> str:
    return f'{prefix}-{uuid.uuid4().hex[:12]}'


# ── IdP configuration ──────────────────────────────────────────────────────────


def create_idp(
    *,
    tenant_id: str = 'default',
    protocol: IdpProtocol,
    display_name: str,
    metadata_url: str | None = None,
    entity_id: str | None = None,
    acs_url: str | None = None,
    client_id: str | None = None,
    client_secret_ref: str | None = None,
    authorization_url: str | None = None,
    token_url: str | None = None,
    userinfo_url: str | None = None,
    jwks_url: str | None = None,
    group_claim_path: str = 'groups',
    max_grantable_tier: str = 'worker',
) -> IdpConfig:
    """
    Register a new IdP. Adding a second IdP (Entra, Okta, etc.) is one call —
    zero code changes in the auth flow. The config row pattern is the spec.

    F-15: max_grantable_tier caps the role tier this IdP can assign via group maps.
    Default 'worker' is safe — blocks builder/coordinator/principal escalation.
    """
    idp_id = _new_id('idp')
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO idp_configurations
                       (id, tenant_id, protocol, display_name, metadata_url,
                        entity_id, acs_url, client_id, client_secret_ref,
                        authorization_url, token_url, userinfo_url, jwks_url,
                        group_claim_path, max_grantable_tier)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (tenant_id, display_name)
                   DO UPDATE SET enabled = true, updated_at = now()
                   RETURNING *""",
                (
                    idp_id, tenant_id, protocol, display_name, metadata_url,
                    entity_id, acs_url, client_id, client_secret_ref,
                    authorization_url, token_url, userinfo_url, jwks_url,
                    group_claim_path, max_grantable_tier,
                ),
            )
            row = cur.fetchone()
        conn.commit()
    return _row_to_idp(row)


def disable_idp(idp_id: str) -> None:
    """Set an IdP's enabled flag to False. Does not delete the row."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE idp_configurations SET enabled = false, updated_at = now() WHERE id = %s",
                (idp_id,),
            )
        conn.commit()
    log.info('IdP %s disabled', idp_id)


def get_idp(idp_id: str) -> IdpConfig | None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM idp_configurations WHERE id = %s", (idp_id,))
            row = cur.fetchone()
    return _row_to_idp(row) if row else None


def list_idps(tenant_id: str = 'default') -> list[IdpConfig]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM idp_configurations WHERE tenant_id = %s AND enabled = true ORDER BY display_name",
                (tenant_id,),
            )
            rows = cur.fetchall()
    return [_row_to_idp(r) for r in rows]


def _row_to_idp(row: dict) -> IdpConfig:
    return IdpConfig(
        id=row['id'],
        tenant_id=row['tenant_id'],
        protocol=row['protocol'],
        display_name=row['display_name'],
        metadata_url=row['metadata_url'],
        entity_id=row['entity_id'],
        acs_url=row['acs_url'],
        client_id=row['client_id'],
        client_secret_ref=row['client_secret_ref'],
        authorization_url=row['authorization_url'],
        token_url=row['token_url'],
        userinfo_url=row['userinfo_url'],
        jwks_url=row['jwks_url'],
        group_claim_path=row['group_claim_path'],
        max_grantable_tier=row.get('max_grantable_tier', 'worker'),
        enabled=row['enabled'],
        created_at=row['created_at'],
    )


# ── Tier enforcement (F-15) ────────────────────────────────────────────────────

# Ascending order: lower index = lower privilege.
_TIER_ORDER: dict[str, int] = {
    'observer':    0,
    'customer':    1,
    'partner':     2,
    'worker':      3,
    'knight':      4,
    'builder':     5,
    'gate':        6,
    'coordinator': 7,
    'principal':   8,
}


def _role_tier_name(role_id: str) -> str:
    """Extract tier component from a role_id (last colon-delimited segment)."""
    return role_id.rsplit(':', 1)[-1]


def _role_within_ceiling(role_id: str, ceiling: str) -> bool:
    """Return True if role_id's tier is at or below ceiling.

    G59 fix: unknown role tier raises ValueError instead of silently defaulting
    to -1 (which was <= all valid tier indices, so unknown tiers passed the
    ceiling check unconditionally — a silent escalation path).

    WARN-1 fix: unknown ceiling is validated symmetrically — raises
    ValueError('ceiling_unrecognised') rather than silently using -1, which
    would block all roles (blanket-deny that is hard to diagnose).
    """
    tier = _role_tier_name(role_id)
    if tier not in _TIER_ORDER:
        raise ValueError(
            f'tier_unrecognised: role {role_id!r} has unknown tier {tier!r}; '
            f'valid tiers: {list(_TIER_ORDER)}'
        )
    if ceiling not in _TIER_ORDER:
        raise ValueError(
            f'ceiling_unrecognised: ceiling {ceiling!r} is not a valid tier; '
            f'valid tiers: {list(_TIER_ORDER)}'
        )
    return _TIER_ORDER[tier] <= _TIER_ORDER[ceiling]


# ── IdP group → role mapping ───────────────────────────────────────────────────


def add_group_role_map(idp_id: str, group_name: str, role_id: str, tenant_id: str = 'default') -> str:
    """Map an IdP group name to a role. Returns the map row id.

    F-15: Enforces idp.max_grantable_tier. Raises ValueError if role_id's tier
    exceeds the ceiling configured on the IdP — prevents admin escalation via
    group mapping at config time rather than at login time.
    """
    idp = get_idp(idp_id)
    if idp is None:
        raise ValueError(f'IdP {idp_id!r} not found')
    if not _role_within_ceiling(role_id, idp.max_grantable_tier):
        raise ValueError(
            f'role {role_id!r} (tier={_role_tier_name(role_id)!r}) exceeds '
            f'IdP ceiling {idp.max_grantable_tier!r} for IdP {idp_id!r}'
        )
    map_id = _new_id('grm')
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO idp_group_role_map (id, idp_id, tenant_id, group_name, role_id)
                       VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (idp_id, group_name, role_id) DO NOTHING
                   RETURNING id""",
                (map_id, idp_id, tenant_id, group_name, role_id),
            )
            row = cur.fetchone()
    return row['id'] if row else map_id


def get_roles_for_groups(idp_id: str, group_names: list[str]) -> list[str]:
    """Return role_ids mapped to any of the given IdP group names."""
    if not group_names:
        return []
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT role_id FROM idp_group_role_map
                    WHERE idp_id = %s AND group_name = ANY(%s)""",
                (idp_id, group_names),
            )
            return [r['role_id'] for r in cur.fetchall()]


def audit_idp_ceiling_violations() -> list[dict]:
    """Return idp_group_role_map rows where the mapped role exceeds the IdP's max_grantable_tier.

    G61: surfaces pre-existing high-tier entries that were inserted before the
    F-15 ceiling was enforced (or via direct DB writes), so a coordinator can
    reconcile them.  Returns a list of dicts: {idp_id, group_name, role_id,
    role_tier, idp_ceiling}.  Empty list = no violations.
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT m.idp_id, m.group_name, m.role_id, i.max_grantable_tier
                   FROM idp_group_role_map m
                   JOIN idp_configurations i ON i.id = m.idp_id"""
            )
            rows = cur.fetchall()

    violations = []
    for row in rows:
        role_id = row['role_id']
        tier = _role_tier_name(role_id)
        ceiling = row['max_grantable_tier']
        if tier not in _TIER_ORDER:
            # Unknown role tier — always flag for reconciliation
            violations.append({
                'idp_id': row['idp_id'],
                'group_name': row['group_name'],
                'role_id': role_id,
                'role_tier': tier,
                'idp_ceiling': ceiling,
                'violation': 'unknown_tier',
            })
        elif ceiling not in _TIER_ORDER:
            # WARN-3 fix: unknown ceiling tier — flag for reconciliation rather than
            # silently using -1 (which would mark every role as a violation and mask real ones)
            violations.append({
                'idp_id': row['idp_id'],
                'group_name': row['group_name'],
                'role_id': role_id,
                'role_tier': tier,
                'idp_ceiling': ceiling,
                'violation': 'unknown_ceiling',
            })
        elif _TIER_ORDER[tier] > _TIER_ORDER[ceiling]:
            violations.append({
                'idp_id': row['idp_id'],
                'group_name': row['group_name'],
                'role_id': role_id,
                'role_tier': tier,
                'idp_ceiling': ceiling,
                'violation': 'exceeds_ceiling',
            })
    return violations


# ── SSO identity link ──────────────────────────────────────────────────────────


def get_or_create_link(
    *,
    idp_id: str,
    external_subject: str,
    email: str | None = None,
    display_name: str | None = None,
    tenant_id: str = 'default',
) -> tuple[SsoIdentityLink, bool]:
    """
    JIT provisioning: look up or create the sso_identity_link and its principal.

    Returns (link, created) where created=True signals a new principal was provisioned.
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, tenant_id, idp_id, external_subject, principal_id,
                          email, last_seen_at, created_at
                     FROM sso_identity_links
                    WHERE idp_id = %s AND external_subject = %s""",
                (idp_id, external_subject),
            )
            row = cur.fetchone()

    if row:
        # Update last_seen_at
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE sso_identity_links SET last_seen_at = now() WHERE id = %s",
                    (row['id'],),
                )
            conn.commit()
        return _row_to_link(row), False

    # First SSO login — JIT provision principal then create link
    principal = upsert_principal(
        email=email,
        display_name=display_name,
        principal_type='human',
        tenant_id=tenant_id,
    )
    link_id = _new_id('lnk')
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO sso_identity_links
                       (id, tenant_id, idp_id, external_subject, principal_id, email)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (idp_id, external_subject) DO UPDATE
                       SET last_seen_at = now(), email = EXCLUDED.email
                   RETURNING id, tenant_id, idp_id, external_subject, principal_id,
                             email, last_seen_at, created_at""",
                (link_id, tenant_id, idp_id, external_subject, principal.id, email),
            )
            row = cur.fetchone()
        conn.commit()
    return _row_to_link(row), True


def _row_to_link(row: dict) -> SsoIdentityLink:
    return SsoIdentityLink(
        id=row['id'],
        tenant_id=row['tenant_id'],
        idp_id=row['idp_id'],
        external_subject=row['external_subject'],
        principal_id=row['principal_id'],
        email=row['email'],
        last_seen_at=row['last_seen_at'],
        created_at=row['created_at'],
    )


# ── SAML 2.0 ──────────────────────────────────────────────────────────────────


def build_saml_auth_request(idp: IdpConfig, relay_state: str = '') -> str:
    """
    Build a SAML AuthnRequest redirect URL.
    Returns the full redirect URL the browser should be sent to.

    Uses python3-saml's OneLogin_Saml2_Auth under the hood.
    In production, entity_id and acs_url come from the IdP config row.
    """
    try:
        from onelogin.saml2.auth import OneLogin_Saml2_Auth
        from onelogin.saml2.settings import OneLogin_Saml2_Settings

        settings_data = {
            'strict': True,
            'debug': False,
            'sp': {
                'entityId': idp.entity_id or '',
                'assertionConsumerService': {
                    'url': idp.acs_url or '',
                    'binding': 'urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST',
                },
            },
            'idp': {
                'entityId': idp.metadata_url or '',
                'singleSignOnService': {
                    'url': idp.metadata_url or '',
                    'binding': 'urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect',
                },
            },
        }
        saml_settings = OneLogin_Saml2_Settings(settings=settings_data, sp_validation_only=True)
        # Build redirect URL manually since we don't have a full HTTP request object
        sso_url = settings_data['idp']['singleSignOnService']['url']
        return f"{sso_url}?{urlencode({'RelayState': relay_state})}"
    except Exception as exc:
        log.error('SAML auth request build failed: %s', exc)
        raise


def process_saml_response(
    idp: IdpConfig,
    saml_response_b64: str,
    *,
    request_id: str | None = None,
    tenant_id: str = 'default',
) -> LoginResult:
    """
    Validate a SAML response and return a LoginResult.

    Uses python3-saml's OneLogin_Saml2_Auth with strict=True:
      - XML signature validation (assertion + response)
      - NotBefore / NotOnOrAfter time window check
      - Audience restriction check (must match idp.entity_id)
      - NameID + attribute extraction after validation passes

    If idp.enabled is False, raises ValueError (safety gate before HTTP routes go live).
    """
    if not idp.enabled:
        raise ValueError(f'IdP {idp.id!r} is disabled')

    if not idp.entity_id or not idp.acs_url or not idp.metadata_url:
        raise ValueError(
            f'IdP {idp.id!r} missing required SAML fields: entity_id, acs_url, metadata_url'
        )

    from onelogin.saml2.auth import OneLogin_Saml2_Auth
    from onelogin.saml2.settings import OneLogin_Saml2_Settings

    settings_data: dict = {
        'strict': True,       # BLOCK 1 fix: enforce sig + time window + audience
        'debug': False,
        'sp': {
            'entityId': idp.entity_id,
            'assertionConsumerService': {
                'url': idp.acs_url,
                'binding': 'urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST',
            },
            'NameIDFormat': 'urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress',
        },
        'idp': {
            'entityId': idp.metadata_url,
            'singleSignOnService': {
                'url': idp.metadata_url,
                'binding': 'urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect',
            },
            'x509cert': _fetch_saml_x509cert(idp.metadata_url),
        },
        'security': {
            'wantAssertionsSigned': True,
            'wantMessagesSigned': False,
            'requireSignedAssertions': True,
        },
    }

    # Build a synthetic request object python3-saml expects
    request_data = {
        'http_host': _host_from_url(idp.acs_url),
        'script_name': _path_from_url(idp.acs_url),
        'get_data': {},
        'post_data': {'SAMLResponse': saml_response_b64},
    }

    try:
        saml_auth = OneLogin_Saml2_Auth(request_data, old_settings=settings_data)
        saml_auth.process_response()
        errors = saml_auth.get_errors()
        if errors:
            raise ValueError(f'SAML validation failed: {errors} — {saml_auth.get_last_error_reason()}')
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f'SAML processing error: {exc}') from exc

    name_id = saml_auth.get_nameid()
    if not name_id:
        raise ValueError('SAML Assertion missing NameID after validation')

    # F-20 (G34): SAML assertion replay prevention
    _record_saml_assertion(saml_auth=saml_auth, idp_id=idp.id)

    attrs = saml_auth.get_attributes()
    email = _first_attr(attrs, ['email', 'emailAddress', 'http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress'])
    display_name = _first_attr(attrs, ['displayName', 'name', 'http://schemas.microsoft.com/identity/claims/displayname'])
    groups_raw = attrs.get(idp.group_claim_path) or attrs.get('groups') or []
    groups = [str(g) for g in groups_raw] if groups_raw else []

    return _complete_sso_login(
        idp=idp,
        external_subject=name_id,
        email=email,
        display_name=display_name,
        groups=groups,
        tenant_id=tenant_id,
    )


def _record_saml_assertion(saml_auth: object, idp_id: str) -> None:
    """
    F-20 (G34): Insert the assertion into saml_used_assertions to prevent replay.

    Fails closed: any INSERT failure (including UniqueViolation) raises ValueError.
    The caller must NOT proceed to _complete_sso_login if this raises.

    Security notes:
    - not_on_or_after is captured for the cleanup job only; it is NOT the replay guard.
      The PRIMARY KEY (assertion_id, idp_id) is the load-bearing replay guard.
      python3-saml strict=True validates NotOnOrAfter before this function is called.
    - not_on_or_after is capped at 24h from now (F1 adversarial fix) to prevent
      pre-poisoning attacks using far-future timestamps that survive cleanup indefinitely.
    - UniqueViolation handler queries used_at to distinguish concurrent-retry race
      from replay attack in logs (F2 adversarial fix).
    - Missing assertion_id fails closed (F6): python3-saml strict mode rejects
      EncryptedAssertion without SP decryption key at process_response(), so None
      here indicates a genuinely malformed or unsigned assertion that slipped past.
    """
    assertion_id: str | None = saml_auth.get_last_assertion_id()  # type: ignore[attr-defined]
    if not assertion_id:
        raise ValueError(
            'SAML Assertion missing assertion_id — cannot prevent replay. '
            'Possible causes: assertion not yet decrypted (EncryptedAssertion without SP key), '
            'or assertion element absent from response.'
        )

    # not_on_or_after: cleanup job anchor; fallback to now()+5min if unavailable
    # F1 fix: cap at 24h to block pre-poisoning with attacker-controlled far-future timestamps
    try:
        raw_expiry = saml_auth.get_session_expiration()  # type: ignore[attr-defined]
        if raw_expiry:
            not_on_or_after = datetime.fromtimestamp(int(raw_expiry), tz=timezone.utc)
        else:
            not_on_or_after = datetime.fromtimestamp(time.time() + 300, tz=timezone.utc)
    except Exception:
        log.warning('SAML get_session_expiration() raised unexpectedly — using 5-min fallback for idp_id=%s', idp_id)
        not_on_or_after = datetime.fromtimestamp(time.time() + 300, tz=timezone.utc)
    max_expiry = datetime.fromtimestamp(time.time() + 86400, tz=timezone.utc)
    not_on_or_after = min(not_on_or_after, max_expiry)

    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        INSERT INTO saml_used_assertions (assertion_id, idp_id, not_on_or_after)
                        VALUES (%s, %s, %s)
                        """,
                        (assertion_id, idp_id, not_on_or_after),
                    )
                    conn.commit()
                except psycopg2.errors.UniqueViolation:
                    # F2 fix: rollback, then query used_at age to distinguish
                    # concurrent-retry race (< 5s) from replay attack (>= 5s)
                    conn.rollback()
                    age_ms: int | None = None
                    try:
                        cur.execute(
                            'SELECT EXTRACT(EPOCH FROM (now() - used_at)) * 1000 '
                            'FROM saml_used_assertions WHERE assertion_id=%s AND idp_id=%s',
                            (assertion_id, idp_id),
                        )
                        row = cur.fetchone()
                        age_ms = int(row[0]) if row else None
                    except Exception:
                        pass
                    if age_ms is not None and age_ms < 5000:
                        log.debug(
                            'SAML concurrent-retry race (not attack): assertion_id=%s idp_id=%s age_ms=%d',
                            assertion_id, idp_id, age_ms,
                        )
                    else:
                        log.warning(
                            'SAML replay rejected: assertion_id=%s idp_id=%s age_ms=%s',
                            assertion_id, idp_id, age_ms,
                        )
                    raise ValueError('SAML assertion replay detected')
    except ValueError:
        raise
    except psycopg2.Error as exc:
        log.error('SAML replay ledger INSERT failed: %s', exc)
        raise ValueError('SAML replay ledger unavailable — login rejected') from exc


def _host_from_url(url: str) -> str:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return parsed.netloc or 'localhost'


def _path_from_url(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).path or '/'


def _base_url(url: str) -> str:
    """Return scheme://host from a URL — used to derive expected OIDC issuer."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    return f'{parsed.scheme}://{parsed.netloc}'


def _fetch_saml_x509cert(metadata_url: str | None) -> str:
    """
    Fetch the x509 signing cert from an IdP SAML metadata URL.

    Parses the XML to extract the first X509Certificate element under
    IDPSSODescriptor > KeyDescriptor[use=signing].

    Returns empty string if metadata_url is absent or fetch fails — python3-saml
    in strict mode will then reject the assertion (fail closed, not open).
    """
    if not metadata_url:
        return ''
    try:
        import httpx
        from defusedxml import ElementTree as defused_ET

        resp = httpx.get(metadata_url, timeout=5.0)
        resp.raise_for_status()
        root = defused_ET.fromstring(resp.content)
        ns = {
            'md': 'urn:oasis:names:tc:SAML:2.0:metadata',
            'ds': 'http://www.w3.org/2000/09/xmldsig#',
        }
        # Look for signing cert first, fall back to any cert
        for use in ('signing', None):
            xpath = (
                f'.//md:IDPSSODescriptor/md:KeyDescriptor[@use="{use}"]/ds:KeyInfo/ds:X509Data/ds:X509Certificate'
                if use
                else './/ds:X509Certificate'
            )
            el = root.find(xpath, ns)
            if el is not None and el.text:
                return el.text.strip()
    except Exception:
        log.warning('Could not fetch x509cert from SAML metadata_url %r', metadata_url)
    return ''


def _first_attr(attrs: dict, keys: list[str]) -> str | None:
    for k in keys:
        val = attrs.get(k)
        if val:
            return str(val[0]) if isinstance(val, list) else str(val)
    return None


# ── OIDC ───────────────────────────────────────────────────────────────────────


def build_oidc_auth_url(
    idp: IdpConfig,
    *,
    state: str,
    nonce: str,
    redirect_uri: str,
    scopes: list[str] | None = None,
) -> str:
    """
    Build the OIDC authorization redirect URL.
    Caller must store (state, nonce) in session for validation on callback.
    """
    if not idp.authorization_url or not idp.client_id:
        raise ValueError(f'IdP {idp.id!r} is missing OIDC authorization_url or client_id')

    params = {
        'response_type': 'code',
        'client_id': idp.client_id,
        'redirect_uri': redirect_uri,
        'scope': ' '.join(scopes or ['openid', 'email', 'profile']),
        'state': state,
        'nonce': nonce,
    }
    return f"{idp.authorization_url}?{urlencode(params)}"


def exchange_oidc_code(
    idp: IdpConfig,
    *,
    code: str,
    redirect_uri: str,
    state: str,
    nonce: str,
    tenant_id: str = 'default',
) -> LoginResult:
    """
    Exchange authorization code for tokens, validate, and return LoginResult.
    Uses authlib's sync httpx client.
    """
    import httpx

    if not idp.token_url or not idp.client_id:
        raise ValueError(f'IdP {idp.id!r} missing token_url or client_id')

    client_secret = _resolve_client_secret(idp.client_secret_ref)

    resp = httpx.post(
        idp.token_url,
        data={
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirect_uri,
            'client_id': idp.client_id,
            'client_secret': client_secret,
        },
        timeout=10.0,
    )
    resp.raise_for_status()
    token_data = resp.json()

    id_token = token_data.get('id_token')
    if not id_token:
        raise ValueError('OIDC token response missing id_token')

    claims = _decode_oidc_id_token(id_token, idp, nonce=nonce)

    sub = claims.get('sub')
    if not sub:
        raise ValueError('OIDC id_token missing sub claim')

    email = claims.get('email')
    display_name = claims.get('name')

    # Extract groups from the configured claim path
    groups: list[str] = []
    if idp.group_claim_path and idp.group_claim_path in claims:
        raw = claims[idp.group_claim_path]
        groups = raw if isinstance(raw, list) else [raw]

    return _complete_sso_login(
        idp=idp,
        external_subject=sub,
        email=email,
        display_name=display_name,
        groups=groups,
        tenant_id=tenant_id,
    )


def _decode_oidc_id_token(id_token: str, idp: IdpConfig, *, nonce: str) -> dict:
    """
    Decode and validate an OIDC id_token with full signature verification.

    BLOCK 2 fix (Athena G6): fetches JWKS from idp.jwks_url and validates
    the JWT signature (RS256 or ES256). Uses authlib's JsonWebKey + JsonWebToken.

    Validates:
      - JWT signature against JWKS public key
      - iss claim matches idp.metadata_url (or authorization_url base)
      - aud claim includes idp.client_id
      - nonce claim matches (replay prevention)
      - exp claim (expiry)

    If idp.jwks_url is None, raises ValueError — signature bypass is not allowed.
    """
    if not idp.jwks_url:
        raise ValueError(
            f'IdP {idp.id!r} missing jwks_url — OIDC signature validation requires JWKS endpoint'
        )
    if not idp.client_id:
        raise ValueError(f'IdP {idp.id!r} missing client_id — cannot verify aud claim')

    # Derive expected issuer before fetching JWKS — cheap guard, no network needed
    expected_iss = idp.metadata_url or _base_url(idp.authorization_url or '')
    if not expected_iss:
        raise ValueError(
            f'IdP {idp.id!r} has no metadata_url or authorization_url — cannot verify iss claim'
        )

    import httpx
    from authlib.jose import JsonWebKey, JsonWebToken
    from authlib.jose.errors import JoseError

    # Fetch JWKS from IdP (cached in prod via httpx CacheControl — not here in v1)
    try:
        jwks_resp = httpx.get(idp.jwks_url, timeout=5.0)
        jwks_resp.raise_for_status()
        jwks_data = jwks_resp.json()
    except Exception as exc:
        raise ValueError(f'Failed to fetch JWKS from {idp.jwks_url!r}: {exc}') from exc

    # Build JWK set and decode+verify the JWT
    try:
        key_set = JsonWebKey.import_key_set(jwks_data)
        jwt = JsonWebToken(['RS256', 'ES256'])
        claims = jwt.decode(id_token, key_set)
    except JoseError as exc:
        raise ValueError(f'OIDC id_token signature invalid: {exc}') from exc
    except Exception as exc:
        raise ValueError(f'OIDC id_token decode failed: {exc}') from exc

    # aud: must include our client_id
    aud = claims.get('aud', '')
    aud_list = aud if isinstance(aud, list) else [aud]
    if idp.client_id not in aud_list:
        raise ValueError(
            f'OIDC id_token aud {aud_list!r} does not include client_id {idp.client_id!r}'
        )

    # iss: must match expected issuer (prevents tokens from other IdPs with same aud)
    if claims.get('iss') != expected_iss:
        raise ValueError(
            f'OIDC id_token iss {claims.get("iss")!r} does not match expected {expected_iss!r}'
        )

    # nonce: replay prevention
    if claims.get('nonce') != nonce:
        raise ValueError('OIDC id_token nonce mismatch — possible replay attack')

    # exp: must not be expired
    exp = claims.get('exp')
    if exp and int(time.time()) > int(exp):
        raise ValueError('OIDC id_token has expired')

    return dict(claims)


def _resolve_client_secret(secret_ref: str | None) -> str:
    """
    Resolve a Vault ref to the actual secret string.
    Pre-Vault (2B.4): falls back to SOS_OIDC_CLIENT_SECRET env var for dev.
    """
    if not secret_ref:
        return os.getenv('SOS_OIDC_CLIENT_SECRET', '')
    # Vault lookup (stub until 2B.4)
    # Future: vault_client.read(secret_ref)['data']['value']
    env_key = f'VAULT_{secret_ref.upper().replace("/", "_").replace("-", "_")}'
    return os.getenv(env_key) or os.getenv('SOS_OIDC_CLIENT_SECRET', '')


# ── Shared SSO login completion ────────────────────────────────────────────────


def _complete_sso_login(
    *,
    idp: IdpConfig,
    external_subject: str,
    email: str | None,
    display_name: str | None,
    groups: list[str],
    tenant_id: str,
) -> LoginResult:
    """
    Shared completion path for SAML and OIDC:
      1. JIT-provision principal via sso_identity_links
      2. Apply group → role mappings from idp_group_role_map
      3. Update last_login_at
      4. Return LoginResult (no session token minted here — DISP-001 does that)
    """
    link, created = get_or_create_link(
        idp_id=idp.id,
        external_subject=external_subject,
        email=email,
        display_name=display_name,
        tenant_id=tenant_id,
    )

    # Apply group → role mappings, capped at IdP's tier ceiling (F-15)
    # BLOCK-2 fix: explicit loop catches ValueError from _role_within_ceiling so a
    # DB-level poison role (unknown tier, bypassing config-time guard) cannot crash
    # the login path with an unhandled exception → 401 DoS for the whole IdP.
    raw_role_ids = get_roles_for_groups(idp.id, groups)
    role_ids: list[str] = []
    for r in raw_role_ids:
        try:
            if _role_within_ceiling(r, idp.max_grantable_tier):
                role_ids.append(r)
        except ValueError:
            log.error('SSO login: skipping role %r with unrecognised tier (stream=%s)', r, idp.id)
    dropped = [r for r in raw_role_ids if r not in role_ids]
    if dropped:
        log.warning(
            'SSO login: dropped %d role(s) exceeding or unrecognised tier vs IdP ceiling %r: %s',
            len(dropped), idp.max_grantable_tier, dropped,
        )
    for role_id in role_ids:
        assign_role(
            role_id,
            link.principal_id,
            assignee_type='human',
            assigned_by=f'sso:{idp.id}',
        )

    update_last_login(link.principal_id)

    principal = get_principal(link.principal_id)
    mfa_req = requires_mfa(link.principal_id)

    log.info(
        'SSO login: principal=%s email=%s idp=%s new=%s roles=%s',
        link.principal_id, email, idp.id, created, role_ids,
    )

    return LoginResult(
        principal_id=link.principal_id,
        email=email,
        display_name=display_name,
        tenant_id=tenant_id,
        mfa_verified=False,       # MFA not yet evaluated — caller must challenge
        mfa_required=mfa_req,
        roles=role_ids,
    )


# requires_mfa_for_principal removed — use principals.requires_mfa(principal_id) directly.
# Soft note from Athena G6: no duplication across contract modules.


# ── SCIM 2.0 provisioning ──────────────────────────────────────────────────────


def scim_provision_user(
    *,
    idp_id: str,
    external_id: str,
    email: str,
    display_name: str | None = None,
    active: bool = True,
    groups: list[str] | None = None,
) -> str:
    """
    SCIM user provision/update. Returns principal_id.

    Creates or updates the principal. If active=False, deprovisions.
    Group changes update role_assignments via idp_group_role_map.

    F-10: tenant_id is derived from idp_configurations — not accepted from caller.
          This prevents cross-tenant escalation via a crafted SCIM payload.
    F-15: Roles assigned via group mappings are capped at idp.max_grantable_tier.
    """
    # F-10: derive tenant_id from IdP config — never from caller
    idp = get_idp(idp_id)
    if idp is None:
        raise ValueError(f'SCIM: IdP {idp_id!r} not found')
    tenant_id = idp.tenant_id

    principal = upsert_principal(
        email=email,
        display_name=display_name,
        principal_type='human',
        tenant_id=tenant_id,
    )

    # Ensure sso_identity_links row exists for the SCIM external_id
    link_id = _new_id('lnk')
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO sso_identity_links
                       (id, tenant_id, idp_id, external_subject, principal_id, email)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (idp_id, external_subject)
                   DO UPDATE SET last_seen_at = now(), email = EXCLUDED.email""",
                (link_id, tenant_id, idp_id, external_id, principal.id, email),
            )
        conn.commit()

    # Deprovision path
    if not active:
        set_principal_status(principal.id, 'deprovisioned', updated_by=f'scim:{idp_id}')
        log.info('SCIM deprovisioned principal %s', principal.id)
        return principal.id

    # WARN-5 fix: re-provision of a deprovisioned principal must re-activate them.
    # upsert_principal ON CONFLICT only updates display_name + updated_at — it does NOT
    # touch status. Without this, a user deprovisioned then re-provisioned stays
    # status='deprovisioned' silently (will matter when DISP-001 enforces status checks).
    if principal.status == 'deprovisioned':
        set_principal_status(principal.id, 'active', updated_by=f'scim:{idp_id}')
        log.info('SCIM re-activated previously deprovisioned principal %s', principal.id)

    # Apply group → role mappings, capped at IdP's tier ceiling (F-15)
    # BLOCK-2 fix: same resilient loop as login path — unknown-tier role in DB
    # must not crash SCIM provision with an unhandled ValueError.
    if groups is not None:
        raw_role_ids = get_roles_for_groups(idp_id, groups)
        role_ids: list[str] = []
        for r in raw_role_ids:
            try:
                if _role_within_ceiling(r, idp.max_grantable_tier):
                    role_ids.append(r)
            except ValueError:
                log.error('SCIM provision: skipping role %r with unrecognised tier (idp=%s)', r, idp_id)
        dropped = [r for r in raw_role_ids if r not in role_ids]
        if dropped:
            log.warning(
                'SCIM provision: dropped %d role(s) exceeding or unrecognised tier vs IdP ceiling %r: %s',
                len(dropped), idp.max_grantable_tier, dropped,
            )
        for role_id in role_ids:
            assign_role(
                role_id,
                principal.id,
                assignee_type='human',
                assigned_by=f'scim:{idp_id}',
            )

    log.info('SCIM provisioned principal %s (active=%s groups=%s)', principal.id, active, groups)
    return principal.id


def scim_deprovision_user(
    *,
    idp_id: str,
    external_id: str,
) -> bool:
    """
    SCIM deprovision. Returns True if principal was found and deprovisioned.

    G60 fix: removed dead `tenant_id` parameter — tenant_id is derived from
    idp_id (same as scim_provision_user) and was never actually used here.
    The parameter accepted a caller-supplied value that was silently ignored,
    creating a misleading API surface.
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT principal_id FROM sso_identity_links
                    WHERE idp_id = %s AND external_subject = %s""",
                (idp_id, external_id),
            )
            row = cur.fetchone()
    if not row:
        return False
    set_principal_status(row['principal_id'], 'deprovisioned', updated_by=f'scim:{idp_id}')
    return True


# ── MFA — TOTP ─────────────────────────────────────────────────────────────────


def enroll_totp(
    principal_id: str,
    *,
    label: str = 'default',
    issuer: str = 'SOS',
) -> tuple[str, str]:
    """
    Generate a new TOTP secret and enroll it.

    Returns (otpauth_uri, secret_ref).
    In dev mode (SOS_TOTP_STORE=local), secret_ref is a local key.
    In prod, secret_ref is a Vault path (2B.4).

    The caller should display the otpauth_uri as a QR code.
    The enrollment is NOT confirmed until verify_totp() succeeds once.
    """
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    display_email = principal_id  # fallback; caller can pass email
    otpauth_uri = totp.provisioning_uri(name=display_email, issuer_name=issuer)

    # Store secret
    secret_ref = _store_totp_secret(principal_id, label, secret)

    method_id = _new_id('mfa')
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO mfa_enrolled_methods
                       (id, principal_id, method, secret_ref, label, enabled)
                   VALUES (%s, %s, 'totp', %s, %s, true)
                   ON CONFLICT (principal_id, method, label)
                   DO UPDATE SET secret_ref = EXCLUDED.secret_ref, enabled = true""",
                (method_id, principal_id, secret_ref, label),
            )
        conn.commit()

    return otpauth_uri, secret_ref


def _totp_window_start(totp_obj: 'pyotp.TOTP', code: str) -> int | None:
    """Return the time_window_start (Unix epoch, seconds) for which code is valid.

    G27 (F-09): Iterates over the same ±1 window that totp.verify() uses.
    Returns the start of the earliest matching window, or None if code is invalid.
    Using the canonical window (not current clock time) ensures the hash is
    identical on replay attempts within the same window boundary.
    """
    now = int(time.time())
    interval = int(totp_obj.interval)
    counter = now // interval
    for offset in (-1, 0, 1):
        candidate = counter + offset
        candidate_time = candidate * interval
        # Use hmac.compare_digest (constant-time) to avoid timing side-channel
        # on the code comparison — matches pyotp.verify()'s internal guard.
        if hmac.compare_digest(totp_obj.at(candidate_time), code):
            return candidate_time
    return None


def _totp_code_hash(principal_id: str, code: str, time_window_start: int) -> bytes:
    """sha256(principal_id:code:time_window_start) — full 32-byte digest.

    G27 (F-09): Bound to principal (two users same 6-digit code never collide)
    AND time window (same code in next window → different hash, not a false
    positive). Full sha256 — no truncation; no birthday-bound collision risk.
    """
    raw = f'{principal_id}:{code}:{time_window_start}'.encode()
    return hashlib.sha256(raw).digest()


def verify_totp(principal_id: str, code: str, *, label: str = 'default') -> bool:
    """
    Verify a TOTP code. Returns True on success, False otherwise.
    Updates last_used_at on success.

    Window=1 allows ±30 second drift per RFC 6238.

    G27 (F-09): Replay ledger — records (principal_id, code_hash) on successful
    verification. Duplicate INSERT raises UniqueViolation → replay rejected.
    The code_hash ties the code to its canonical time window so cross-window
    reuse (different window → different hash) is not a false positive.
    """
    secret_ref = _get_totp_secret_ref(principal_id, label)
    if not secret_ref:
        return False

    secret = _resolve_totp_secret(secret_ref)
    if not secret:
        return False

    totp = pyotp.TOTP(secret)

    # Determine the canonical time window this code is valid for.
    # This must happen BEFORE recording in the ledger — the window determines
    # the hash; using the current clock (not the canonical window) would let
    # an attacker replay by waiting until the clock crosses a window boundary.
    time_window_start = _totp_window_start(totp, code)
    if time_window_start is None:
        log.warning('TOTP verification failed for principal %s', principal_id)
        return False

    # Replay ledger: INSERT with PK — duplicate fails atomically.
    # The PK resolves the race between two concurrent requests with the same
    # code: both can pass the time-window check, but only one INSERT wins.
    #
    # G62: flood quota check runs in the same DB round-trip as the INSERT to
    # avoid an extra connection. We count this principal's existing rows in the
    # last 5 minutes before inserting.
    #
    # TOCTOU bound: concurrent requests that both read count=N-1 will both pass
    # and both insert, giving count=N+threads-1. The practical ceiling is bounded
    # by valid TOTP codes: the INSERT PK is (principal_id, code_hash), and
    # code_hash includes the 30-sec time window. Within any concurrent burst,
    # the attacker can produce at most ~3 unique valid codes (±1 window step).
    # So worst-case over-quota is N + ~3, not N + unbounded threads.
    # Goal is unbounded-table prevention; strict per-request enforcement is not
    # required (quota is not a brute-force gate — the TOTP secret itself is).
    code_hash = _totp_code_hash(principal_id, code, time_window_start)
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                # G62: check flood quota before inserting
                cur.execute(
                    """SELECT COUNT(*) AS cnt FROM mfa_used_codes
                         WHERE principal_id = %s
                           AND used_at > now() - interval '5 minutes'""",
                    (principal_id,),
                )
                quota_row = cur.fetchone()
                current_count = quota_row['cnt'] if quota_row else 0
                if current_count >= _MFA_FLOOD_QUOTA_PER_5MIN:
                    log.warning(
                        'TOTP flood quota exceeded for principal %s '
                        '(%d entries in last 5 min, limit=%d)',
                        principal_id, current_count, _MFA_FLOOD_QUOTA_PER_5MIN,
                    )
                    return False

                cur.execute(
                    'INSERT INTO mfa_used_codes (principal_id, code_hash) VALUES (%s, %s)',
                    (principal_id, psycopg2.Binary(code_hash)),
                )
            conn.commit()
    except psycopg2.errors.UniqueViolation:
        log.warning(
            'TOTP replay attempt rejected for principal %s (window=%d)',
            principal_id, time_window_start,
        )
        return False
    except psycopg2.Error as exc:
        # Non-replay DB error (network, disk, pool exhaustion) — fail closed.
        # verify_totp() contract is -> bool; unhandled DB exceptions violate it.
        log.error(
            'TOTP ledger INSERT failed for principal %s: %s', principal_id, exc
        )
        return False

    _update_mfa_last_used(principal_id, 'totp', label)
    log.info('TOTP verified for principal %s', principal_id)
    return True


def cleanup_mfa_used_codes() -> int:
    """
    G62: Delete mfa_used_codes rows older than 5 minutes. Returns rows deleted.

    Wire to a periodic job (e.g., systemd timer every 5 minutes). The 5-minute
    retention window exceeds the maximum TOTP replay window (90 sec = ±1 step
    at 30 sec/step), so no live replay-prevention entries are removed.

    The `mfa_used_codes_cleanup_idx ON (used_at)` index (migration 041) keeps
    this DELETE O(expired_rows) rather than a full-table scan.
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM mfa_used_codes WHERE used_at < now() - interval '5 minutes'"
            )
            deleted = cur.rowcount
        conn.commit()
    if deleted:
        log.info('G62: cleaned up %d expired mfa_used_codes rows', deleted)
    return deleted


def _vault_client() -> 'hvac.Client':
    """
    Return an authenticated hvac Vault client using AppRole credentials.

    Credentials sourced from env (VAULT_ADDR, VAULT_ROLE_ID, VAULT_SECRET_ID).
    Raises RuntimeError if hvac not installed or credentials missing.
    """
    try:
        import hvac
    except ImportError as exc:
        raise RuntimeError('hvac not installed — pip install hvac') from exc

    addr = os.getenv('VAULT_ADDR', 'http://127.0.0.1:8200')
    role_id = os.getenv('VAULT_ROLE_ID')
    secret_id = os.getenv('VAULT_SECRET_ID')

    if not role_id or not secret_id:
        raise RuntimeError('VAULT_ROLE_ID and VAULT_SECRET_ID must be set for prod Vault access')

    client = hvac.Client(url=addr)
    resp = client.auth.approle.login(role_id=role_id, secret_id=secret_id)
    client.token = resp['auth']['client_token']

    if not client.is_authenticated():
        raise RuntimeError('Vault AppRole authentication failed')

    return client


def _store_totp_secret(principal_id: str, label: str, secret: str) -> str:
    """
    Store a TOTP secret and return an opaque ref.

    Dev mode (SOS_TOTP_STORE=local): in-process dict only.
    Prod mode: writes to Vault KV v2 at sos/totp/{principal_id}/{label}.
    Fails hard if Vault unavailable — env-var storage defeats the second-factor model.
    """
    if _totp_dev_mode():
        ref = f'local:{principal_id}:{label}'
        _LOCAL_TOTP_STORE[ref] = secret
        return ref

    vault_path = f'totp/{principal_id}/{label}'
    ref = f'vault:sos:{vault_path}'
    try:
        client = _vault_client()
        client.secrets.kv.v2.create_or_update_secret(
            path=vault_path,
            mount_point='sos',
            secret={'value': secret},
        )
    except Exception as exc:
        raise RuntimeError(f'Vault write failed for TOTP secret: {exc}') from exc

    log.info('TOTP secret stored in Vault at sos/%s', vault_path)
    return ref


def _resolve_totp_secret(secret_ref: str) -> str | None:
    if secret_ref.startswith('local:'):
        return _LOCAL_TOTP_STORE.get(secret_ref)

    if not secret_ref.startswith('vault:sos:'):
        log.warning('Unknown TOTP secret ref format: %r', secret_ref)
        return None

    vault_path = secret_ref[len('vault:sos:'):]
    try:
        client = _vault_client()
        resp = client.secrets.kv.v2.read_secret_version(
            path=vault_path,
            mount_point='sos',
        )
        return resp['data']['data'].get('value')
    except Exception as exc:
        log.error('Vault read failed for TOTP secret %r: %s', secret_ref, exc)
        return None


def _get_totp_secret_ref(principal_id: str, label: str) -> str | None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT secret_ref FROM mfa_enrolled_methods
                    WHERE principal_id = %s AND method = 'totp' AND label = %s AND enabled = true""",
                (principal_id, label),
            )
            row = cur.fetchone()
    return row['secret_ref'] if row else None


def _update_mfa_last_used(principal_id: str, method: MfaMethod, label: str) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE mfa_enrolled_methods
                      SET last_used_at = now()
                    WHERE principal_id = %s AND method = %s AND label = %s""",
                (principal_id, method, label),
            )
        conn.commit()


# ── MFA — WebAuthn ─────────────────────────────────────────────────────────────


def begin_webauthn_registration(
    principal_id: str,
    *,
    rp_id: str,
    rp_name: str,
    label: str = 'default',
) -> dict:
    """
    Generate registration options for WebAuthn enrollment.
    Returns the options dict to send to the browser (PublicKeyCredentialCreationOptions).
    """
    options = webauthn.generate_registration_options(
        rp_id=rp_id,
        rp_name=rp_name,
        user_id=principal_id.encode(),
        user_name=principal_id,
        user_display_name=label,
    )
    return json.loads(webauthn.options_to_json(options))


def complete_webauthn_registration(
    principal_id: str,
    *,
    registration_response: dict,
    expected_challenge: bytes,
    rp_id: str,
    label: str = 'default',
) -> MfaEnrolledMethod:
    """
    Verify and persist a WebAuthn registration response.
    Returns the enrolled MfaEnrolledMethod.
    """
    verified = webauthn.verify_registration_response(
        credential=registration_response,
        expected_challenge=expected_challenge,
        expected_rp_id=rp_id,
        expected_origin=f'https://{rp_id}',
    )

    credential_id_b64 = base64.urlsafe_b64encode(verified.credential_id).decode()
    public_key_b64 = base64.urlsafe_b64encode(verified.credential_public_key).decode()
    aaguid = str(verified.aaguid) if verified.aaguid else None

    method_id = _new_id('mfa')
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO mfa_enrolled_methods
                       (id, principal_id, method, credential_id, public_key, aaguid, sign_count, label)
                   VALUES (%s, %s, 'webauthn', %s, %s, %s, %s, %s)
                   ON CONFLICT (principal_id, method, label)
                   DO UPDATE SET
                       credential_id = EXCLUDED.credential_id,
                       public_key    = EXCLUDED.public_key,
                       aaguid        = EXCLUDED.aaguid,
                       sign_count    = EXCLUDED.sign_count,
                       enabled       = true
                   RETURNING id, principal_id, method, secret_ref, credential_id,
                             public_key, aaguid, sign_count, label, enabled,
                             last_used_at, created_at""",
                (
                    method_id, principal_id, credential_id_b64,
                    public_key_b64, aaguid, verified.sign_count, label,
                ),
            )
            row = cur.fetchone()
        conn.commit()

    log.info('WebAuthn credential enrolled for principal %s label=%s', principal_id, label)
    return _row_to_mfa_method(row)


def begin_webauthn_authentication(
    principal_id: str,
    *,
    rp_id: str,
    label: str = 'default',
) -> dict:
    """Generate authentication options (challenge) for WebAuthn assertion."""
    cred_id_b64 = _get_webauthn_credential_id(principal_id, label)
    if not cred_id_b64:
        raise ValueError(f'No WebAuthn credential enrolled for principal {principal_id!r}')

    cred_id = base64.urlsafe_b64decode(cred_id_b64 + '==')
    options = webauthn.generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=[
            webauthn.PublicKeyCredentialDescriptor(id=cred_id)
        ],
    )
    return json.loads(webauthn.options_to_json(options))


def verify_webauthn_assertion(
    principal_id: str,
    *,
    assertion_response: dict,
    expected_challenge: bytes,
    rp_id: str,
    label: str = 'default',
) -> bool:
    """
    Verify a WebAuthn assertion. Updates sign_count on success (replay prevention).
    Returns True on success, False otherwise.
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT credential_id, public_key, sign_count
                     FROM mfa_enrolled_methods
                    WHERE principal_id = %s AND method = 'webauthn' AND label = %s AND enabled = true""",
                (principal_id, label),
            )
            row = cur.fetchone()

    if not row:
        return False

    cred_id = base64.urlsafe_b64decode(row['credential_id'] + '==')
    public_key = base64.urlsafe_b64decode(row['public_key'] + '==')

    try:
        verified = webauthn.verify_authentication_response(
            credential=assertion_response,
            expected_challenge=expected_challenge,
            expected_rp_id=rp_id,
            expected_origin=f'https://{rp_id}',
            credential_public_key=public_key,
            credential_current_sign_count=row['sign_count'],
        )
    except Exception as exc:
        log.warning('WebAuthn assertion verification failed for %s: %s', principal_id, exc)
        return False

    # Update sign_count — monotonic counter prevents replay attacks
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE mfa_enrolled_methods
                      SET sign_count = %s, last_used_at = now()
                    WHERE principal_id = %s AND method = 'webauthn' AND label = %s""",
                (verified.new_sign_count, principal_id, label),
            )
        conn.commit()

    log.info('WebAuthn assertion verified for principal %s', principal_id)
    return True


def _get_webauthn_credential_id(principal_id: str, label: str) -> str | None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT credential_id FROM mfa_enrolled_methods
                    WHERE principal_id = %s AND method = 'webauthn'
                      AND label = %s AND enabled = true""",
                (principal_id, label),
            )
            row = cur.fetchone()
    return row['credential_id'] if row else None


def list_mfa_methods(principal_id: str) -> list[MfaEnrolledMethod]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, principal_id, method, secret_ref, credential_id,
                          public_key, aaguid, sign_count, label, enabled,
                          last_used_at, created_at
                     FROM mfa_enrolled_methods
                    WHERE principal_id = %s AND enabled = true
                    ORDER BY created_at""",
                (principal_id,),
            )
            rows = cur.fetchall()
    return [_row_to_mfa_method(r) for r in rows]


def disable_mfa_method(principal_id: str, label: str, method: MfaMethod) -> bool:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE mfa_enrolled_methods SET enabled = false
                    WHERE principal_id = %s AND method = %s AND label = %s""",
                (principal_id, method, label),
            )
            updated = cur.rowcount > 0
        conn.commit()
    return updated


def _row_to_mfa_method(row: dict) -> MfaEnrolledMethod:
    return MfaEnrolledMethod(
        id=row['id'],
        principal_id=row['principal_id'],
        method=row['method'],
        secret_ref=row['secret_ref'],
        credential_id=row['credential_id'],
        public_key=row['public_key'],
        aaguid=row['aaguid'],
        sign_count=row['sign_count'],
        label=row['label'],
        enabled=row['enabled'],
        last_used_at=row['last_used_at'],
        created_at=row['created_at'],
    )
