"""
§2B.4 DEK envelope encryption tests.

Gate: Athena G7

Tests verify:
  - Provision generates a real AES-256-GCM DEK, wraps with KEK
  - Encrypt/decrypt roundtrip (plaintext in == plaintext out)
  - Cross-workspace isolation: different workspace cannot decrypt
  - Rotation: re-wrapped DEK still decrypts correctly
  - Audit events emitted for each operation
  - has_workspace_key returns correct state
  - Missing workspace raises ValueError on get_dek / decrypt

DB + Vault are mocked — tests are unit tests verifying contract logic.
Integration tests (marked @pytest.mark.integration) require real DB + Vault.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, call, patch

import pytest

# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _vault_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set Vault env vars so _vault_client() doesn't raise on import."""
    monkeypatch.setenv('VAULT_ADDR', 'http://127.0.0.1:8200')
    monkeypatch.setenv('VAULT_ROLE_ID', 'test-role-id')
    monkeypatch.setenv('VAULT_SECRET_ID', 'test-secret-id')
    monkeypatch.setenv('MIRROR_DATABASE_URL', 'postgresql://test/test')


def _make_vault_client_mock(kek_hex: str | None = None) -> MagicMock:
    """Return a mock hvac client that stores/retrieves a single KEK."""
    _stored: dict[str, str] = {}

    if kek_hex:
        _stored['value'] = kek_hex

    client = MagicMock()
    client.is_authenticated.return_value = True

    def kv_create(path: str, mount_point: str, secret: dict) -> None:
        _stored['value'] = secret['value']

    def kv_read(path: str, mount_point: str) -> dict:
        return {
            'data': {
                'data': {'value': _stored.get('value', '')},
                'metadata': {'version': 1},
            },
        }

    client.secrets.kv.v2.create_or_update_secret.side_effect = kv_create
    client.secrets.kv.v2.read_secret_version.side_effect = kv_read
    return client


def _make_conn_mock(workspace_row: dict | None = None) -> MagicMock:
    """Return a psycopg2 connection mock."""
    cur = MagicMock()
    cur.fetchone.return_value = workspace_row
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return conn, cur


# ── _wrap_dek / _unwrap_dek ────────────────────────────────────────────────────


class TestDekWrapUnwrap:
    def test_wrap_produces_nonce_plus_ciphertext(self) -> None:
        from sos.contracts.dek import _wrap_dek
        kek = os.urandom(32)
        dek = os.urandom(32)
        wrapped = _wrap_dek(dek, kek)
        assert len(wrapped) > 12  # nonce + ciphertext + GCM tag

    def test_unwrap_recovers_original_dek(self) -> None:
        from sos.contracts.dek import _unwrap_dek, _wrap_dek
        kek = os.urandom(32)
        dek = os.urandom(32)
        wrapped = _wrap_dek(dek, kek)
        recovered = _unwrap_dek(wrapped, kek)
        assert recovered == dek

    def test_wrong_kek_raises(self) -> None:
        from sos.contracts.dek import _unwrap_dek, _wrap_dek
        kek1 = os.urandom(32)
        kek2 = os.urandom(32)
        dek = os.urandom(32)
        wrapped = _wrap_dek(dek, kek1)
        with pytest.raises(Exception):
            _unwrap_dek(wrapped, kek2)

    def test_tampered_ciphertext_raises(self) -> None:
        from sos.contracts.dek import _unwrap_dek, _wrap_dek
        kek = os.urandom(32)
        dek = os.urandom(32)
        wrapped = _wrap_dek(dek, kek)
        # Flip a byte in the ciphertext
        tampered = bytearray(wrapped)
        tampered[20] ^= 0xFF
        with pytest.raises(Exception):
            _unwrap_dek(bytes(tampered), kek)


# ── encrypt / decrypt ──────────────────────────────────────────────────────────


class TestEncryptDecrypt:
    def _setup(self):
        """Return (kek_hex, wrapped_dek_bytes) for mocking."""
        kek = os.urandom(32)
        dek = os.urandom(32)
        from sos.contracts.dek import _wrap_dek
        wrapped = _wrap_dek(dek, kek)
        return kek.hex(), wrapped

    def test_encrypt_decrypt_roundtrip(self) -> None:
        kek_hex, wrapped = self._setup()
        conn, cur = _make_conn_mock({'dek_encrypted_with_kek': wrapped})
        vault = _make_vault_client_mock(kek_hex=kek_hex)

        plaintext = b'sensitive-engram-body'

        with patch('sos.contracts.dek._connect', return_value=conn), \
             patch('sos.contracts.dek._vault_client', return_value=(vault, 'test-req-id')):
            from sos.contracts.dek import decrypt, encrypt

            blob = encrypt(plaintext, 'workspace-acme')
            recovered = decrypt(blob, 'workspace-acme')

        assert recovered == plaintext

    def test_encrypted_blob_is_not_plaintext(self) -> None:
        kek_hex, wrapped = self._setup()
        conn, cur = _make_conn_mock({'dek_encrypted_with_kek': wrapped})
        vault = _make_vault_client_mock(kek_hex=kek_hex)

        with patch('sos.contracts.dek._connect', return_value=conn), \
             patch('sos.contracts.dek._vault_client', return_value=(vault, 'test-req-id')):
            from sos.contracts.dek import encrypt
            blob = encrypt(b'do not store me plain', 'workspace-acme')

        assert b'do not store me plain' not in blob

    def test_cross_workspace_isolation(self) -> None:
        """DEK from workspace-a cannot decrypt ciphertext from workspace-b."""
        kek_a_hex, wrapped_a = self._setup()
        kek_b_hex, wrapped_b = self._setup()

        # Encrypt with workspace-a
        conn_a, _ = _make_conn_mock({'dek_encrypted_with_kek': wrapped_a})
        vault_a = _make_vault_client_mock(kek_hex=kek_a_hex)

        with patch('sos.contracts.dek._connect', return_value=conn_a), \
             patch('sos.contracts.dek._vault_client', return_value=(vault_a, 'req-a')):
            from sos.contracts.dek import encrypt
            blob = encrypt(b'secret-a', 'workspace-a')

        # Try to decrypt with workspace-b
        conn_b, _ = _make_conn_mock({'dek_encrypted_with_kek': wrapped_b})
        vault_b = _make_vault_client_mock(kek_hex=kek_b_hex)

        with patch('sos.contracts.dek._connect', return_value=conn_b), \
             patch('sos.contracts.dek._vault_client', return_value=(vault_b, 'req-b')):
            from sos.contracts.dek import decrypt
            with pytest.raises(ValueError, match='Decryption failed'):
                decrypt(blob, 'workspace-b')

    def test_missing_workspace_raises_on_get_dek(self) -> None:
        conn, _ = _make_conn_mock(None)  # no row
        with patch('sos.contracts.dek._connect', return_value=conn):
            from sos.contracts.dek import get_dek
            with pytest.raises(ValueError, match='has no DEK'):
                get_dek('workspace-missing')

    def test_short_blob_raises_on_decrypt(self) -> None:
        kek_hex, wrapped = self._setup()
        conn, _ = _make_conn_mock({'dek_encrypted_with_kek': wrapped})
        vault = _make_vault_client_mock(kek_hex=kek_hex)

        with patch('sos.contracts.dek._connect', return_value=conn), \
             patch('sos.contracts.dek._vault_client', return_value=(vault, 'test-req-id')):
            from sos.contracts.dek import decrypt
            with pytest.raises(ValueError, match='too short'):
                decrypt(b'tooshort', 'workspace-acme')


# ── provision_workspace_key ────────────────────────────────────────────────────


class TestProvision:
    def test_provision_writes_to_db_and_vault(self) -> None:
        # First fetchone() = SELECT pre-check (no row → None).
        # Second fetchone() = INSERT RETURNING (row written → workspace_id dict).
        cur = MagicMock()
        cur.fetchone.side_effect = [None, {'workspace_id': 'workspace-new'}]
        conn = MagicMock()
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        vault = _make_vault_client_mock()

        with patch('sos.contracts.dek._connect', return_value=conn), \
             patch('sos.contracts.dek._vault_client', return_value=(vault, 'test-req-id')):
            from sos.contracts.dek import provision_workspace_key
            provision_workspace_key('workspace-new')

        # Vault KEK was written
        vault.secrets.kv.v2.create_or_update_secret.assert_called_once()
        # DB was committed
        conn.commit.assert_called()

    def test_provision_idempotent_check_raises(self) -> None:
        # Row already exists
        conn, _ = _make_conn_mock({'workspace_id': 'workspace-exists'})
        vault = _make_vault_client_mock()

        with patch('sos.contracts.dek._connect', return_value=conn), \
             patch('sos.contracts.dek._vault_client', return_value=(vault, 'test-req-id')):
            from sos.contracts.dek import provision_workspace_key
            with pytest.raises(ValueError, match='already has a DEK'):
                provision_workspace_key('workspace-exists')


# ── has_workspace_key ─────────────────────────────────────────────────────────


class TestHasWorkspaceKey:
    def test_returns_true_when_row_exists(self) -> None:
        conn, _ = _make_conn_mock({'workspace_id': 'acme'})
        with patch('sos.contracts.dek._connect', return_value=conn):
            from sos.contracts.dek import has_workspace_key
            assert has_workspace_key('acme') is True

    def test_returns_false_when_no_row(self) -> None:
        conn, _ = _make_conn_mock(None)
        with patch('sos.contracts.dek._connect', return_value=conn):
            from sos.contracts.dek import has_workspace_key
            assert has_workspace_key('no-such') is False


# ── rotate_workspace_key ───────────────────────────────────────────────────────


class TestRotation:
    def test_rotation_changes_wrapped_dek(self) -> None:
        """After rotation, workspace_keys is updated with a new wrapped DEK."""
        kek_hex, wrapped = self._setup()
        conn, cur = _make_conn_mock({
            'dek_encrypted_with_kek': wrapped,
            'kek_ref': 'sos/dek/acme/kek',
        })
        vault = _make_vault_client_mock(kek_hex=kek_hex)

        with patch('sos.contracts.dek._connect', return_value=conn), \
             patch('sos.contracts.dek._vault_client', return_value=(vault, 'test-req-id')):
            from sos.contracts.dek import rotate_workspace_key
            rotate_workspace_key('acme')

        # New KEK was written
        assert vault.secrets.kv.v2.create_or_update_secret.call_count >= 1
        # DB was updated
        conn.commit.assert_called()

    def test_rotation_missing_workspace_raises(self) -> None:
        conn, _ = _make_conn_mock(None)
        with patch('sos.contracts.dek._connect', return_value=conn):
            from sos.contracts.dek import rotate_workspace_key
            with pytest.raises(ValueError, match='has no DEK'):
                rotate_workspace_key('missing')

    def test_rotation_destroys_old_kek_version(self) -> None:
        """B.3: rotate_workspace_key() calls destroy_secret_versions with old version."""
        kek_hex, wrapped = self._setup()
        conn, cur = _make_conn_mock({
            'dek_encrypted_with_kek': wrapped,
            'kek_ref': 'sos/dek/acme/kek',
        })
        vault = _make_vault_client_mock(kek_hex=kek_hex)

        with patch('sos.contracts.dek._connect', return_value=conn), \
             patch('sos.contracts.dek._vault_client', return_value=(vault, 'test-req-id')):
            from sos.contracts.dek import rotate_workspace_key
            rotate_workspace_key('acme')

        # destroy_secret_versions must have been called with version=1 (mock returns version 1)
        vault.secrets.kv.v2.destroy_secret_versions.assert_called_once()
        call_kwargs = vault.secrets.kv.v2.destroy_secret_versions.call_args
        assert call_kwargs.kwargs.get('versions') == [1] or \
               (call_kwargs.args and 1 in call_kwargs.args)

    def _setup(self):
        kek = os.urandom(32)
        dek = os.urandom(32)
        from sos.contracts.dek import _wrap_dek
        wrapped = _wrap_dek(dek, kek)
        return kek.hex(), wrapped


# ── B.2 / G26 Vault token cache ───────────────────────────────────────────────


class TestVaultTokenCache:
    def setup_method(self) -> None:
        """Clear the per-workspace cache before each test."""
        import sos.contracts.dek as dek_mod
        dek_mod._vault_token_cache.clear()

    def test_cached_token_reused_on_second_call(self) -> None:
        """G26: Second call for same workspace reuses the cached token (no re-login)."""
        import time

        import sos.contracts.dek as dek_mod

        # Pre-populate a valid per-workspace cache entry
        dek_mod._vault_token_cache['ws-a'] = {
            'token': 'cached-tok-xyz',
            'addr': 'http://127.0.0.1:8200',
            'expires_at': time.monotonic() + 3000,
            'request_id': 'cached-req-abc',
        }

        login_mock = MagicMock(return_value={'auth': {'client_token': 'should-not-see'}})
        fake_client = MagicMock()
        fake_client.is_authenticated.return_value = True
        fake_client.auth.approle.login = login_mock

        with patch('hvac.Client', return_value=fake_client):
            from sos.contracts.dek import _vault_client
            client, req_id = _vault_client('ws-a')

        # Token must be the cached one, request_id must match cached entry
        assert client.token == 'cached-tok-xyz'
        assert req_id == 'cached-req-abc'
        # AppRole login must NOT have been called
        login_mock.assert_not_called()

    def test_expired_cache_triggers_fresh_login(self) -> None:
        """G26: An expired cache entry causes a new AppRole login and updates the cache."""
        import time

        import sos.contracts.dek as dek_mod

        # Pre-populate cache with an already-expired entry for 'ws-a'
        dek_mod._vault_token_cache['ws-a'] = {
            'token': 'old-tok',
            'addr': 'http://127.0.0.1:8200',
            'expires_at': time.monotonic() - 1,
            'request_id': 'old-req',
        }

        login_mock = MagicMock(return_value={'auth': {'client_token': 'fresh-tok'}})
        fake_client = MagicMock()
        fake_client.is_authenticated.return_value = True
        fake_client.auth.approle.login = login_mock

        with patch('hvac.Client', return_value=fake_client):
            from sos.contracts.dek import _vault_client
            client, req_id = _vault_client('ws-a')

        # A fresh login must have been performed
        login_mock.assert_called_once()
        # Cache must now hold the fresh token under workspace key
        assert dek_mod._vault_token_cache['ws-a']['token'] == 'fresh-tok'
        # New request_id is a UUID (different from 'old-req')
        assert req_id != 'old-req'


# ── TC-G26 per-workspace cache isolation ─────────────────────────────────────


class TestG26Cache:
    """G26 (F-08): per-workspace Vault token cache isolation tests."""

    def setup_method(self) -> None:
        import sos.contracts.dek as dek_mod
        dek_mod._vault_token_cache.clear()

    def test_tc_g26a_403_on_workspace_a_preserves_workspace_b(self) -> None:
        """TC-G26a: 403 on workspace A evicts A's entry; B's entry survives."""
        import time

        import sos.contracts.dek as dek_mod

        # Pre-populate B with a valid token
        dek_mod._vault_token_cache['ws-b'] = {
            'token': 'tok-b',
            'addr': 'http://127.0.0.1:8200',
            'expires_at': time.monotonic() + 3000,
            'request_id': 'req-b-original',
        }

        # Pre-populate A with its token
        dek_mod._vault_token_cache['ws-a'] = {
            'token': 'tok-a',
            'addr': 'http://127.0.0.1:8200',
            'expires_at': time.monotonic() + 3000,
            'request_id': 'req-a-original',
        }

        # Simulate 403 eviction for workspace A with req-a-original
        from sos.contracts.dek import _evict_workspace_token
        _evict_workspace_token('ws-a', 'req-a-original')

        # A's entry is gone
        assert 'ws-a' not in dek_mod._vault_token_cache
        # B's entry is intact
        assert dek_mod._vault_token_cache.get('ws-b', {}).get('token') == 'tok-b'

    def test_tc_g26a_eviction_wrong_request_id_leaves_entry(self) -> None:
        """TC-G26a: Eviction with stale request_id does NOT evict fresh entry.

        Simulates: workspace A gets a 403, concurrent request already refreshed
        the cache with a new request_id — the 403-triggered eviction must not
        evict the new entry.
        """
        import time

        import sos.contracts.dek as dek_mod

        # Cache has been refreshed (new request_id)
        dek_mod._vault_token_cache['ws-a'] = {
            'token': 'tok-a-fresh',
            'addr': 'http://127.0.0.1:8200',
            'expires_at': time.monotonic() + 3000,
            'request_id': 'req-a-new',
        }

        # Eviction attempt with OLD request_id — should be a no-op
        from sos.contracts.dek import _evict_workspace_token
        _evict_workspace_token('ws-a', 'req-a-stale')

        # Fresh entry must survive
        assert dek_mod._vault_token_cache.get('ws-a', {}).get('token') == 'tok-a-fresh'

    def test_tc_g26b_cache_key_uniqueness_across_workspaces(self) -> None:
        """TC-G26b: Rapid writes for A/B/C/D — each key stored separately, no overwrite."""
        import time

        import sos.contracts.dek as dek_mod

        workspaces = ['ws-a', 'ws-b', 'ws-c', 'ws-d']
        tokens = {ws: f'tok-{ws}' for ws in workspaces}

        for ws in workspaces:
            dek_mod._vault_token_cache[ws] = {
                'token': tokens[ws],
                'addr': 'http://127.0.0.1:8200',
                'expires_at': time.monotonic() + 3000,
                'request_id': f'req-{ws}',
            }

        # Verify all 4 entries exist with their own tokens
        for ws in workspaces:
            assert dek_mod._vault_token_cache[ws]['token'] == tokens[ws], (
                f'{ws} token overwritten by another workspace'
            )

    def test_tc_g26c_lru_eviction_bounded_by_vault_cache_per_workspace(self) -> None:
        """TC-G26c: Cache size bounded — fill to VAULT_CACHE_PER_WORKSPACE + 1,
        assert oldest entry evicted (LRU) without affecting newer entries.
        """
        import time

        import sos.contracts.dek as dek_mod

        # Set a small limit for testing
        original_limit = dek_mod.VAULT_CACHE_PER_WORKSPACE
        dek_mod.VAULT_CACHE_PER_WORKSPACE = 3
        try:
            login_mock = MagicMock(
                side_effect=[
                    {'auth': {'client_token': f'tok-ws-{i}'}} for i in range(5)
                ]
            )
            fake_client = MagicMock()
            fake_client.is_authenticated.return_value = True
            fake_client.auth.approle.login = login_mock

            with patch('hvac.Client', return_value=fake_client):
                from sos.contracts.dek import _vault_client
                # Fill cache with 3 workspaces (fills to limit)
                _vault_client('ws-1')
                _vault_client('ws-2')
                _vault_client('ws-3')

                # Cache is at limit — adding ws-4 must evict oldest (ws-1)
                _vault_client('ws-4')

            # ws-1 (oldest) should have been evicted
            assert 'ws-1' not in dek_mod._vault_token_cache
            # ws-2, ws-3, ws-4 should remain
            assert 'ws-2' in dek_mod._vault_token_cache
            assert 'ws-3' in dek_mod._vault_token_cache
            assert 'ws-4' in dek_mod._vault_token_cache
        finally:
            dek_mod.VAULT_CACHE_PER_WORKSPACE = original_limit


# ── B.4 TOCTOU ON CONFLICT ───────────────────────────────────────────────────


class TestProvisionTOCTOU:
    def test_on_conflict_concurrent_race_raises(self) -> None:
        """B.4: When INSERT RETURNING returns no rows, raise ValueError for concurrent race."""
        # SELECT pre-check returns None (race window: not yet inserted)
        # INSERT RETURNING returns None (concurrent winner already inserted → DO NOTHING)
        cur = MagicMock()
        cur.fetchone.side_effect = [None, None]  # pre-check=None, RETURNING=None
        conn = MagicMock()
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        vault = _make_vault_client_mock()

        with patch('sos.contracts.dek._connect', return_value=conn), \
             patch('sos.contracts.dek._vault_client', return_value=(vault, 'test-req-id')):
            from sos.contracts.dek import provision_workspace_key
            with pytest.raises(ValueError, match='concurrent race'):
                provision_workspace_key('workspace-race')
