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
import time
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
    # P2-C/P2-G rider: the revoke rate-limit guard is process-global state,
    # same shape as the token cache — reset it around every test in this
    # file so test order/timing can never leak a throttle into an unrelated
    # test. P2-G (sos-205-790a2a63 gate-4) made this a per-tenant dict
    # instead of one global float.
    app_module._LAST_REVOKE_FLUSH_TS = {}
    yield
    auth._token_cache_clear()
    auth._TOKEN_CACHE_INFLIGHT.clear()
    app_module._LAST_REVOKE_FLUSH_TS = {}


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
        "cache_flushed": True,
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


# ── P2-C/P2-G: revoke cache-flush throttle (sos-205-47f5f8c2 gate-3,
# hardened sos-205-790a2a63 gate-4) ───────────────────────────────────────
# revoke_api_key() clears the WHOLE in-process token cache (both pools, all
# tenants) on every call. The gate-3 verdict measured a single ~6ms revoke
# forcing ~7757x cost onto every other live client's next lookup, and
# observed the brain's capability-gate roster fetch (a 5s hardcoded timeout)
# lose that race and degrade to its static fallback during the same window.
# This is a blunt min-interval guard, not the durable fix (per-tenant cache
# invalidation via an indexed fingerprint column — sos#206).
#
# gate-4 P2-G found the ORIGINAL throttle was a single global timestamp
# checked BEFORE the DB delete: a 429 for tenant B's revoke — caused purely
# by tenant A revoking 3s earlier — aborted the whole request and left
# tenant B's key rows fully present. Fixed: the delete always runs; only the
# cache flush is throttled, per tenant; a throttled flush is a 200 with
# `cache_flushed: false`, never a bare 429.


def _revoke_rate_limit_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, dict[str, str]]:
    db_path = tmp_path / "revoke_rate.db"
    database = SquadDB(db_path=db_path)
    with database.connect() as conn:
        conn.execute(_DDL)
    monkeypatch.setattr(app_module, "_SYSTEM_BEARERS", {"sys-tok-test"})
    monkeypatch.setattr(app_module, "SquadDB", lambda: database)
    return TestClient(app_module.app), {"Authorization": "Bearer sys-tok-test"}


def test_auth_revoke_route_second_rapid_call_flush_throttled_not_429(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client, headers = _revoke_rate_limit_fixture(tmp_path, monkeypatch)

    first = client.post("/auth/revoke", json={"tenant_id": "acme"}, headers=headers)
    assert first.status_code == 200
    assert first.json()["cache_flushed"] is True

    second = client.post("/auth/revoke", json={"tenant_id": "acme"}, headers=headers)
    assert second.status_code == 200  # P2-G: never a bare 429
    body = second.json()
    assert body["cache_flushed"] is False
    assert body["retry_after"] > 0
    assert "warning" in body
    assert "TTL" in body["warning"]


def test_auth_revoke_route_delete_always_runs_even_when_flush_throttled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The DB delete must happen on EVERY call, independent of the flush
    throttle — a revoked tenant's key rows must never survive a throttled
    call. Mint two keys for the same tenant so the second revoke call still
    has a row to delete and its `revoked_rows` count proves the DELETE ran."""
    db_path = tmp_path / "revoke_delete_always.db"
    database = SquadDB(db_path=db_path)
    with database.connect() as conn:
        conn.execute(_DDL)
    monkeypatch.setattr(app_module, "_SYSTEM_BEARERS", {"sys-tok-test"})
    monkeypatch.setattr(app_module, "SquadDB", lambda: database)
    client = TestClient(app_module.app)
    headers = {"Authorization": "Bearer sys-tok-test"}

    auth.create_api_key("acme", "agent", db=database)
    first = client.post("/auth/revoke", json={"tenant_id": "acme"}, headers=headers)
    assert first.status_code == 200
    assert first.json()["revoked_rows"] == 1

    auth.create_api_key("acme", "agent", db=database)  # a fresh row to prove the 2nd delete ran
    second = client.post("/auth/revoke", json={"tenant_id": "acme"}, headers=headers)
    assert second.status_code == 200
    assert second.json()["cache_flushed"] is False  # throttled...
    assert second.json()["revoked_rows"] == 1  # ...but the delete still ran

    with database.connect() as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) AS n FROM api_keys WHERE tenant_id = ?", ("acme",)
        ).fetchone()
    assert remaining["n"] == 0


def test_auth_revoke_route_throttle_is_per_tenant_not_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """gate-4 P2-G: a 429/throttle for tenant A's flush must never abort or
    look like it aborted a DIFFERENT tenant's revoke."""
    db_path = tmp_path / "revoke_per_tenant.db"
    database = SquadDB(db_path=db_path)
    with database.connect() as conn:
        conn.execute(_DDL)
    monkeypatch.setattr(app_module, "_SYSTEM_BEARERS", {"sys-tok-test"})
    monkeypatch.setattr(app_module, "SquadDB", lambda: database)
    client = TestClient(app_module.app)
    headers = {"Authorization": "Bearer sys-tok-test"}

    auth.create_api_key("tenant-a", "agent", db=database)
    auth.create_api_key("tenant-b", "agent", db=database)

    resp_a = client.post("/auth/revoke", json={"tenant_id": "tenant-a"}, headers=headers)
    assert resp_a.status_code == 200
    assert resp_a.json()["cache_flushed"] is True

    # tenant-b revokes immediately after — its OWN flush clock has never
    # fired, so it must not be throttled by tenant-a's recent flush.
    resp_b = client.post("/auth/revoke", json={"tenant_id": "tenant-b"}, headers=headers)
    assert resp_b.status_code == 200
    assert resp_b.json()["cache_flushed"] is True
    assert resp_b.json()["revoked_rows"] == 1

    with database.connect() as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) AS n FROM api_keys WHERE tenant_id = ?", ("tenant-b",)
        ).fetchone()
    assert remaining["n"] == 0


def test_auth_revoke_route_allows_again_after_interval_elapses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client, headers = _revoke_rate_limit_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(app_module, "_REVOKE_MIN_INTERVAL_S", 0.05)

    first = client.post("/auth/revoke", json={"tenant_id": "acme"}, headers=headers)
    assert first.status_code == 200

    time.sleep(0.1)

    second = client.post("/auth/revoke", json={"tenant_id": "acme"}, headers=headers)
    assert second.status_code == 200
    assert second.json()["cache_flushed"] is True


def test_auth_revoke_route_rate_limit_does_not_bypass_bearer_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The throttle guard must not become a way to probe auth: an
    unauthenticated/wrong-bearer call still gets 401/403 —
    _require_system_bearer runs first."""
    client, _headers = _revoke_rate_limit_fixture(tmp_path, monkeypatch)

    resp = client.post("/auth/revoke", json={"tenant_id": "acme"})
    assert resp.status_code == 401

    resp = client.post(
        "/auth/revoke", json={"tenant_id": "acme"}, headers={"Authorization": "Bearer wrong"}
    )
    assert resp.status_code == 403
