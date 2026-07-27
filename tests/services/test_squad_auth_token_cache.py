"""Squad auth token-verification cache — 2026-07-27 incident regression tests.

The incident: _lookup_token bcrypt-checked the presented token against every
api_keys row synchronously on the event loop (~5s of CPU with 17 bcrypt rows).
Timeout-retrying clients turned that into congestion collapse: the service
pegged a core, stopped answering, and restarts replayed the same load.

These tests lock the fix, mutation-style: each one fails if the specific
mechanism it names is deleted.

Hardened 2026-07-27 against the sos-205-a7c2fc44 adversarial gate verdict
(/home/mumega/mupot-worktrees/_gate-verdicts/sos-205-a7c2fc44-adversarial.md):
BLOCK-2 (revocation), BLOCK-3 (positive/negative pool isolation), and a
cross-thread lock smoke test for BLOCK-1's thread offload.
"""
from __future__ import annotations

import concurrent.futures
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sos.services.squad import app as app_module
from sos.services.squad import auth
from sos.services.squad.service import SquadDB


class CountingSquadDB(SquadDB):
    """SquadDB that counts connect() calls — a cache hit must not connect."""

    def __init__(self, db_path: Path):
        super().__init__(db_path)
        self.connect_count = 0

    def connect(self) -> sqlite3.Connection:
        self.connect_count += 1
        return super().connect()


@pytest.fixture()
def db(tmp_path: Path) -> CountingSquadDB:
    database = CountingSquadDB(tmp_path / "squads.db")
    with database.connect() as conn:
        conn.execute(
            """
            CREATE TABLE api_keys (
                token_hash TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                identity_type TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
    database.connect_count = 0
    return database


@pytest.fixture(autouse=True)
def _fresh_cache():
    auth._token_cache_clear()
    auth._TOKEN_CACHE_INFLIGHT.clear()
    yield
    auth._token_cache_clear()
    auth._TOKEN_CACHE_INFLIGHT.clear()


def _insert_key(db: SquadDB, token: str, tenant_id: str = "t1", identity_type: str = "agent") -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO api_keys (token_hash, tenant_id, identity_type, created_at) VALUES (?, ?, ?, ?)",
            (auth.hash_token(token), tenant_id, identity_type, "2026-07-27T00:00:00Z"),
        )


def test_valid_token_cached_no_second_scan(db: CountingSquadDB):
    _insert_key(db, "tok-alpha")
    db.connect_count = 0

    first = auth._lookup_token("tok-alpha", db)
    assert first is not None and first.tenant_id == "t1"
    scans_for_first = db.connect_count
    assert scans_for_first >= 1

    second = auth._lookup_token("tok-alpha", db)
    assert second is not None and second.tenant_id == "t1"
    assert db.connect_count == scans_for_first, "cache hit must not touch the DB"
    # Identity rebuilt from snapshot matches the row path
    assert second.identity.metadata["tenant_id"] == "t1"
    assert second.identity.metadata["identity_type"] == "agent"


def test_invalid_token_negative_cached(db: CountingSquadDB):
    _insert_key(db, "tok-alpha")
    db.connect_count = 0

    assert auth._lookup_token("tok-wrong", db) is None
    scans_for_first = db.connect_count
    assert auth._lookup_token("tok-wrong", db) is None
    assert db.connect_count == scans_for_first, "negative hit must not rescan"


def test_expired_entry_is_a_miss(db: CountingSquadDB, monkeypatch: pytest.MonkeyPatch):
    _insert_key(db, "tok-alpha")
    assert auth._lookup_token("tok-alpha", db) is not None
    before = db.connect_count

    key = auth._token_cache_key("tok-alpha", db)
    expires_at, snapshot = auth._TOKEN_CACHE_POSITIVE[key]
    auth._TOKEN_CACHE_POSITIVE[key] = (expires_at - auth._TOKEN_CACHE_POSITIVE_TTL_S - 1, snapshot)

    assert auth._lookup_token("tok-alpha", db) is not None
    assert db.connect_count > before, "expired entry must rescan"


def test_cache_never_stores_raw_token(db: CountingSquadDB):
    _insert_key(db, "tok-alpha")
    auth._lookup_token("tok-alpha", db)
    for pool in (auth._TOKEN_CACHE_POSITIVE, auth._TOKEN_CACHE_NEGATIVE):
        for cache_key, (_, snapshot) in pool.items():
            assert "tok-alpha" not in cache_key
            if snapshot is not None:
                assert "tok-alpha" not in str(snapshot)


def test_cache_bounded(db: CountingSquadDB):
    for i in range(auth._TOKEN_CACHE_POSITIVE_MAX + 20):
        auth._token_cache_put(f"pos-key-{i}", {"tenant_id": "t", "identity_type": "agent"})
    assert len(auth._TOKEN_CACHE_POSITIVE) <= auth._TOKEN_CACHE_POSITIVE_MAX

    for i in range(auth._TOKEN_CACHE_NEGATIVE_MAX + 20):
        auth._token_cache_put(f"neg-key-{i}", None)
    assert len(auth._TOKEN_CACHE_NEGATIVE) <= auth._TOKEN_CACHE_NEGATIVE_MAX


def test_create_api_key_clears_negative_entry(db: CountingSquadDB, monkeypatch: pytest.MonkeyPatch):
    # A pre-mint probe with the future token leaves a negative entry; the
    # mint must clear it so the fresh key authenticates immediately.
    real_token_hex = auth.secrets.token_hex

    def fixed_hex(n: int) -> str:
        return "f" * (2 * n)

    monkeypatch.setattr(auth.secrets, "token_hex", fixed_hex)
    predicted = f"sk-squad-t9-{fixed_hex(16)}"
    assert auth._lookup_token(predicted, db) is None  # negative-cached

    token, _created = auth.create_api_key("t9", "agent", db=db)
    assert token == predicted
    ctx = auth._lookup_token(token, db)
    assert ctx is not None and ctx.tenant_id == "t9"


def test_system_token_bypasses_cache_and_db(db: CountingSquadDB, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth, "SYSTEM_TOKEN", "sys-tok")
    db.connect_count = 0
    ctx = auth._lookup_token("sys-tok", db)
    assert ctx is not None and ctx.is_system
    assert db.connect_count == 0
    assert auth._TOKEN_CACHE_POSITIVE == {}
    assert auth._TOKEN_CACHE_NEGATIVE == {}


# ── BLOCK-2: revocation ────────────────────────────────────────────────────


def test_revoke_api_key_invalidates_immediately(db: CountingSquadDB):
    token, _created = auth.create_api_key("fired-contractor", "agent", db=db)
    ctx = auth._lookup_token(token, db)
    assert ctx is not None and ctx.tenant_id == "fired-contractor"  # cached positive

    deleted = auth.revoke_api_key("fired-contractor", db=db)
    assert deleted == 1

    # No stale-validity window: the very next lookup must fail, not just the
    # one after the (dropped) 300s/30s TTL.
    assert auth._lookup_token(token, db) is None


def test_revoke_api_key_clears_unrelated_cached_entries_too(db: CountingSquadDB):
    # Whole-cache clear is the documented, correct trade-off (raw tokens are
    # never stored, so per-entry targeting is impossible) — assert it really
    # does clear entries for OTHER tenants, not just the revoked one.
    victim_token, _ = auth.create_api_key("bystander", "agent", db=db)
    auth._lookup_token(victim_token, db)  # populate a positive entry
    assert auth._TOKEN_CACHE_POSITIVE  # sanity: something is cached

    fired_token, _ = auth.create_api_key("fired-contractor", "agent", db=db)
    auth._lookup_token(fired_token, db)
    auth.revoke_api_key("fired-contractor", db=db)

    assert auth._TOKEN_CACHE_POSITIVE == {}
    assert auth._TOKEN_CACHE_NEGATIVE == {}
    # The bystander's row is untouched in the DB, just re-scanned once more.
    ctx = auth._lookup_token(victim_token, db)
    assert ctx is not None and ctx.tenant_id == "bystander"


def test_revoke_api_key_only_deletes_named_tenant(db: CountingSquadDB):
    _insert_key(db, "tok-keep", tenant_id="keep-me")
    _insert_key(db, "tok-gone", tenant_id="fired-contractor")

    deleted = auth.revoke_api_key("fired-contractor", db=db)
    assert deleted == 1
    assert auth._lookup_token("tok-gone", db) is None
    assert auth._lookup_token("tok-keep", db) is not None


# ── BLOCK-3: positive/negative pool isolation ───────────────────────────────


def test_eviction_pool_isolation_negatives_cannot_evict_positives(db: CountingSquadDB, monkeypatch: pytest.MonkeyPatch):
    # Real bcrypt scans are ~0.3-1.2s each on this host (see the adversarial
    # verdict's own measurements); spraying the real 192-entry negative cap
    # would mean 200+ full-table bcrypt scans in one test. Shrink the caps
    # for this test only — the eviction-isolation MECHANISM being locked
    # (negatives can only evict negatives) does not depend on the cap size.
    monkeypatch.setattr(auth, "_TOKEN_CACHE_NEGATIVE_MAX", 5)

    # Mint and cache a real, live positive entry via the actual DB + bcrypt
    # path (not a synthetic _token_cache_put) so this exercises the real
    # _lookup_token miss/hit flow, matching the adversarial probe's B2/B3.
    token, _created = auth.create_api_key("victim-tenant", "agent", db=db)
    ctx = auth._lookup_token(token, db)
    assert ctx is not None
    assert len(auth._TOKEN_CACHE_POSITIVE) == 1

    # Spray more unique bad tokens than the (shrunk) negative pool cap.
    for i in range(auth._TOKEN_CACHE_NEGATIVE_MAX + 10):
        auth._lookup_token(f"attacker-spray-{i}", db)

    assert len(auth._TOKEN_CACHE_NEGATIVE) <= auth._TOKEN_CACHE_NEGATIVE_MAX
    # The legit positive entry must have survived the negative spray —
    # a single shared pool (the pre-fix design) would have evicted it.
    assert len(auth._TOKEN_CACHE_POSITIVE) == 1
    db.connect_count = 0
    assert auth._lookup_token(token, db) is not None
    assert db.connect_count == 0, "surviving positive entry must still be a cache hit"


def test_positive_pool_capped_independently(db: CountingSquadDB):
    for i in range(auth._TOKEN_CACHE_POSITIVE_MAX + 10):
        auth._token_cache_put(f"pos-{i}", {"tenant_id": f"t{i}", "identity_type": "agent"})
    assert len(auth._TOKEN_CACHE_POSITIVE) <= auth._TOKEN_CACHE_POSITIVE_MAX
    # Negative pool is untouched by positive traffic.
    assert auth._TOKEN_CACHE_NEGATIVE == {}


# ── BLOCK-1: cross-thread lock smoke test ───────────────────────────────────


def test_concurrent_lookups_do_not_corrupt_cache(db: CountingSquadDB):
    # _lookup_token now runs off the event loop via anyio.to_thread.run_sync
    # in require_capability, which makes _TOKEN_CACHE_POSITIVE/_NEGATIVE
    # genuinely shared mutable state across threads. This does not prove the
    # lock is sufficient under every interleaving, but it is a real smoke
    # test: 50 concurrent lookups (mixed valid/invalid tokens) from a thread
    # pool must all resolve correctly and must not raise (e.g. a
    # "dictionary changed size during iteration" from the unguarded
    # min()-over-dict eviction scan).
    _insert_key(db, "tok-shared", tenant_id="concurrent-tenant")

    def _lookup(i: int):
        token = "tok-shared" if i % 2 == 0 else f"tok-bad-{i}"
        return token, auth._lookup_token(token, db)

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(_lookup, range(50)))

    for token, ctx in results:
        if token == "tok-shared":
            assert ctx is not None and ctx.tenant_id == "concurrent-tenant"
        else:
            assert ctx is None

    # Lock must exist and actually be a lock (mutation-check: a
    # threading.Lock instance, not e.g. a no-op placeholder).
    assert isinstance(auth._TOKEN_CACHE_LOCK, type(threading.Lock()))


# ── BLOCK-1b: single-flight (sos-205-b5307dd7 re-gate) ──────────────────────
# The re-gate verdict measured 8 concurrent replays of ONE cold token costing
# 8 full-table scans (vs 1 when replayed sequentially) — the cache-miss check
# and the scan were not atomic w.r.t. each other, so every concurrent caller
# raced to scan before any of them had cached a result.


def test_single_flight_one_scan_for_concurrent_replays_of_same_token(
    db: CountingSquadDB, monkeypatch: pytest.MonkeyPatch
):
    """Mutation-style: fails if _TOKEN_CACHE_INFLIGHT / its lookup in
    _lookup_token is removed. The scan is artificially slowed so all 8
    ThreadPoolExecutor workers are reliably in-flight before the leader
    finishes — otherwise this could pass by accident on a fast machine even
    without the fix (a follower scheduled after the leader already cached
    the result would just get a legitimate, unrelated cache hit)."""
    _insert_key(db, "tok-hot", tenant_id="t1")

    real_scan = auth._scan_and_cache

    def _slow_scan(token: str, database: SquadDB, cache_key: str):
        time.sleep(0.2)
        return real_scan(token, database, cache_key)

    monkeypatch.setattr(auth, "_scan_and_cache", _slow_scan)
    db.connect_count = 0

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _i: auth._lookup_token("tok-hot", db), range(8)))

    assert db.connect_count == 1, (
        f"expected exactly 1 DB scan for 8 concurrent replays of one cold "
        f"token, got {db.connect_count}"
    )
    for ctx in results:
        assert ctx is not None and ctx.tenant_id == "t1"


def test_single_flight_propagates_scan_exception_to_followers(
    db: CountingSquadDB, monkeypatch: pytest.MonkeyPatch
):
    """A DB error during the leader's scan must reach every follower waiting
    on the same Future, not hang them or silently return None."""

    def _boom(token: str, database: SquadDB, cache_key: str):
        time.sleep(0.1)
        raise RuntimeError("simulated scan failure")

    monkeypatch.setattr(auth, "_scan_and_cache", _boom)

    def _lookup(_i: int):
        try:
            auth._lookup_token("tok-error", db)
            return "no-error"
        except RuntimeError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(_lookup, range(4)))

    assert results == ["simulated scan failure"] * 4
    # The in-flight entry must be evicted on failure too, or every future
    # lookup of this token would hang forever waiting on a dead Future.
    assert auth._token_cache_key("tok-error", db) not in auth._TOKEN_CACHE_INFLIGHT


# ── WARN-1: cache keyed by db (sos-205-b5307dd7 re-gate) ────────────────────


def test_cache_key_scoped_by_db(tmp_path: Path):
    """The cache used to be keyed on the token alone, so a token that only
    exists in DB A would authenticate against DB B via a shared cache entry
    without DB B ever being queried. _lookup_token(token, db) and
    revoke_api_key(tenant, db=...) both advertise a per-db contract; the
    cache must honor it."""
    db_a = CountingSquadDB(tmp_path / "a.db")
    db_b = CountingSquadDB(tmp_path / "b.db")
    ddl = """
        CREATE TABLE api_keys (
            token_hash TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            identity_type TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """
    for database in (db_a, db_b):
        with database.connect() as conn:
            conn.execute(ddl)
        database.connect_count = 0

    _insert_key(db_a, "tok-only-in-a", tenant_id="tenant-a")

    assert auth._lookup_token("tok-only-in-a", db_a) is not None

    # Same token presented against DB B: must be a genuine miss (DB B gets
    # queried), never a hit served from DB A's cache entry.
    db_b.connect_count = 0
    assert auth._lookup_token("tok-only-in-a", db_b) is None
    assert db_b.connect_count >= 1, "cache must not be shared across distinct SquadDB instances"

    assert auth._token_cache_key("tok-only-in-a", db_a) != auth._token_cache_key("tok-only-in-a", db_b)


# ── P0-A: fail-closed SYSTEM_TOKEN (sos-205-47f5f8c2 gate-3) ────────────────
# `token == SYSTEM_TOKEN` with SYSTEM_TOKEN defaulting to "" (unset env)
# matched an empty presented token — i.e. NO Authorization header at all —
# and granted system:sos, unrestricted-cross-tenant access. Fixed to
# `SYSTEM_TOKEN and hmac.compare_digest(token, SYSTEM_TOKEN)`: the branch
# cannot fire at all while no token is configured.


def test_empty_system_token_does_not_match_empty_presented_token(db: CountingSquadDB, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth, "SYSTEM_TOKEN", "")
    assert auth._lookup_token("", db) is None


def test_empty_system_token_does_not_match_anything(db: CountingSquadDB, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth, "SYSTEM_TOKEN", "")
    assert auth._lookup_token("some-random-token", db) is None
    assert auth._lookup_token("", db) is None


_ROLE_DDL = """
    CREATE TABLE roles (
        id          TEXT PRIMARY KEY,
        project_id  TEXT NOT NULL,
        tenant_id   TEXT NOT NULL DEFAULT 'default',
        name        TEXT NOT NULL,
        description TEXT,
        created_at  TEXT NOT NULL,
        rank        INTEGER NOT NULL DEFAULT 0,
        UNIQUE(project_id, name, tenant_id)
    );
    CREATE TABLE role_permissions (
        role_id    TEXT NOT NULL,
        permission TEXT NOT NULL,
        PRIMARY KEY (role_id, permission)
    );
    CREATE TABLE role_assignments (
        role_id       TEXT NOT NULL,
        assignee_id   TEXT NOT NULL,
        assignee_type TEXT NOT NULL DEFAULT 'agent',
        assigned_at   TEXT NOT NULL,
        assigned_by   TEXT NOT NULL,
        PRIMARY KEY (role_id, assignee_id)
    );
"""


@pytest.fixture()
def http_client(db: CountingSquadDB, monkeypatch: pytest.MonkeyPatch):
    """A TestClient wired at the throwaway `db` fixture, for the
    `_parse_bearer` route surface (the 34 routes P0-A actually affects)."""
    with db.connect() as conn:
        conn.executescript(_ROLE_DDL)
    monkeypatch.setattr(app_module, "SquadDB", lambda: db)
    monkeypatch.setattr(app_module._role_svc, "db", db)
    return TestClient(app_module.app)


def test_p0a_empty_env_no_header_is_401(http_client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth, "SYSTEM_TOKEN", "")
    resp = http_client.get("/me/roles")
    assert resp.status_code == 401


def test_p0a_empty_env_empty_bearer_is_401(http_client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth, "SYSTEM_TOKEN", "")
    resp = http_client.get("/me/roles", headers={"Authorization": "Bearer "})
    assert resp.status_code == 401


def test_p0a_set_env_correct_token_passes(http_client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth, "SYSTEM_TOKEN", "the-real-system-token")
    resp = http_client.get("/me/roles", headers={"Authorization": "Bearer the-real-system-token"})
    assert resp.status_code == 200


def test_p0a_set_env_wrong_token_is_401(http_client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth, "SYSTEM_TOKEN", "the-real-system-token")
    resp = http_client.get("/me/roles", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401


# ── LOW-1: bounded follower wait (sos-205-47f5f8c2 gate-3) ──────────────────


def test_follower_timeout_evicts_and_raises(db: CountingSquadDB, monkeypatch: pytest.MonkeyPatch):
    """A follower waiting on a stalled leader must not hang forever. Timeout
    shortened for test speed; asserts both the raise AND that the inflight
    entry is evicted so the NEXT caller isn't stuck behind the same dead
    wait either."""
    monkeypatch.setattr(auth, "_TOKEN_CACHE_INFLIGHT_TIMEOUT_S", 0.2)
    _insert_key(db, "tok-stall", tenant_id="t1")

    release_leader = threading.Event()
    real_scan = auth._scan_and_cache

    def _stalling_scan(token: str, database: SquadDB, cache_key: str):
        release_leader.wait(timeout=5)
        return real_scan(token, database, cache_key)

    monkeypatch.setattr(auth, "_scan_and_cache", _stalling_scan)

    leader_started = threading.Event()

    def _leader() -> None:
        leader_started.set()
        auth._lookup_token("tok-stall", db)

    leader_thread = threading.Thread(target=_leader)
    leader_thread.start()
    leader_started.wait(timeout=2)
    time.sleep(0.05)  # let the leader register its Future before we follow

    with pytest.raises(concurrent.futures.TimeoutError):
        auth._lookup_token("tok-stall", db)

    key = auth._token_cache_key("tok-stall", db)
    assert key not in auth._TOKEN_CACHE_INFLIGHT, "timed-out follower must evict, not leave the key wedged"

    release_leader.set()
    leader_thread.join(timeout=5)
