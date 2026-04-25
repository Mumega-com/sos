"""
§2B.4 Per-workspace DEK envelope encryption contract.

Gate: Athena G7

Design:
  - Each workspace (tenant) has one DEK (Data Encryption Key) — AES-256-GCM, 32 bytes.
  - The DEK is encrypted with the workspace's KEK (Key Encryption Key).
  - The KEK lives in Vault KV v2 at sos/dek/{workspace_id}/kek — it never leaves Vault.
  - The encrypted DEK (nonce || AES-GCM ciphertext) lives in workspace_keys table.

Envelope encryption flow:
  Encrypt:
    1. get_dek(workspace_id) → plaintext DEK (in-memory only)
    2. AESGCM(dek).encrypt(nonce, plaintext, aad) → ciphertext
  Decrypt:
    1. get_dek(workspace_id) → plaintext DEK
    2. AESGCM(dek).decrypt(nonce, ciphertext, aad) → plaintext

Audit:
  Every encrypt/decrypt operation emits an audit_events row via the DB
  (action='dek_encrypt' / 'dek_decrypt', resource='workspace:{id}', actor=caller_id).

DB: psycopg2 sync against MIRROR_DATABASE_URL or DATABASE_URL.
Vault: hvac via VAULT_ADDR + VAULT_ROLE_ID + VAULT_SECRET_ID.

Constitutional constraints:
  1. DEKs never leave memory — never written to disk, DB, or logs.
  2. KEK never leaves Vault — we write to Vault, we read from Vault; never in env vars.
  3. Audit on every wrap/unwrap/encrypt/decrypt (Athena G7 acceptance criterion 3).
  4. Cross-workspace isolation: each workspace has its own KEK+DEK pair. Never share.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

log = logging.getLogger(__name__)

_ALGORITHM = 'AES-256-GCM'
_KEY_BYTES = 32   # 256-bit DEK
_NONCE_BYTES = 12  # 96-bit nonce for AES-GCM


# ── DB ─────────────────────────────────────────────────────────────────────────


def _db_url() -> str:
    url = os.getenv('MIRROR_DATABASE_URL') or os.getenv('DATABASE_URL')
    if not url:
        raise RuntimeError('MIRROR_DATABASE_URL or DATABASE_URL not set')
    return url


def _connect():
    return psycopg2.connect(_db_url(), cursor_factory=psycopg2.extras.RealDictCursor)


# ── Vault helpers ──────────────────────────────────────────────────────────────

# G26 (F-08): Per-workspace token cache — isolates cache entries so a 403 on
# workspace A does not evict workspace B's token.
#
# Cache key: workspace_id (str).
# Each entry: {'token', 'addr', 'expires_at', 'request_id'}.
# request_id tags the entry at insertion — eviction on 403 only removes the
# entry if its request_id matches the one that triggered the 403, preventing
# a concurrent fresh login for the same workspace from being evicted.
#
# LRU eviction: when cache exceeds VAULT_CACHE_PER_WORKSPACE entries, the
# least-recently-used workspace entry is evicted. This bounds memory growth
# if many workspaces are accessed.
#
# TODO Sprint 006: add proactive invalidation on workspace suspension.

VAULT_CACHE_PER_WORKSPACE: int = int(os.environ.get('VAULT_CACHE_PER_WORKSPACE', '100'))

_vault_token_cache: OrderedDict[str, dict] = OrderedDict()
_vault_cache_lock = threading.Lock()

_VAULT_TOKEN_TTL = 3500      # seconds — slightly under Vault default 1hr lease
_VAULT_TOKEN_BUFFER = 30     # seconds — refresh this early to avoid edge expiry


def _vault_client(workspace_id: str, *, _skip_cache: bool = False) -> tuple:
    """Return (hvac.Client, request_id) for the given workspace via AppRole.

    G26 (F-08): Token is cached per workspace_id in an LRU OrderedDict
    (max VAULT_CACHE_PER_WORKSPACE entries). Returns the cached entry's
    request_id so callers can call _evict_workspace_token() with the right
    request_id on a 403 — preventing concurrent requests from evicting each
    other's tokens.

    Call with _skip_cache=True to force a fresh login (used after 403 eviction).
    """
    import hvac

    addr = os.getenv('VAULT_ADDR', 'http://127.0.0.1:8200')
    role_id = os.getenv('VAULT_ROLE_ID')
    secret_id = os.getenv('VAULT_SECRET_ID')

    if not role_id or not secret_id:
        raise RuntimeError('VAULT_ROLE_ID and VAULT_SECRET_ID must be set')

    now = time.monotonic()

    if not _skip_cache:
        with _vault_cache_lock:
            cached = _vault_token_cache.get(workspace_id)
            if (
                cached
                and cached.get('addr') == addr
                and cached.get('expires_at', 0) > now + _VAULT_TOKEN_BUFFER
            ):
                # Move to end (LRU — most recently used)
                _vault_token_cache.move_to_end(workspace_id)
                client = hvac.Client(url=addr)
                client.token = cached['token']
                return client, cached['request_id']

    # Fresh AppRole login
    client = hvac.Client(url=addr)
    resp = client.auth.approle.login(role_id=role_id, secret_id=secret_id)
    client.token = resp['auth']['client_token']

    if not client.is_authenticated():
        raise RuntimeError('Vault AppRole authentication failed')

    new_req_id = str(uuid.uuid4())
    entry = {
        'token': client.token,
        'addr': addr,
        'expires_at': now + _VAULT_TOKEN_TTL,
        'request_id': new_req_id,
    }

    with _vault_cache_lock:
        _vault_token_cache[workspace_id] = entry
        _vault_token_cache.move_to_end(workspace_id)
        # LRU eviction — evict oldest entry if over limit
        while len(_vault_token_cache) > VAULT_CACHE_PER_WORKSPACE:
            _vault_token_cache.popitem(last=False)

    return client, new_req_id


def _evict_workspace_token(workspace_id: str, request_id: str) -> None:
    """Evict the cache entry for workspace_id only if it still carries request_id.

    G26 (F-08): A concurrent request may have already refreshed the entry with
    a new request_id. In that case, we do NOT evict — the fresh entry is valid.
    Only evict if this is still the same entry that triggered the 403.
    Logs at WARNING with workspace_id and request_id.
    """
    with _vault_cache_lock:
        entry = _vault_token_cache.get(workspace_id)
        if entry and entry.get('request_id') == request_id:
            del _vault_token_cache[workspace_id]
            log.warning(
                'Vault token cache evicted: workspace_id=%s request_id=%s',
                workspace_id, request_id,
            )


def _kek_vault_path(workspace_id: str) -> str:
    return f'dek/{workspace_id}/kek'


def _read_kek(workspace_id: str) -> tuple[bytes, int]:
    """Fetch KEK bytes and current Vault version from Vault.

    Returns (kek_bytes, version_number).  Version is captured here so
    rotate_workspace_key() can destroy the old version after re-wrapping (B.3).

    G26 (F-08): On 403 Forbidden, evicts only this workspace's cache entry
    (not the global cache) and retries once with a fresh AppRole login.
    Other workspaces' cached tokens are unaffected.
    """
    path = _kek_vault_path(workspace_id)

    try:
        client, req_id = _vault_client(workspace_id)
        try:
            resp = client.secrets.kv.v2.read_secret_version(path=path, mount_point='sos')
        except Exception as exc:
            # Detect 403/Forbidden — evict only this workspace's entry and retry once
            if '403' in str(exc) or 'Forbidden' in str(exc):
                log.warning(
                    'Vault 403 on KEK read — evicting workspace token and retrying '
                    '(workspace_id=%s request_id=%s)',
                    workspace_id, req_id,
                )
                _evict_workspace_token(workspace_id, req_id)
                client, _ = _vault_client(workspace_id, _skip_cache=True)
                resp = client.secrets.kv.v2.read_secret_version(path=path, mount_point='sos')
            else:
                raise
        hex_key = resp['data']['data']['value']
        version = resp['data']['metadata']['version']
        return bytes.fromhex(hex_key), version
    except Exception as exc:
        raise RuntimeError(f'KEK read failed for workspace {workspace_id!r}: {exc}') from exc


def _write_kek(workspace_id: str, kek_bytes: bytes) -> str:
    """Write a new KEK to Vault. Returns the Vault path."""
    client, _ = _vault_client(workspace_id)
    path = _kek_vault_path(workspace_id)
    client.secrets.kv.v2.create_or_update_secret(
        path=path,
        mount_point='sos',
        secret={'value': kek_bytes.hex()},
    )
    log.info('KEK written to Vault for workspace %s', workspace_id)
    return f'sos/{path}'


# ── DEK wrap / unwrap ──────────────────────────────────────────────────────────


def _wrap_dek(dek: bytes, kek: bytes) -> bytes:
    """Encrypt DEK with KEK using AES-256-GCM. Returns nonce || ciphertext."""
    nonce = os.urandom(_NONCE_BYTES)
    aesgcm = AESGCM(kek)
    ciphertext = aesgcm.encrypt(nonce, dek, None)
    return nonce + ciphertext


def _unwrap_dek(wrapped: bytes, kek: bytes) -> bytes:
    """Decrypt DEK from nonce || ciphertext using KEK."""
    nonce = wrapped[:_NONCE_BYTES]
    ciphertext = wrapped[_NONCE_BYTES:]
    aesgcm = AESGCM(kek)
    return aesgcm.decrypt(nonce, ciphertext, None)


# ── Audit emission ─────────────────────────────────────────────────────────────


def _emit_dek_audit(
    action: str,
    workspace_id: str,
    caller_id: str = 'system',
) -> None:
    """Emit audit_events row for DEK operations. Non-fatal on failure."""
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO audit_events
                           (stream_id, actor_id, actor_type, action, resource, payload)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (
                        'dek',
                        caller_id,
                        'system',
                        action,
                        f'workspace:{workspace_id}',
                        psycopg2.extras.Json({
                            'workspace_id': workspace_id,
                            'algorithm': _ALGORITHM,
                            'ts': datetime.now(timezone.utc).isoformat(),
                        }),
                    ),
                )
            conn.commit()
    except Exception as exc:
        log.warning('DEK audit emit failed (non-fatal): %s', exc)


# ── Public API ─────────────────────────────────────────────────────────────────


def provision_workspace_key(workspace_id: str, *, caller_id: str = 'system') -> None:
    """
    Generate a new KEK + DEK for a workspace and store them.

    KEK → Vault KV v2 at sos/dek/{workspace_id}/kek
    Encrypted DEK → workspace_keys table

    Idempotent: if the workspace already has a key, raises ValueError.
    Call rotate_workspace_key() for key rotation.
    """
    # Check if already provisioned
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT workspace_id FROM workspace_keys WHERE workspace_id = %s',
                (workspace_id,),
            )
            if cur.fetchone():
                raise ValueError(f'Workspace {workspace_id!r} already has a DEK — use rotate_workspace_key()')

    # Generate KEK and DEK
    kek = os.urandom(_KEY_BYTES)
    dek = os.urandom(_KEY_BYTES)
    kek_path = _write_kek(workspace_id, kek)
    wrapped_dek = _wrap_dek(dek, kek)

    with _connect() as conn:
        with conn.cursor() as cur:
            # B.4: Use ON CONFLICT DO NOTHING RETURNING to close the TOCTOU window.
            # If two concurrent provision calls both pass the pre-check SELECT above,
            # only one INSERT wins; the other gets zero rows back → raise ValueError.
            cur.execute(
                """INSERT INTO workspace_keys
                       (workspace_id, dek_encrypted_with_kek, kek_ref, algorithm)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (workspace_id) DO NOTHING
                   RETURNING workspace_id""",
                (workspace_id, psycopg2.Binary(wrapped_dek), kek_path, _ALGORITHM),
            )
            if cur.fetchone() is None:
                raise ValueError(
                    f'Workspace {workspace_id!r} already provisioned — concurrent race detected'
                )
        conn.commit()

    # Zero KEK + DEK from memory immediately (Python GC doesn't guarantee this,
    # but we clear references to minimize window)
    del kek, dek

    _emit_dek_audit('dek_provision', workspace_id, caller_id)
    log.info('DEK provisioned for workspace %s', workspace_id)


def get_dek(workspace_id: str, *, caller_id: str = 'system') -> bytes:
    """
    Unwrap and return the plaintext DEK for a workspace.

    DEK lives in memory only — caller must zero the returned bytes after use.
    Audit event emitted on every call.
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT dek_encrypted_with_kek FROM workspace_keys WHERE workspace_id = %s',
                (workspace_id,),
            )
            row = cur.fetchone()

    if not row:
        raise ValueError(f'Workspace {workspace_id!r} has no DEK — call provision_workspace_key() first')

    wrapped_dek = bytes(row['dek_encrypted_with_kek'])
    kek, _version = _read_kek(workspace_id)

    try:
        dek = _unwrap_dek(wrapped_dek, kek)
    except Exception as exc:
        raise RuntimeError(f'DEK unwrap failed for workspace {workspace_id!r}: {exc}') from exc
    finally:
        del kek

    _emit_dek_audit('dek_unwrap', workspace_id, caller_id)
    return dek


def encrypt(plaintext: bytes, workspace_id: str, *, caller_id: str = 'system') -> bytes:
    """
    Encrypt plaintext under the workspace's DEK.

    Returns: nonce (12 bytes) || AES-GCM ciphertext
    The returned bytes are safe to store in any column or blob.
    """
    dek = get_dek(workspace_id, caller_id=caller_id)
    try:
        nonce = os.urandom(_NONCE_BYTES)
        aesgcm = AESGCM(dek)
        ciphertext = aesgcm.encrypt(nonce, plaintext, workspace_id.encode())
        result = nonce + ciphertext
    finally:
        del dek

    _emit_dek_audit('dek_encrypt', workspace_id, caller_id)
    return result


def decrypt(ciphertext_blob: bytes, workspace_id: str, *, caller_id: str = 'system') -> bytes:
    """
    Decrypt a blob produced by encrypt().

    Expects: nonce (12 bytes) || AES-GCM ciphertext
    Raises ValueError on authentication failure (tampered data or wrong workspace).
    """
    if len(ciphertext_blob) < _NONCE_BYTES + 16:  # 16 = GCM tag minimum
        raise ValueError('Ciphertext blob too short — expected nonce || GCM ciphertext')

    dek = get_dek(workspace_id, caller_id=caller_id)
    try:
        nonce = ciphertext_blob[:_NONCE_BYTES]
        ciphertext = ciphertext_blob[_NONCE_BYTES:]
        aesgcm = AESGCM(dek)
        plaintext = aesgcm.decrypt(nonce, ciphertext, workspace_id.encode())
    except Exception as exc:
        raise ValueError(f'Decryption failed for workspace {workspace_id!r}: {exc}') from exc
    finally:
        del dek

    _emit_dek_audit('dek_decrypt', workspace_id, caller_id)
    return plaintext


def rotate_workspace_key(workspace_id: str, *, caller_id: str = 'system') -> None:
    """
    Rotate the KEK for a workspace.

    1. Unwrap current DEK with old KEK
    2. Generate new KEK, write to Vault
    3. Re-wrap DEK with new KEK, update workspace_keys
    4. Update rotated_at timestamp

    The DEK value itself does not change — only the KEK that wraps it.
    This allows re-encryption of the DEK without re-encrypting all data.
    """
    # Fetch current wrapped DEK
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT dek_encrypted_with_kek, kek_ref FROM workspace_keys WHERE workspace_id = %s',
                (workspace_id,),
            )
            row = cur.fetchone()

    if not row:
        raise ValueError(f'Workspace {workspace_id!r} has no DEK')

    wrapped_dek = bytes(row['dek_encrypted_with_kek'])

    # Unwrap with current KEK — capture old version for B.3 destroy
    old_kek, old_version = _read_kek(workspace_id)
    try:
        dek = _unwrap_dek(wrapped_dek, old_kek)
    finally:
        del old_kek

    # Generate new KEK and re-wrap
    new_kek = os.urandom(_KEY_BYTES)
    new_kek_path = _write_kek(workspace_id, new_kek)
    new_wrapped_dek = _wrap_dek(dek, new_kek)

    del new_kek, dek

    # Update workspace_keys
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE workspace_keys
                      SET dek_encrypted_with_kek = %s,
                          kek_ref = %s,
                          rotated_at = now()
                    WHERE workspace_id = %s""",
                (psycopg2.Binary(new_wrapped_dek), new_kek_path, workspace_id),
            )
        conn.commit()

    # B.3: Destroy the old KEK version in Vault now that the new wrapped DEK is
    # safely persisted.  A failure here is logged as a warning — the rotation
    # itself succeeded and the old version is already unreachable via the updated
    # kek_ref.  A follow-up cleanup job can retry if needed.
    try:
        client, _ = _vault_client(workspace_id)
        path = _kek_vault_path(workspace_id)
        client.secrets.kv.v2.destroy_secret_versions(
            path=path,
            mount_point='sos',
            versions=[old_version],
        )
        log.info('Old KEK version %d destroyed for workspace %s', old_version, workspace_id)
    except Exception as exc:
        log.warning(
            'Failed to destroy old KEK version %d for workspace %s (non-fatal): %s',
            old_version,
            workspace_id,
            exc,
        )

    _emit_dek_audit('dek_rotate', workspace_id, caller_id)
    log.info('DEK rotated for workspace %s', workspace_id)


def has_workspace_key(workspace_id: str) -> bool:
    """Return True if the workspace has a provisioned DEK."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT 1 FROM workspace_keys WHERE workspace_id = %s',
                (workspace_id,),
            )
            return cur.fetchone() is not None
