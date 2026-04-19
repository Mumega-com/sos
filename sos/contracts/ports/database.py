"""DatabasePort — relational store (D1, SQLite, Postgres).

Canonical contract shared between SOS (Python) and Inkwell (TypeScript).
Source of truth for how plugins run SQL without touching a specific
driver.

Tenant binding: IMPLICIT. The port is typically bound to a per-tenant
database or schema when the adapter is constructed. Cross-tenant SQL
lives on the richer sos.contracts.storage.SQLStore.

Parameters: positional list of primitives, matching D1's `?`/`$1` style.
Named-param SQL is an adapter-specific extension and not part of this port.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


# --- Request / response models ---------------------------------------------


class QueryRequest(BaseModel):
    """A single SELECT. `params` are positional."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sql: str = Field(min_length=1)
    params: Optional[list[Any]] = None


class ExecuteRequest(BaseModel):
    """A single INSERT / UPDATE / DELETE."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sql: str = Field(min_length=1)
    params: Optional[list[Any]] = None


class ExecuteResult(BaseModel):
    """What we report after a write — `changes` is rows-affected.
    Mirrors Inkwell's `{ changes: number }`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    changes: int = 0
    last_insert_id: Optional[int] = None


class BatchStatement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sql: str = Field(min_length=1)
    params: Optional[list[Any]] = None


class BatchRequest(BaseModel):
    """Atomic multi-statement write."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    statements: list[BatchStatement] = Field(min_length=1)


# Row type — generic JSON object. The TS version uses a generic `T`; Python
# callers can validate the returned dicts into their own Pydantic models.
Row = dict[str, Any]


# --- Port protocol ----------------------------------------------------------


@runtime_checkable
class DatabasePort(Protocol):
    """Relational store. Tenant bound by adapter wiring."""

    async def query(self, req: QueryRequest) -> list[Row]:
        """SELECT — all rows."""
        ...

    async def query_one(self, req: QueryRequest) -> Optional[Row]:
        """SELECT — first row or None."""
        ...

    async def execute(self, req: ExecuteRequest) -> ExecuteResult:
        """INSERT / UPDATE / DELETE — returns `changes` and `last_insert_id`."""
        ...

    async def batch(self, req: BatchRequest) -> None:
        """Run all statements atomically."""
        ...


__all__ = [
    "QueryRequest",
    "ExecuteRequest",
    "ExecuteResult",
    "BatchStatement",
    "BatchRequest",
    "Row",
    "DatabasePort",
]
