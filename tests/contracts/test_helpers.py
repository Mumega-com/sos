"""
Tests for sos.contracts._helpers — assert_pg_array fail-fast guard.

These are pure unit tests; no DB or network access required.

Run:
    pytest tests/contracts/test_helpers.py -v
"""
from __future__ import annotations

import pytest
import psycopg2.extras

from sos.contracts._helpers import assert_pg_array


# ── passing cases ──────────────────────────────────────────────────────────────

def test_list_passes() -> None:
    """Plain list is a valid TEXT[] binding — must not raise."""
    assert_pg_array(["admin", "editor"], "permitted_roles")


def test_tuple_passes() -> None:
    """Tuple is also a valid TEXT[] binding."""
    assert_pg_array(("admin",), "permitted_roles")


def test_none_passes() -> None:
    """None is valid — maps to SQL NULL."""
    assert_pg_array(None, "permitted_roles")


def test_empty_list_passes() -> None:
    """Empty list is a valid TEXT[] binding."""
    assert_pg_array([], "tags")


def test_empty_tuple_passes() -> None:
    """Empty tuple is a valid TEXT[] binding."""
    assert_pg_array((), "tags")


# ── failing cases ──────────────────────────────────────────────────────────────

def test_json_wrapped_list_raises() -> None:
    """psycopg2.extras.Json([]) is the classic mis-wrapping — must raise."""
    bad = psycopg2.extras.Json([])
    with pytest.raises(ValueError) as exc_info:
        assert_pg_array(bad, "permitted_roles")
    assert "permitted_roles" in str(exc_info.value)
    assert "list/tuple" in str(exc_info.value)
    assert "Json-wrapped" in str(exc_info.value)


def test_json_wrapped_nonempty_raises() -> None:
    """Json([...]) with content still raises."""
    bad = psycopg2.extras.Json(["admin", "editor"])
    with pytest.raises(ValueError) as exc_info:
        assert_pg_array(bad, "scopes")
    assert "scopes" in str(exc_info.value)


def test_string_raises() -> None:
    """A bare string (e.g. '{"a","b"}') must not silently pass."""
    with pytest.raises(ValueError) as exc_info:
        assert_pg_array('{"admin"}', "permitted_roles")
    assert "permitted_roles" in str(exc_info.value)


def test_dict_raises() -> None:
    """A dict is for JSONB, not TEXT[] — must raise."""
    with pytest.raises(ValueError) as exc_info:
        assert_pg_array({"role": "admin"}, "roles")
    assert "roles" in str(exc_info.value)


def test_int_raises() -> None:
    """Integer is never a valid TEXT[] value."""
    with pytest.raises(ValueError):
        assert_pg_array(42, "permitted_roles")


# ── field_name appears in all error messages ───────────────────────────────────

@pytest.mark.parametrize("bad_value", [
    psycopg2.extras.Json([]),
    "bad",
    {"x": 1},
    123,
])
def test_field_name_in_error(bad_value: object) -> None:
    """field_name must always appear in the ValueError message."""
    sentinel = "my_special_column"
    with pytest.raises(ValueError) as exc_info:
        assert_pg_array(bad_value, sentinel)
    assert sentinel in str(exc_info.value)
