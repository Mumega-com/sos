"""Alembic ↔ ORM parity contract.

Every service that ships alembic migrations must satisfy two invariants:

1. `alembic upgrade head` against a fresh SQLite applies cleanly.
2. After upgrading, the table columns match the ORM definitions
   exactly — no drift, no forgotten columns, no accidental extras.

This test runs the migration, introspects the resulting schema, and
compares to the ORM's ``__table__.columns``. If a future migration
forgets to drop a column from 0001, or an ORM rename isn't mirrored,
this test fails loudly at CI time instead of in prod.

Today's covered services: **squad**. **identity** has migrations but
its ORM lives in `sos/services/identity/models.py` and is covered
separately once it's stable.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config


def _apply_migrations(alembic_cfg_path: Path, db_path: Path) -> None:
    """Run `alembic upgrade head` against *db_path* using *alembic_cfg_path*."""
    cfg = Config(str(alembic_cfg_path))
    # Resolve script_location relative to repo root (not pytest cwd).
    cfg.set_main_option("script_location", str(alembic_cfg_path.parent / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")


def _sqlite_columns(db_path: Path, table: str) -> set[str]:
    con = sqlite3.connect(str(db_path))
    try:
        rows = list(con.execute(f"PRAGMA table_info({table})"))
        return {row[1] for row in rows}
    finally:
        con.close()


class TestSquadAlembicOrmParity:
    @pytest.fixture
    def fresh_db(self, tmp_path):
        repo_root = Path(__file__).resolve().parents[2]
        alembic_ini = repo_root / "sos" / "services" / "squad" / "alembic.ini"
        db = tmp_path / "squad.db"
        _apply_migrations(alembic_ini, db)
        return db

    def test_squad_skills_columns_match_orm(self, fresh_db):
        from sos.services.squad.models import SquadSkill

        orm_cols = {c.name for c in SquadSkill.__table__.columns}
        db_cols = _sqlite_columns(fresh_db, "squad_skills")
        assert orm_cols == db_cols, (
            f"squad_skills ORM/DB drift — "
            f"only-in-ORM={orm_cols - db_cols}, only-in-DB={db_cols - orm_cols}"
        )

    def test_pipeline_specs_columns_match_orm(self, fresh_db):
        from sos.services.squad.models import PipelineSpec

        orm_cols = {c.name for c in PipelineSpec.__table__.columns}
        db_cols = _sqlite_columns(fresh_db, "pipeline_specs")
        assert orm_cols == db_cols, (
            f"pipeline_specs ORM/DB drift — "
            f"only-in-ORM={orm_cols - db_cols}, only-in-DB={db_cols - orm_cols}"
        )

    def test_pipeline_runs_columns_match_orm(self, fresh_db):
        from sos.services.squad.models import PipelineRun

        orm_cols = {c.name for c in PipelineRun.__table__.columns}
        db_cols = _sqlite_columns(fresh_db, "pipeline_runs")
        assert orm_cols == db_cols, (
            f"pipeline_runs ORM/DB drift — "
            f"only-in-ORM={orm_cols - db_cols}, only-in-DB={db_cols - orm_cols}"
        )

    def test_no_legacy_columns_leaked_back(self, fresh_db):
        """Guard against accidentally re-adding the v0.6.2 drops."""
        legacy = {
            "squad_skills": {"framework", "agent"},
            "pipeline_specs": {"review_enabled", "review_agent", "review_cmd"},
            "pipeline_runs": {"reviewer_notes"},
        }
        for table, dropped_cols in legacy.items():
            db_cols = _sqlite_columns(fresh_db, table)
            regressions = dropped_cols & db_cols
            assert not regressions, (
                f"{table} has re-introduced v0.6.2-dropped columns: {regressions}"
            )
