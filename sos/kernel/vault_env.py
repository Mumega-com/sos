"""
vault_env — resolve vault: references in environment variables.

At startup, any env var with a value matching:
    vault:sos/env/<secret-name>#<field>
is replaced with the actual secret fetched from Vault.

Usage:
    from sos.kernel.vault_env import load
    load()   # call once at service startup before reading env vars

Vault ref format:
    vault:sos/env/api-keys#ELEVENLABS_API_KEY
    vault:sos/env/database#DATABASE_URL

This module uses the same AppRole credentials as dek.py (VAULT_ADDR,
VAULT_ROLE_ID, VAULT_SECRET_ID from environment).

If Vault is unreachable the module logs a warning and leaves the
vault: ref strings in place — callers must handle missing values.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

log = logging.getLogger(__name__)

# Cache: {path -> {field -> value}} so we only fetch each path once per process
_cache: dict[str, dict[str, str]] = {}

_VAULT_REF_RE = re.compile(r'^vault:(?P<path>[^#]+)#(?P<field>.+)$')


def _vault_client():
    """Return authenticated hvac client reusing the dek.py cache."""
    # Import here to avoid circular deps and allow use in psycopg2-free envs
    try:
        from sos.contracts.dek import _vault_client as dek_client
        return dek_client()
    except Exception:
        pass

    # Fallback: fresh AppRole login
    try:
        import hvac  # type: ignore[import]
    except ImportError:
        raise RuntimeError('hvac not installed — cannot resolve vault: refs')

    addr = os.environ.get('VAULT_ADDR', 'http://127.0.0.1:8200')
    role_id = os.environ.get('VAULT_ROLE_ID', '')
    secret_id = os.environ.get('VAULT_SECRET_ID', '')
    if not role_id or not secret_id:
        raise RuntimeError('VAULT_ROLE_ID and VAULT_SECRET_ID are required')

    client = hvac.Client(url=addr)
    resp = client.auth.approle.login(role_id=role_id, secret_id=secret_id)
    client.token = resp['auth']['client_token']
    return client


def _fetch_path(path: str) -> dict[str, str]:
    """Fetch all fields from a Vault KV v2 path. Raises on error."""
    if path in _cache:
        return _cache[path]

    client = _vault_client()
    # path is e.g. "sos/env/api-keys" — mount point is "sos", key is "env/api-keys"
    mount, _, key = path.partition('/')
    result = client.secrets.kv.v2.read_secret_version(
        path=key,
        mount_point=mount,
        raise_on_deleted_version=True,
    )
    data: dict[str, str] = result['data']['data']
    _cache[path] = data
    return data


def resolve(ref: str) -> Optional[str]:
    """
    Resolve a single vault: reference string.
    Returns the secret value, or None if resolution fails.
    """
    m = _VAULT_REF_RE.match(ref)
    if not m:
        return None
    path = m.group('path')
    field = m.group('field')
    try:
        data = _fetch_path(path)
        return data.get(field)
    except Exception as exc:
        log.warning('vault_env: failed to resolve %s: %s', ref, exc)
        return None


def load(prefix: str = 'vault:') -> int:
    """
    Scan all environment variables. Any value starting with `prefix`
    that matches the vault: ref format is replaced in os.environ with
    the actual secret value from Vault.

    Returns the number of variables resolved.
    """
    refs = {k: v for k, v in os.environ.items() if v.startswith(prefix)}
    if not refs:
        return 0

    resolved = 0
    for key, ref in refs.items():
        value = resolve(ref)
        if value is not None:
            os.environ[key] = value
            resolved += 1
        else:
            log.warning('vault_env: could not resolve %s=%s — leaving ref in place', key, ref)

    log.info('vault_env: resolved %d/%d vault: references', resolved, len(refs))
    return resolved
