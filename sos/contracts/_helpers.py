"""
Internal helpers for SOS contract layer.

These utilities guard against common psycopg2 mis-use patterns that
psycopg2 silently accepts but Postgres rejects at runtime.
"""
from __future__ import annotations

from typing import Any


def assert_pg_array(value: Any, field_name: str) -> None:
    """Fail fast if a TEXT[] / array column is incorrectly wrapped.

    psycopg2.extras.Json() is for JSONB columns only.  When it wraps a
    list that should go to a TEXT[] column, psycopg2 serialises the list
    as a JSON string (e.g. ``'["a","b"]'``) and Postgres rejects it with
    ``ERROR: malformed array literal``.  This helper raises immediately at
    call time so the bug surfaces with a useful message instead of a
    cryptic DB error.

    Valid values: ``list``, ``tuple``, or ``None``.

    Args:
        value:      The value that will be bound to a TEXT[] parameter.
        field_name: Column / field name used in the error message.

    Raises:
        ValueError: If *value* is a ``psycopg2.extras.Json`` instance or
                    any other type that is not a list, tuple, or None.

    Example::

        from psycopg2.extras import Json
        from sos.contracts._helpers import assert_pg_array

        roles = ["admin", "editor"]
        assert_pg_array(roles, "permitted_roles")   # OK

        assert_pg_array(Json(roles), "permitted_roles")  # raises ValueError
    """
    # Detect Json wrapping without importing psycopg2 at module level so
    # that contracts remain importable in environments without psycopg2
    # (e.g. pure unit-test CI containers).
    try:
        from psycopg2.extras import Json as _Json  # type: ignore[import]
        if isinstance(value, _Json):
            raise ValueError(
                f"Field '{field_name}' must be a list/tuple, not Json-wrapped. "
                "Use a plain list for TEXT[] columns; Json() is for JSONB only."
            )
    except ImportError:
        # psycopg2 not installed — nothing to guard against.
        pass

    if value is None or isinstance(value, (list, tuple)):
        return

    raise ValueError(
        f"Field '{field_name}' must be a list/tuple, not Json-wrapped. "
        f"Got {type(value).__name__!r} instead."
    )
