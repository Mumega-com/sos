"""Integration tests — auth caller migration.

Verifies that all migrated callers (dashboard, economy, mirror) still honour
the public-function contracts after delegating to sos.services.auth.verify_bearer.

Tests:
  1-2.  Dashboard _verify_token returns the correct entry dict shape.
  3-4.  Economy _verify_bearer returns the correct entry dict shape.
  5-6.  Admin token (sk-hadi-ops-*) validates via dashboard and economy paths.
  7-8.  Cross-tenant forbid: tenant-A token must not read tenant-B data.
  9-10. System env-var token (SOS_SYSTEM_TOKEN) grants admin via dashboard.
  11.   Mirror resolve_token returns "sos:<project>" for a scoped bus token.
  12.   Mirror resolve_token returns None for an internal (no-project) bus token.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


def _write_tokens(tmp_path: Path, entries: list[dict[str, Any]]) -> Path:
    p = tmp_path / "tokens.json"
    p.write_text(json.dumps(entries))
    return p


# Stable admin token minted 2026-04-16 — matches the token_hash in tokens.json.
ADMIN_TOKEN_RAW = "sk-hadi-ops-0ec359ba06ffea21c6048b9d94bcb960"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _strip_env_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent env-var system tokens from leaking between tests."""
    for var in ("SOS_SYSTEM_TOKEN", "MIRROR_TOKEN", "BUS_BRIDGE_TOKEN", "CYRUS_BUS_TOKEN"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture()
def patched_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Return (auth_module, tokens_file) with TOKENS_PATH pointed at tmp_path."""
    import importlib
    import sos.kernel.auth as auth

    tokens_file = _write_tokens(tmp_path, [])
    monkeypatch.setattr(auth, "TOKENS_PATH", tokens_file)
    auth._cache.invalidate()
    return auth, tokens_file


@pytest.fixture()
def dashboard_module(patched_auth, monkeypatch: pytest.MonkeyPatch):
    """Return dashboard module with auth patched to use tmp tokens."""
    auth, tokens_file = patched_auth
    import sos.services.dashboard.auth as dash_auth

    # Redirect dashboard auth submodule to the same patched instance.
    monkeypatch.setattr(dash_auth, "_auth_verify_bearer", auth.verify_bearer)
    return dash_auth, tokens_file, auth


@pytest.fixture()
def economy_module(patched_auth, monkeypatch: pytest.MonkeyPatch):
    """Return economy module with auth patched to use tmp tokens.

    Soft-skips when the economy service cannot be imported (e.g. optional
    Solana-plugin deps like `base58` missing in the local env). The auth-migration
    contract the economy tests cover is still exercised by the dashboard-side
    tests in this file.
    """
    auth, tokens_file = patched_auth
    econ = pytest.importorskip(
        "sos.services.economy.app",
        reason="economy service unavailable (optional Solana deps) — Loom 2026-04-18",
    )
    monkeypatch.setattr(econ, "_auth_verify_bearer", auth.verify_bearer)
    return econ, tokens_file, auth


# ---------------------------------------------------------------------------
# Tests 1-2 — Dashboard _verify_token
# ---------------------------------------------------------------------------


def test_dashboard_verify_token_returns_dict_shape(dashboard_module):
    """_verify_token must return a dict with at least project and label keys."""
    dash, tokens_file, auth = dashboard_module
    raw = "sk-dash-test-001"
    tokens_file.write_text(
        json.dumps([
            {
                "token_hash": _sha256(raw),
                "project": "acme",
                "label": "Acme Corp",
                "active": True,
                "agent": "acme-bot",
            }
        ])
    )
    auth._cache.invalidate()
    entry = dash._verify_token(raw)
    assert entry is not None, "_verify_token returned None for a valid token"
    assert entry["project"] == "acme"
    assert entry["label"] == "Acme Corp"
    assert entry.get("active") is True


def test_dashboard_verify_token_bad_token_returns_none(dashboard_module):
    """_verify_token must return None for an unknown token."""
    dash, tokens_file, auth = dashboard_module
    tokens_file.write_text(json.dumps([]))
    auth._cache.invalidate()
    assert dash._verify_token("sk-not-in-file") is None


# ---------------------------------------------------------------------------
# Tests 3-4 — Economy _verify_bearer
# ---------------------------------------------------------------------------


def test_economy_verify_bearer_returns_dict_shape(economy_module):
    """_verify_bearer must return a dict with a 'project' key usable by _resolve_tenant."""
    from fastapi import HTTPException
    econ, tokens_file, auth = economy_module
    raw = "sk-econ-test-001"
    tokens_file.write_text(
        json.dumps([
            {
                "token_hash": _sha256(raw),
                "project": "viamar",
                "label": "Viamar",
                "active": True,
                "agent": "viamar-bot",
            }
        ])
    )
    auth._cache.invalidate()
    entry = econ._verify_bearer(f"Bearer {raw}")
    assert entry is not None
    assert entry["project"] == "viamar"
    tenant = econ._resolve_tenant(entry)
    assert tenant == "viamar"


def test_economy_verify_bearer_invalid_raises_401(economy_module):
    """_verify_bearer must raise HTTPException 401 for an invalid token."""
    from fastapi import HTTPException
    econ, tokens_file, auth = economy_module
    tokens_file.write_text(json.dumps([]))
    auth._cache.invalidate()
    with pytest.raises(HTTPException) as exc_info:
        econ._verify_bearer("Bearer sk-not-valid")
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# Tests 5-6 — Admin token (sk-hadi-ops-...) via dashboard and economy
# ---------------------------------------------------------------------------


def test_admin_token_validates_via_dashboard(dashboard_module):
    """The admin token minted 2026-04-16 must verify via the dashboard path."""
    dash, tokens_file, auth = dashboard_module
    tokens_file.write_text(
        json.dumps([
            {
                "token_hash": _sha256(ADMIN_TOKEN_RAW),
                "project": None,
                "label": "Hadi Ops Admin",
                "active": True,
                "agent": "mumega",
            }
        ])
    )
    auth._cache.invalidate()
    entry = dash._verify_token(ADMIN_TOKEN_RAW)
    assert entry is not None, "Admin token did not validate via dashboard path"
    assert entry.get("is_admin") is True


def test_admin_token_validates_via_economy(economy_module):
    """The admin token must validate via economy's _verify_bearer and return is_admin."""
    econ, tokens_file, auth = economy_module
    tokens_file.write_text(
        json.dumps([
            {
                "token_hash": _sha256(ADMIN_TOKEN_RAW),
                "project": None,
                "label": "Hadi Ops Admin",
                "active": True,
                "agent": "mumega",
            }
        ])
    )
    auth._cache.invalidate()
    entry = econ._verify_bearer(f"Bearer {ADMIN_TOKEN_RAW}")
    assert entry is not None
    assert entry.get("is_admin") is True
    # System-scoped admin token — project is None, so _resolve_tenant returns None.
    assert econ._resolve_tenant(entry) is None


# ---------------------------------------------------------------------------
# Tests 7-8 — Cross-tenant forbid semantics
# ---------------------------------------------------------------------------


def test_cross_tenant_dashboard_token_does_not_leak(dashboard_module):
    """A token scoped to tenant A must not appear as tenant B."""
    dash, tokens_file, auth = dashboard_module
    raw_a = "sk-tenant-a-001"
    raw_b = "sk-tenant-b-001"
    tokens_file.write_text(
        json.dumps([
            {
                "token_hash": _sha256(raw_a),
                "project": "tenant-a",
                "label": "Tenant A",
                "active": True,
                "agent": "bot-a",
            },
            {
                "token_hash": _sha256(raw_b),
                "project": "tenant-b",
                "label": "Tenant B",
                "active": True,
                "agent": "bot-b",
            },
        ])
    )
    auth._cache.invalidate()
    entry_a = dash._verify_token(raw_a)
    assert entry_a is not None
    assert entry_a["project"] == "tenant-a"
    # token A must not resolve to tenant B
    assert entry_a["project"] != "tenant-b"


def test_cross_tenant_economy_forbid(economy_module):
    """A tenant-A token must not be usable to write events for tenant-B."""
    from fastapi import HTTPException
    import asyncio
    from httpx import AsyncClient
    from fastapi.testclient import TestClient

    econ, tokens_file, auth = economy_module
    raw_a = "sk-cross-tenant-a"
    tokens_file.write_text(
        json.dumps([
            {
                "token_hash": _sha256(raw_a),
                "project": "tenant-a",
                "label": "Cross Tenant A",
                "active": True,
                "agent": "bot-a",
            }
        ])
    )
    auth._cache.invalidate()

    client = TestClient(econ.app, raise_server_exceptions=True)
    resp = client.post(
        "/usage",
        headers={"Authorization": f"Bearer {raw_a}"},
        json={
            "tenant": "tenant-b",  # Different tenant — should be 403
            "provider": "anthropic",
            "model": "claude-3-5-haiku",
            "input_tokens": 100,
            "output_tokens": 50,
            "cost_micros": 1000,
            "cost_currency": "USD",
        },
    )
    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# Tests 9-10 — SOS_SYSTEM_TOKEN grants admin via dashboard and economy
# ---------------------------------------------------------------------------


def test_system_env_token_grants_admin_via_dashboard(dashboard_module, monkeypatch: pytest.MonkeyPatch):
    """SOS_SYSTEM_TOKEN set in env must verify as is_admin=True via dashboard."""
    dash, tokens_file, auth = dashboard_module
    monkeypatch.setenv("SOS_SYSTEM_TOKEN", "sk-env-system-tok")
    entry = dash._verify_token("sk-env-system-tok")
    assert entry is not None
    assert entry.get("is_admin") is True
    assert entry.get("is_system") is True


def test_system_env_token_grants_admin_via_economy(economy_module, monkeypatch: pytest.MonkeyPatch):
    """SOS_SYSTEM_TOKEN set in env must verify as is_admin=True via economy."""
    econ, tokens_file, auth = economy_module
    monkeypatch.setenv("SOS_SYSTEM_TOKEN", "sk-env-system-econ")
    entry = econ._verify_bearer("Bearer sk-env-system-econ")
    assert entry is not None
    assert entry.get("is_admin") is True
    assert entry.get("is_system") is True


# ---------------------------------------------------------------------------
# Tests 11-12 — Mirror resolve_token SOS bus token path
# ---------------------------------------------------------------------------


_MIRROR_DIR = Path("/home/mumega/mirror")


def _get_mirror_resolve_token():
    """Import resolve_token from mirror_api, adding mirror dir to sys.path."""
    import sys as _sys
    import importlib

    mirror_dir = str(_MIRROR_DIR)
    added = mirror_dir not in _sys.path
    if added:
        _sys.path.insert(0, mirror_dir)
    try:
        if "mirror_api" in _sys.modules:
            mirror = importlib.reload(_sys.modules["mirror_api"])
        else:
            import mirror_api as mirror  # type: ignore[import]
        return mirror.resolve_token
    finally:
        if added:
            _sys.path.remove(mirror_dir)


def test_mirror_resolve_token_scoped_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """resolve_token must return 'sos:<project>' for a project-scoped SOS bus token."""
    pytest.importorskip("mirror_api", reason="mirror_api not importable")  # soft-skip if no db
    import sos.kernel.auth as auth

    raw = "sk-mirror-scoped-001"
    tokens_file = _write_tokens(
        tmp_path,
        [
            {
                "token_hash": _sha256(raw),
                "project": "frc-project",
                "label": "FRC",
                "active": True,
                "agent": "frc-bot",
            }
        ],
    )
    monkeypatch.setattr(auth, "TOKENS_PATH", tokens_file)
    auth._cache.invalidate()

    resolve_token = _get_mirror_resolve_token()
    result = resolve_token(f"Bearer {raw}")
    assert result == "sos:frc-project", f"Expected 'sos:frc-project', got {result!r}"


def test_mirror_resolve_token_internal_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """resolve_token must return None for an internal (no-project) SOS bus token."""
    pytest.importorskip("mirror_api", reason="mirror_api not importable")
    import sos.kernel.auth as auth

    raw = "sk-mirror-internal-001"
    tokens_file = _write_tokens(
        tmp_path,
        [
            {
                "token_hash": _sha256(raw),
                "project": None,
                "label": "Internal Agent",
                "active": True,
                "agent": "kasra",
            }
        ],
    )
    monkeypatch.setattr(auth, "TOKENS_PATH", tokens_file)
    auth._cache.invalidate()

    resolve_token = _get_mirror_resolve_token()
    result = resolve_token(f"Bearer {raw}")
    assert result is None, f"Expected None for internal agent token, got {result!r}"
