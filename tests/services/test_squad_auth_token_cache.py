"""Squad auth token-verification cache — 2026-07-27 incident regression tests.

The incident: _lookup_token bcrypt-checked the presented token against every
api_keys row synchronously on the event loop (~5s of CPU with 17 bcrypt rows).
Timeout-retrying clients turned that into congestion collapse: the service
pegged a core, stopped answering, and restarts replayed the same load.

These tests lock the fix, mutation-style: each one fails if the specific
mechanism it names is deleted.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

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
    auth._TOKEN_CACHE.clear()
    yield
    auth._TOKEN_CACHE.clear()


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

    key = auth._token_cache_key("tok-alpha")
    expires_at, snapshot = auth._TOKEN_CACHE[key]
    auth._TOKEN_CACHE[key] = (expires_at - auth._TOKEN_CACHE_POSITIVE_TTL_S - 1, snapshot)

    assert auth._lookup_token("tok-alpha", db) is not None
    assert db.connect_count > before, "expired entry must rescan"


def test_cache_never_stores_raw_token(db: CountingSquadDB):
    _insert_key(db, "tok-alpha")
    auth._lookup_token("tok-alpha", db)
    for cache_key, (_, snapshot) in auth._TOKEN_CACHE.items():
        assert "tok-alpha" not in cache_key
        if snapshot is not None:
            assert "tok-alpha" not in str(snapshot)


def test_cache_bounded(db: CountingSquadDB):
    for i in range(auth._TOKEN_CACHE_MAX + 20):
        auth._token_cache_put(f"key-{i}", None)
    assert len(auth._TOKEN_CACHE) <= auth._TOKEN_CACHE_MAX


def test_create_api_key_clears_negative_entry(db: CountingSquadDB, monkeypatch: pytest.MonkeyPatch):
    # A pre-mint probe with the future token leaves a negative entry; the
    # mint must clear it so the fresh key authenticates immediately.
    minted = {}
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
    assert auth._TOKEN_CACHE == {}
