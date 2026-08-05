"""sos#213 — detect malformed / duplicated DATABASE_URL without printing secrets."""

from __future__ import annotations

from sos.kernel.database_url_hygiene import (
    find_database_url_defects,
    fingerprint_value,
    inspect_database_url_value,
    libpq_host_guess,
)


def test_libpq_and_urlsplit_disagree_when_password_has_at() -> None:
    # Synthetic — not a live secret. Password contains '@'.
    url = "postgresql://user:p@ss@db.example.supabase.co:5432/postgres"
    meta = inspect_database_url_value(url)
    assert meta["unencoded_at_in_userinfo"] is True
    assert meta["urlsplit_host"] == "db.example.supabase.co"
    assert meta["libpq_first_at_host"] == "ss@db.example.supabase.co:5432"
    assert libpq_host_guess(url) == "ss@db.example.supabase.co:5432"


def test_well_formed_url_has_single_at() -> None:
    url = "postgresql://user:pass@localhost:5432/sos"
    meta = inspect_database_url_value(url)
    assert meta["unencoded_at_in_userinfo"] is False
    assert meta["urlsplit_host"] == "localhost"
    assert meta["libpq_first_at_host"] == "localhost:5432"


def test_duplicate_database_url_keys_reported_by_fingerprint() -> None:
    env_text = "\n".join(
        [
            "DATABASE_URL=postgresql://user:p@ss@db.example.supabase.co:5432/postgres",
            "DATABASE_URL=postgresql://sos:x@localhost:5432/sos",
            "OTHER=1",
        ]
    )
    defects = find_database_url_defects(env_text)
    kinds = {d["kind"] for d in defects}
    assert "unencoded_at_in_userinfo" in kinds
    assert "duplicate_key" in kinds
    dup = next(d for d in defects if d["kind"] == "duplicate_key")
    assert dup["key"] == "DATABASE_URL"
    assert dup["lines"] == [1, 2]
    assert dup["sha12s"] == [
        fingerprint_value("postgresql://user:p@ss@db.example.supabase.co:5432/postgres"),
        fingerprint_value("postgresql://sos:x@localhost:5432/sos"),
    ]


def test_fingerprint_is_stable_and_short() -> None:
    assert fingerprint_value("abc") == fingerprint_value("abc")
    assert len(fingerprint_value("abc")) == 12
