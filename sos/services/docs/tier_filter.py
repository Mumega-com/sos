"""
Tier enforcement for sos-docs.

Imports from sos.contracts.tiers when available (canonical kernel surface).
Falls back to a self-contained implementation when the kernel module does not
yet exist — the fallback must stay semantically identical so that the service
continues to work while the kernel surfaces that contract.

This is the ONLY enforcement point.  Routes never re-check tiers.

Five-tier Hive visibility model:
  public   — visible to all callers, including unauthenticated (no token needed)
  squad    — visible to callers whose token carries any squad membership claim
  project  — visible to callers whose token carries the matching project_id
  role     — visible to callers whose token carries at least one of permitted_roles[]
  entity   — visible to callers whose token carries the matching entity_id
  private  — visible only to the author (author_id == caller_entity_id)

apply_tier_filter() returns SQL WHERE fragment(s) as a list of strings that are
AND-combined by the caller into a complete WHERE clause.  Parameterised values
are returned separately so callers can pass them safely to the DB driver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Attempt to import from the canonical kernel contract.
# If the module exists it takes precedence; otherwise we fall through to the
# local implementation.
try:
    from sos.contracts.tiers import CallerContext, apply_tier_filter  # type: ignore[import]

    _USING_KERNEL_CONTRACT = True
except ImportError:
    _USING_KERNEL_CONTRACT = False


# ---------------------------------------------------------------------------
# Caller context — identical shape to what sos.contracts.tiers will expose
# ---------------------------------------------------------------------------

if not _USING_KERNEL_CONTRACT:

    @dataclass(frozen=True)
    class CallerContext:
        """Claims extracted from a caller's token.

        Attributes
        ----------
        roles:
            Set of role names granted to this caller (e.g. ``{'coordinator', 'author'}``).
        entity_id:
            The caller's workspace / customer identifier.  ``None`` for
            unauthenticated or cross-tenant system callers.
        project_id:
            The project scope declared in the token.  ``None`` if not present.
        squad_ids:
            Squads the caller belongs to.  Used to unlock ``squad``-tier nodes.
        is_system:
            System tokens bypass tier gating (all nodes visible).
        """

        roles: frozenset[str] = field(default_factory=frozenset)
        entity_id: str | None = None
        project_id: str | None = None
        squad_ids: frozenset[str] = field(default_factory=frozenset)
        is_system: bool = False

    def apply_tier_filter(
        caller: CallerContext,
        table_alias: str = "n",
    ) -> tuple[str, list[Any]]:
        """Return a SQL WHERE fragment and its bound parameters.

        Parameters
        ----------
        caller:
            Claims for the requesting caller.
        table_alias:
            SQL alias for the ``docs_nodes`` table (default ``"n"``).

        Returns
        -------
        (where_fragment, params)
            ``where_fragment`` is a parenthesised OR expression that can be
            ANDed into a larger WHERE clause.
            ``params`` is an ordered list of positional bind values matching
            the ``%s`` placeholders in the fragment.

        Notes
        -----
        - System callers see everything — return ``('TRUE', [])``.
        - Unauthenticated callers see only ``public`` nodes.
        - A node is visible when *at least one* tier condition is satisfied.
        """

        a = table_alias

        # System tokens bypass all tier checks.
        if caller.is_system:
            return "TRUE", []

        clauses: list[str] = []
        params: list[Any] = []

        # public — always visible when authenticated
        clauses.append(f"{a}.tier = 'public'")

        # squad — caller has any squad membership
        if caller.squad_ids:
            squad_list = list(caller.squad_ids)
            placeholders = ", ".join(["%s"] * len(squad_list))
            clauses.append(f"({a}.tier = 'squad' AND {a}.squad_id IN ({placeholders}))")
            params.extend(squad_list)

        # project — token carries matching project_id
        if caller.project_id:
            clauses.append(f"({a}.tier = 'project' AND {a}.project_id = %s)")
            params.append(caller.project_id)

        # role — caller holds at least one permitted role
        if caller.roles:
            role_list = list(caller.roles)
            placeholders = ", ".join(["%s"] * len(role_list))
            clauses.append(
                f"({a}.tier = 'role' AND {a}.permitted_roles && ARRAY[{placeholders}]::TEXT[])"
            )
            params.extend(role_list)

        # entity — token carries matching entity_id
        if caller.entity_id:
            clauses.append(f"({a}.tier = 'entity' AND {a}.entity_id = %s)")
            params.append(caller.entity_id)

            # private — only visible to the author themselves
            clauses.append(f"({a}.tier = 'private' AND {a}.author_id = %s)")
            params.append(caller.entity_id)

        where = "(" + " OR ".join(clauses) + ")"
        return where, params


def is_coordinator_or_author(caller: CallerContext) -> bool:
    """Return True when the caller may write nodes or modify tiers.

    Coordinators may do anything.  Authors may create nodes but cannot
    promote/demote tiers (that check is separate).
    """
    if caller.is_system:
        return True
    return bool(caller.roles & {"coordinator", "author"})


def is_coordinator(caller: CallerContext) -> bool:
    """Return True when the caller has coordinator-level access."""
    if caller.is_system:
        return True
    return "coordinator" in caller.roles
