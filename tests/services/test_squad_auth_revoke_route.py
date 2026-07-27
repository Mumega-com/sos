"""BLOCK-2 fix regression tests (sos-205-b5307dd7 re-gate).

The re-gate verdict proved the CLI's `revoke` subcommand printed
`cache_cleared=true` while only ever clearing its OWN (separate-process,
always-empty) cache — the running service kept authenticating a revoked
token for up to the positive-cache TTL. The fix has two halves:

1. An in-service `POST /auth/revoke` route (app.py) that runs
   `revoke_api_key` IN the serving process, so it can actually clear the
   cache that matters.
2. An honest CLI (`sos/services/squad/auth.py::_cli`) that tries that route
   first via `_revoke_via_service` and only prints a receipt claiming the
   cache is clear when it genuinely reached the service — never a bare
   `cache_cleared=true` fabricated from the CLI's own empty state.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sos.services.squad import app as app_module
from sos.services.squad import auth
from sos.services.squad.service import SquadDB


_DDL = """
    CREATE TABLE api_keys (
        token_hash TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        identity_type TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
"""


@pytest.fixture(autouse=True)
def _fresh_cache():
    auth._token_cache_clear()
    auth._TOKEN_CACHE_INFLIGHT.clear()
    yield
    auth._token_cache_clear()
    auth._TOKEN_CACHE_INFLIGHT.clear()


# ── POST /auth/revoke route ──────────────────────────────────────────────


def test_auth_revoke_route_requires_system_bearer(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(app_module, "_SYSTEM_BEARERS", {"sys-tok-test"})
    client = TestClient(app_module.app)

    resp = client.post("/auth/revoke", json={"tenant_id": "acme"})
    assert resp.status_code == 401  # no Authorization header at all

    resp = client.post(
        "/auth/revoke",
        json={"tenant_id": "acme"},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 403


def test_auth_revoke_route_deletes_rows_and_clears_service_process_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The whole point of this route: a positive cache entry populated IN
    THIS PROCESS (simulating what require_capability/lookup_token would have
    done for a live request) must be gone after the route call, and the
    revoked token must stop authenticating immediately — no TTL wait."""
    db_path = tmp_path / "revoke_route.db"
    database = SquadDB(db_path=db_path)
    with database.connect() as conn:
        conn.execute(_DDL)

    monkeypatch.setattr(app_module, "_SYSTEM_BEARERS", {"sys-tok-test"})
    # The route builds its own SquadDB(); redirect it at the app-module
    # import site to this test's throwaway DB.
    monkeypatch.setattr(app_module, "SquadDB", lambda: database)

    token, _created = auth.create_api_key("fired-contractor", "agent", db=database)
    assert auth._lookup_token(token, database) is not None  # populate positive cache
    assert auth._TOKEN_CACHE_POSITIVE  # sanity: something is cached

    client = TestClient(app_module.app)
    resp = client.post(
        "/auth/revoke",
        json={"tenant_id": "fired-contractor"},
        headers={"Authorization": "Bearer sys-tok-test"},
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "tenant_id": "fired-contractor",
        "revoked_rows": 1,
        "cache_cleared": True,
    }

    # This is the property the old CLI-only fix could never deliver: the
    # SAME cache _lookup_token reads from, cleared in the SAME process.
    assert auth._TOKEN_CACHE_POSITIVE == {}
    assert auth._lookup_token(token, database) is None


# ── auth._revoke_via_service (the CLI's honest probe) ────────────────────


class _Resp:
    def __init__(self, status_code: int):
        self.status_code = status_code


def test_revoke_via_service_true_on_200(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth.requests, "post", lambda *a, **k: _Resp(200))
    assert auth._revoke_via_service("acme", "http://localhost:8060", "sys-tok") is True


def test_revoke_via_service_false_on_non_200(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth.requests, "post", lambda *a, **k: _Resp(403))
    assert auth._revoke_via_service("acme", "http://localhost:8060", "sys-tok") is False


def test_revoke_via_service_false_on_connection_error(monkeypatch: pytest.MonkeyPatch):
    def _boom(*a, **k):
        raise auth.requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(auth.requests, "post", _boom)
    assert auth._revoke_via_service("acme", "http://localhost:8060", "sys-tok") is False


def test_revoke_via_service_false_without_system_token():
    # No token configured -> must refuse to even attempt the call, not send
    # an unauthenticated revoke request.
    assert auth._revoke_via_service("acme", "http://localhost:8060", "") is False


# ── CLI receipt honesty ───────────────────────────────────────────────────


def test_cli_revoke_prints_service_receipt_when_reachable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(auth, "revoke_api_key", lambda tenant, db=None: 1)
    monkeypatch.setattr(auth, "_revoke_via_service", lambda tenant, url, tok: True)
    monkeypatch.setattr(sys, "argv", ["auth.py", "revoke", "--tenant", "acme"])

    rc = auth._cli()
    assert rc == 0
    out = capsys.readouterr().out
    assert "cache_cleared=service" in out
    assert "cache_cleared=true" not in out  # the old false receipt must never print


def test_cli_revoke_prints_honest_local_receipt_when_service_unreachable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(auth, "revoke_api_key", lambda tenant, db=None: 1)
    monkeypatch.setattr(auth, "_revoke_via_service", lambda tenant, url, tok: False)
    monkeypatch.setattr(sys, "argv", ["auth.py", "revoke", "--tenant", "acme"])

    rc = auth._cli()
    assert rc == 0
    out = capsys.readouterr().out
    assert "cache_cleared=LOCAL-PROCESS-ONLY" in out
    assert "cache_cleared=true" not in out
    assert "30s" in out  # names the real exposure window, not a silent lie
