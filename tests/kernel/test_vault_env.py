"""Tests for sos.kernel.vault_env — vault: reference resolver."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_vault_client(data: dict) -> MagicMock:
    """Return a mock hvac client whose kv.v2.read_secret_version returns data."""
    client = MagicMock()
    client.secrets.kv.v2.read_secret_version.return_value = {
        'data': {'data': data}
    }
    return client


# ── resolve() ────────────────────────────────────────────────────────────────

class TestResolve:
    def test_resolve_valid_ref(self):
        from sos.kernel.vault_env import resolve, _cache
        _cache.clear()

        vault_data = {'MY_KEY': 'secret-value-abc123'}
        with patch('sos.kernel.vault_env._vault_client', return_value=_make_vault_client(vault_data)):
            result = resolve('vault:sos/env/api-keys#MY_KEY')

        assert result == 'secret-value-abc123'

    def test_resolve_non_vault_ref_returns_none(self):
        from sos.kernel.vault_env import resolve
        assert resolve('plain-value') is None
        assert resolve('') is None
        assert resolve('http://example.com') is None

    def test_resolve_missing_field_returns_none(self):
        from sos.kernel.vault_env import resolve, _cache
        _cache.clear()

        vault_data = {'OTHER_KEY': 'value'}
        with patch('sos.kernel.vault_env._vault_client', return_value=_make_vault_client(vault_data)):
            result = resolve('vault:sos/env/api-keys#MISSING_KEY')

        assert result is None

    def test_resolve_vault_error_returns_none(self):
        from sos.kernel.vault_env import resolve, _cache
        _cache.clear()

        client = MagicMock()
        client.secrets.kv.v2.read_secret_version.side_effect = Exception('Connection refused')

        with patch('sos.kernel.vault_env._vault_client', return_value=client):
            result = resolve('vault:sos/env/api-keys#MY_KEY')

        assert result is None

    def test_resolve_caches_path(self):
        from sos.kernel.vault_env import resolve, _cache
        _cache.clear()

        vault_data = {'KEY_A': 'val-a', 'KEY_B': 'val-b'}
        mock_client = _make_vault_client(vault_data)

        with patch('sos.kernel.vault_env._vault_client', return_value=mock_client):
            resolve('vault:sos/env/api-keys#KEY_A')
            resolve('vault:sos/env/api-keys#KEY_B')

        # Both from the same path — should only call Vault once per path
        assert mock_client.secrets.kv.v2.read_secret_version.call_count == 1

    def test_resolve_different_paths_call_vault_separately(self):
        from sos.kernel.vault_env import resolve, _cache
        _cache.clear()

        def side_effect(path, mount_point, raise_on_deleted_version):
            key = f"sos/{path}"
            if key == 'sos/env/api-keys':
                return {'data': {'data': {'KEY': 'api-val'}}}
            if key == 'sos/env/database':
                return {'data': {'data': {'DB_URL': 'postgres://...'}}}
            raise ValueError(f"unexpected path {key}")

        client = MagicMock()
        client.secrets.kv.v2.read_secret_version.side_effect = side_effect

        with patch('sos.kernel.vault_env._vault_client', return_value=client):
            r1 = resolve('vault:sos/env/api-keys#KEY')
            r2 = resolve('vault:sos/env/database#DB_URL')

        assert r1 == 'api-val'
        assert r2 == 'postgres://...'
        assert client.secrets.kv.v2.read_secret_version.call_count == 2


# ── load() ────────────────────────────────────────────────────────────────────

class TestLoad:
    def test_load_resolves_vault_refs_in_env(self, monkeypatch):
        from sos.kernel.vault_env import load, _cache
        _cache.clear()

        monkeypatch.setenv('ELEVENLABS_API_KEY', 'vault:sos/env/api-keys#ELEVENLABS_API_KEY')
        monkeypatch.setenv('STRIPE_SECRET_KEY', 'vault:sos/env/api-keys#STRIPE_SECRET_KEY')
        monkeypatch.setenv('NORMAL_VAR', 'just-a-plain-value')

        vault_data = {
            'ELEVENLABS_API_KEY': 'sk_live_real_key_abc',
            'STRIPE_SECRET_KEY': 'sk_live_real_stripe_key',
        }
        with patch('sos.kernel.vault_env._vault_client', return_value=_make_vault_client(vault_data)):
            count = load()

        assert count == 2
        assert os.environ['ELEVENLABS_API_KEY'] == 'sk_live_real_key_abc'
        assert os.environ['STRIPE_SECRET_KEY'] == 'sk_live_real_stripe_key'
        assert os.environ['NORMAL_VAR'] == 'just-a-plain-value'

    def test_load_returns_zero_when_no_vault_refs(self, monkeypatch):
        from sos.kernel.vault_env import load, _cache
        _cache.clear()

        monkeypatch.setenv('PLAIN_VAR', 'plain-value')
        # Remove all vault: refs from environment
        for key in list(os.environ.keys()):
            if os.environ.get(key, '').startswith('vault:'):
                monkeypatch.delenv(key)

        count = load()
        assert count == 0

    def test_load_leaves_unresolvable_refs_in_place(self, monkeypatch):
        from sos.kernel.vault_env import load, _cache
        _cache.clear()

        monkeypatch.setenv('BROKEN_KEY', 'vault:sos/env/api-keys#NONEXISTENT')

        vault_data = {}  # empty — field not found
        with patch('sos.kernel.vault_env._vault_client', return_value=_make_vault_client(vault_data)):
            count = load()

        assert count == 0
        assert os.environ['BROKEN_KEY'] == 'vault:sos/env/api-keys#NONEXISTENT'

    def test_load_partial_resolution(self, monkeypatch):
        from sos.kernel.vault_env import load, _cache
        _cache.clear()

        monkeypatch.setenv('GOOD_KEY', 'vault:sos/env/api-keys#GOOD_KEY')
        monkeypatch.setenv('BAD_KEY', 'vault:sos/env/api-keys#MISSING_KEY')

        vault_data = {'GOOD_KEY': 'resolved-value'}
        with patch('sos.kernel.vault_env._vault_client', return_value=_make_vault_client(vault_data)):
            count = load()

        assert count == 1
        assert os.environ['GOOD_KEY'] == 'resolved-value'
        assert os.environ['BAD_KEY'] == 'vault:sos/env/api-keys#MISSING_KEY'


# ── Ref format parsing ────────────────────────────────────────────────────────

class TestRefFormat:
    @pytest.mark.parametrize('ref,valid', [
        ('vault:sos/env/api-keys#MY_KEY', True),
        ('vault:sos/env/database#DATABASE_URL', True),
        ('plain-value', False),
        ('vault:sos/env/missing-hash', False),  # no # separator
        ('', False),
    ])
    def test_ref_format_validity(self, ref, valid):
        from sos.kernel.vault_env import _VAULT_REF_RE
        assert bool(_VAULT_REF_RE.match(ref)) == valid

    def test_path_and_field_extracted_correctly(self):
        from sos.kernel.vault_env import _VAULT_REF_RE
        m = _VAULT_REF_RE.match('vault:sos/env/api-keys#ELEVENLABS_API_KEY')
        assert m is not None
        assert m.group('path') == 'sos/env/api-keys'
        assert m.group('field') == 'ELEVENLABS_API_KEY'
