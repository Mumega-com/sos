"""Glass layer — no LLM in render path.

Tile declares a query; SOS resolves server-side; Inkwell renders via template.

Each Tile defines *what data* to fetch (a TileQuery) and *how to display it*
(a TileTemplate). The /glass service evaluates the query at request time and
returns a TilePayload that Inkwell consumes without calling any model.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Query variants (discriminated union on "kind")
# ---------------------------------------------------------------------------


class SqlQuery(BaseModel):
    """Execute parameterized SQL against a named service's database."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["sql"]
    service: str = Field(description="Target service name, e.g. 'economy'.")
    statement: str = Field(description="Parameterized SQL, e.g. 'SELECT * FROM t WHERE id=:id'.")
    params: dict[str, Any] = Field(default_factory=dict)


class BusTailQuery(BaseModel):
    """Read the most recent N entries from a Redis stream."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["bus_tail"]
    stream: str = Field(description="Redis stream name, e.g. 'audit:decisions:acme'.")
    limit: int = Field(default=20, ge=1, le=100)


class HttpQuery(BaseModel):
    """Proxy a GET request to another SOS service."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["http"]
    service: str = Field(description="Target service name, e.g. 'registry'.")
    path: str = Field(description="Path starting with '/', e.g. '/squad/acme/status'.")
    method: Literal["GET"] = "GET"

    @classmethod
    def __get_validators__(cls):  # type: ignore[override]
        yield cls.validate

    @classmethod
    def validate(cls, v: Any) -> "HttpQuery":
        instance = cls.model_validate(v) if isinstance(v, dict) else v
        if not instance.path.startswith("/"):
            raise ValueError("HttpQuery.path must start with '/'")
        return instance


# Tagged union — FastAPI/Pydantic v2 discriminated union on the "kind" field.
TileQuery = Annotated[
    Union[SqlQuery, BusTailQuery, HttpQuery],
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# Template enum
# ---------------------------------------------------------------------------


class TileTemplate(str, Enum):
    NUMBER = "number"
    SPARKLINE = "sparkline"
    PROGRESS_BAR = "progress_bar"
    EVENT_LOG = "event_log"
    STATUS_LIGHT = "status_light"
    CHART = "chart"


# ---------------------------------------------------------------------------
# Core domain models
# ---------------------------------------------------------------------------


class Tile(BaseModel):
    """A single dashboard tile declaration."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        pattern=r"^[a-z0-9-]+$",
        description="Slug — lowercase alphanumeric + hyphens only.",
    )
    title: str = Field(min_length=1, max_length=80)
    query: TileQuery
    template: TileTemplate
    refresh_interval_s: int = Field(default=60, ge=5, le=3600)
    tenant: str


class TilePayload(BaseModel):
    """Resolved tile data returned by GET /glass/payload/{tenant}/{tile_id}."""

    model_config = ConfigDict(extra="forbid")

    tile_id: str
    rendered_at: datetime = Field(description="UTC timestamp of resolution.")
    data: dict[str, Any]
    cache_ttl_s: int


class TileMintRequest(BaseModel):
    """Request body for POST /glass/tiles/{tenant}.

    Mirrors Tile minus the ``tenant`` field — the tenant is derived from the
    URL path parameter.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9-]+$")
    title: str = Field(min_length=1, max_length=80)
    query: TileQuery
    template: TileTemplate
    refresh_interval_s: int = Field(default=60, ge=5, le=3600)


__all__ = [
    "SqlQuery",
    "BusTailQuery",
    "HttpQuery",
    "TileQuery",
    "TileTemplate",
    "Tile",
    "TilePayload",
    "TileMintRequest",
]
