from __future__ import annotations

import sqlite3


def test_squad_dataclass_living_graph_defaults():
    from sos.contracts.squad import Squad

    squad = Squad(id="sq-1", name="Marketing", project="viamar", objective="Ship")

    assert squad.dna_vector == []
    assert squad.coherence == 0.5
    assert squad.receptivity == 0.5
    assert squad.conductance == {}


def _apply_squad_migrations(db_path) -> None:
    """Run the Squad service's Alembic migrations against db_path.

    The service no longer creates tables at import time (v0.6.0 Step 2.2);
    tests must run migrations explicitly like production will.
    """
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    repo_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(repo_root / "sos" / "services" / "squad" / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")


def test_squad_db_initializes_living_graph_schema(tmp_path, monkeypatch):
    from sos.services.squad import service as squad_service

    class _RedisStub:
        def publish(self, *args, **kwargs):
            return 1

        def xadd(self, *args, **kwargs):
            return "1-0"

    monkeypatch.setattr(squad_service.redis, "Redis", lambda **kwargs: _RedisStub())

    db_path = tmp_path / "squads.db"
    _apply_squad_migrations(db_path)
    db = squad_service.SquadDB(db_path)

    with db.connect() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        squad_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(squads)").fetchall()
        }

    assert "squad_wallets" in tables
    assert "squad_transactions" in tables
    assert "squad_goals" in tables
    assert {"dna_vector", "coherence", "receptivity", "conductance_json"} <= squad_columns


def test_squad_bus_emits_payload_field(monkeypatch):
    from sos.services.squad import service as squad_service

    published: list[tuple[str, str]] = []
    added: list[tuple[str, dict[str, str], int]] = []

    class _RedisStub:
        def publish(self, channel, raw):
            published.append((channel, raw))
            return 1

        def xadd(self, stream, payload, maxlen=0):
            added.append((stream, payload, maxlen))
            return "1-0"

    monkeypatch.setattr(squad_service.redis, "Redis", lambda **kwargs: _RedisStub())

    bus = squad_service.SquadBus()
    bus.emit("squad.created", "sq-1", "tester", {"ok": True})

    assert published
    assert added
    assert "payload" in added[0][1]
    assert "data" not in added[0][1]
