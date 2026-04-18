"""Identity service smoke test.

As of v0.6.0 the schema is owned by Alembic (Step 2.2) — the service
no longer runs CREATE TABLE at boot. Tests must therefore upgrade a
tmp DB explicitly, mirroring what prod does via
``scripts/migrate-db.sh``.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def _apply_identity_migrations(db_path: Path) -> None:
    """Run the Identity service's Alembic migrations against db_path."""
    from alembic import command
    from alembic.config import Config

    repo_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(repo_root / "sos" / "services" / "identity" / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")


@pytest.fixture
def identity_core(tmp_path, monkeypatch):
    """Fresh IdentityCore bound to a migrated tmp DB, Redis mocked out."""
    from sos.services.identity import core as identity_core_mod

    db_path = tmp_path / "identity.db"
    _apply_identity_migrations(db_path)

    core = identity_core_mod.IdentityCore()
    core.db_path = db_path
    core.bus._redis = None  # keep the test offline

    # Reset module singleton so each test gets its own instance.
    monkeypatch.setattr(identity_core_mod, "_identity", None, raising=False)
    return core


async def test_identity_service(identity_core):
    core = identity_core

    user = core.create_user("Kasra", bio="Architect", avatar="https://mumega.io/kasra.png")
    assert core.get_user(user.id).bio == "Architect"

    guild = await core.create_guild("Architects Guild", owner_id=user.id, description="Builders of SOS")
    members = core.list_members(guild.id)
    assert len(members) == 1
    assert members[0]["role"] == "leader"

    user2 = core.create_user("River", bio="Oracle")
    await core.join_guild(guild.id, user2.id)
    assert len(core.list_members(guild.id)) == 2
