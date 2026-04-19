"""Pydantic v2 contract tests for sos.contracts.ports.glass.

Covers:
- Round-trip model_dump / model_validate for each TileQuery variant
- extra="forbid" rejects unknown keys on all models
- TileTemplate enum completeness
- Tagged-union discriminator routes correctly on "kind"
- Tile field validation (id regex, title length, refresh_interval_s bounds)
- TileMintRequest mirrors Tile minus tenant
- TilePayload round-trip
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from sos.contracts.ports.glass import (
    BusTailQuery,
    HttpQuery,
    SqlQuery,
    Tile,
    TileMintRequest,
    TilePayload,
    TileQuery,
    TileTemplate,
)
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# SqlQuery
# ---------------------------------------------------------------------------


def test_sql_query_round_trip() -> None:
    q = SqlQuery(kind="sql", service="economy", statement="SELECT * FROM wallet WHERE id=:id", params={"id": "abc"})
    dumped = q.model_dump()
    restored = SqlQuery.model_validate(dumped)
    assert restored == q


def test_sql_query_defaults_empty_params() -> None:
    q = SqlQuery(kind="sql", service="economy", statement="SELECT 1")
    assert q.params == {}


def test_sql_query_extra_forbidden() -> None:
    with pytest.raises(ValidationError):
        SqlQuery(kind="sql", service="economy", statement="SELECT 1", unknown_field="bad")


# ---------------------------------------------------------------------------
# BusTailQuery
# ---------------------------------------------------------------------------


def test_bus_tail_query_round_trip() -> None:
    q = BusTailQuery(kind="bus_tail", stream="audit:decisions:acme", limit=10)
    restored = BusTailQuery.model_validate(q.model_dump())
    assert restored == q


def test_bus_tail_query_default_limit() -> None:
    q = BusTailQuery(kind="bus_tail", stream="mystream")
    assert q.limit == 20


def test_bus_tail_query_limit_bounds() -> None:
    with pytest.raises(ValidationError):
        BusTailQuery(kind="bus_tail", stream="s", limit=0)
    with pytest.raises(ValidationError):
        BusTailQuery(kind="bus_tail", stream="s", limit=101)


def test_bus_tail_query_extra_forbidden() -> None:
    with pytest.raises(ValidationError):
        BusTailQuery(kind="bus_tail", stream="s", limit=5, unexpected=True)


# ---------------------------------------------------------------------------
# HttpQuery
# ---------------------------------------------------------------------------


def test_http_query_round_trip() -> None:
    q = HttpQuery(kind="http", service="registry", path="/squad/acme/status")
    restored = HttpQuery.model_validate(q.model_dump())
    assert restored == q


def test_http_query_default_method() -> None:
    q = HttpQuery(kind="http", service="registry", path="/health")
    assert q.method == "GET"


def test_http_query_extra_forbidden() -> None:
    with pytest.raises(ValidationError):
        HttpQuery(kind="http", service="registry", path="/health", method="GET", extra="nope")


# ---------------------------------------------------------------------------
# TileQuery discriminated union
# ---------------------------------------------------------------------------


def test_tile_query_discriminator_sql() -> None:
    from pydantic import TypeAdapter

    ta: TypeAdapter[TileQuery] = TypeAdapter(TileQuery)
    q = ta.validate_python({"kind": "sql", "service": "economy", "statement": "SELECT 1"})
    assert isinstance(q, SqlQuery)


def test_tile_query_discriminator_bus_tail() -> None:
    from pydantic import TypeAdapter

    ta: TypeAdapter[TileQuery] = TypeAdapter(TileQuery)
    q = ta.validate_python({"kind": "bus_tail", "stream": "mystream"})
    assert isinstance(q, BusTailQuery)


def test_tile_query_discriminator_http() -> None:
    from pydantic import TypeAdapter

    ta: TypeAdapter[TileQuery] = TypeAdapter(TileQuery)
    q = ta.validate_python({"kind": "http", "service": "registry", "path": "/health"})
    assert isinstance(q, HttpQuery)


def test_tile_query_discriminator_invalid_kind() -> None:
    from pydantic import TypeAdapter

    ta: TypeAdapter[TileQuery] = TypeAdapter(TileQuery)
    with pytest.raises(ValidationError):
        ta.validate_python({"kind": "graphql", "query": "{ me }"})


# ---------------------------------------------------------------------------
# TileTemplate enum completeness
# ---------------------------------------------------------------------------


def test_tile_template_all_values() -> None:
    expected = {"number", "sparkline", "progress_bar", "event_log", "status_light", "chart"}
    actual = {m.value for m in TileTemplate}
    assert actual == expected, f"TileTemplate values mismatch: {actual}"


def test_tile_template_is_str_enum() -> None:
    assert TileTemplate.NUMBER == "number"
    assert TileTemplate.SPARKLINE == "sparkline"
    assert TileTemplate.PROGRESS_BAR == "progress_bar"
    assert TileTemplate.EVENT_LOG == "event_log"
    assert TileTemplate.STATUS_LIGHT == "status_light"
    assert TileTemplate.CHART == "chart"


# ---------------------------------------------------------------------------
# Tile
# ---------------------------------------------------------------------------


def _make_tile(**overrides) -> Tile:
    defaults = {
        "id": "health-light",
        "title": "Health",
        "query": {"kind": "http", "service": "registry", "path": "/health"},
        "template": "status_light",
        "refresh_interval_s": 60,
        "tenant": "acme",
    }
    defaults.update(overrides)
    return Tile.model_validate(defaults)


def test_tile_round_trip() -> None:
    t = _make_tile()
    restored = Tile.model_validate(t.model_dump(mode="json"))
    assert restored.id == t.id
    assert restored.tenant == t.tenant


def test_tile_id_regex_valid() -> None:
    t = _make_tile(id="my-tile-01")
    assert t.id == "my-tile-01"


def test_tile_id_regex_rejects_uppercase() -> None:
    with pytest.raises(ValidationError):
        _make_tile(id="MyTile")


def test_tile_id_regex_rejects_spaces() -> None:
    with pytest.raises(ValidationError):
        _make_tile(id="my tile")


def test_tile_title_min_length() -> None:
    with pytest.raises(ValidationError):
        _make_tile(title="")


def test_tile_title_max_length() -> None:
    with pytest.raises(ValidationError):
        _make_tile(title="x" * 81)


def test_tile_refresh_interval_bounds() -> None:
    with pytest.raises(ValidationError):
        _make_tile(refresh_interval_s=4)
    with pytest.raises(ValidationError):
        _make_tile(refresh_interval_s=3601)


def test_tile_extra_forbidden() -> None:
    with pytest.raises(ValidationError):
        Tile.model_validate(
            {
                "id": "x",
                "title": "X",
                "query": {"kind": "http", "service": "s", "path": "/h"},
                "template": "number",
                "tenant": "acme",
                "surprise": "field",
            }
        )


# ---------------------------------------------------------------------------
# TileMintRequest
# ---------------------------------------------------------------------------


def test_tile_mint_request_round_trip() -> None:
    req = TileMintRequest.model_validate(
        {
            "id": "wallet-balance",
            "title": "Wallet Balance",
            "query": {"kind": "http", "service": "economy", "path": "/balance/acme"},
            "template": "number",
            "refresh_interval_s": 30,
        }
    )
    restored = TileMintRequest.model_validate(req.model_dump(mode="json"))
    assert restored.id == req.id


def test_tile_mint_request_has_no_tenant_field() -> None:
    with pytest.raises(ValidationError):
        TileMintRequest.model_validate(
            {
                "id": "x",
                "title": "X",
                "query": {"kind": "http", "service": "s", "path": "/h"},
                "template": "number",
                "tenant": "acme",  # must be rejected — extra="forbid"
            }
        )


# ---------------------------------------------------------------------------
# TilePayload
# ---------------------------------------------------------------------------


def test_tile_payload_round_trip() -> None:
    now = datetime.now(timezone.utc)
    p = TilePayload(tile_id="health-light", rendered_at=now, data={"status": "ok"}, cache_ttl_s=60)
    restored = TilePayload.model_validate(p.model_dump(mode="json"))
    assert restored.tile_id == "health-light"
    assert restored.cache_ttl_s == 60


def test_tile_payload_extra_forbidden() -> None:
    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError):
        TilePayload(tile_id="x", rendered_at=now, data={}, cache_ttl_s=60, unexpected="y")
