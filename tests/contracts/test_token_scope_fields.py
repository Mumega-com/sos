"""v0.6.1 regression guard — tokens.json is_admin/is_system fields honored.

See `docs/plans/2026-04-18-v0.6.1-test-debt.md`. These fields were silently
ignored before v0.6.1 and broke economy route tests with 403 errors.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write_tokens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entries: list[dict]) -> Path:
    """Write *entries* to a temp tokens.json and point auth at it."""
    import sos.kernel.auth as auth_mod

    p = tmp_path / "tokens.json"
    p.write_text(json.dumps(entries))
    monkeypatch.setattr(auth_mod, "TOKENS_PATH", p)
    auth_mod._cache.invalidate()
    return p


def test_is_admin_field_in_tokens_json_promotes_to_admin_context(tmp_path, monkeypatch):
    """A tokens.json entry with is_admin=True must yield AuthContext.is_admin=True."""
    from sos.kernel.auth import verify_bearer

    _write_tokens(
        tmp_path,
        monkeypatch,
        [{"label": "ops", "token": "tk_admin_field", "active": True, "is_admin": True}],
    )

    ctx = verify_bearer("Bearer tk_admin_field")
    assert ctx is not None
    assert ctx.is_admin is True


def test_is_system_field_in_tokens_json_promotes_to_system_context(tmp_path, monkeypatch):
    """A tokens.json entry with is_system=True must yield AuthContext.is_system=True."""
    from sos.kernel.auth import verify_bearer

    _write_tokens(
        tmp_path,
        monkeypatch,
        [{"label": "sysd", "token": "tk_system_field", "active": True, "is_system": True}],
    )

    ctx = verify_bearer("Bearer tk_system_field")
    assert ctx is not None
    assert ctx.is_system is True


def test_plain_tenant_token_does_not_get_admin_or_system(tmp_path, monkeypatch):
    """A plain project-scoped token must not be promoted to admin or system."""
    from sos.kernel.auth import verify_bearer

    _write_tokens(
        tmp_path,
        monkeypatch,
        [{"label": "tenant", "token": "tk_plain", "project": "foo", "active": True}],
    )

    ctx = verify_bearer("Bearer tk_plain")
    assert ctx is not None
    assert ctx.is_admin is False
    assert ctx.is_system is False


def test_agent_admin_still_honored(tmp_path, monkeypatch):
    """Agent-name-based admin path must still work alongside the new field-reading code."""
    from sos.kernel.auth import verify_bearer

    _write_tokens(
        tmp_path,
        monkeypatch,
        [{"label": "kasra-tok", "token": "tk_kasra", "agent": "kasra", "active": True}],
    )

    ctx = verify_bearer("Bearer tk_kasra")
    assert ctx is not None
    assert ctx.is_admin is True
