"""DoneCheck — a structured completion-gate entry.

Replaces free-text completion notes with a checklist the squad can verify
mechanically. Each entry is one acceptance criterion: a short ``text``
prompt, a ``done`` boolean, and — once acked — who ticked it and when.

Used by:
- ``sos.contracts.objective.Objective.done_when`` (live today)
- ``sos.contracts.squad.SquadTask.done_when`` (follow-up — requires DB migration)

Per ``docs/plans/2026-04-19-sos-closure-v1.md`` §4 Tier 1 T1.3: the squad
service ``POST /tasks/{id}/complete`` endpoint MUST refuse completion
unless every entry's ``done`` is True. Empty list = no gate (legacy
behaviour preserved).
"""

from __future__ import annotations

from typing import Iterable

from pydantic import BaseModel, Field


class DoneCheck(BaseModel):
    """One acceptance criterion on an Objective or SquadTask."""

    model_config = {"extra": "forbid"}

    id: str = Field(min_length=1, description="Short stable identifier within the parent item")
    text: str = Field(min_length=1, description="Human-readable criterion")
    done: bool = False
    acked_by: str | None = None
    acked_at: str | None = None


def all_done(checks: Iterable[DoneCheck | dict]) -> bool:
    """Return True iff every check's ``done`` is True (vacuously True on empty).

    Accepts both DoneCheck instances and raw dicts so callers that store the
    list as serialized dicts (dataclasses, DB rows) don't have to rehydrate
    just to ask the question.
    """
    for check in checks:
        done = check.done if isinstance(check, DoneCheck) else bool(check.get("done"))
        if not done:
            return False
    return True


__all__ = ["DoneCheck", "all_done"]
