"""Alembic environment for the Identity service.

Resolves the DB URL from IDENTITY_DB_URL (if set), falling back to
``~/.sos/data/identity.db``. Loads ``Base.metadata`` from
``sos.services.identity.models`` as target_metadata for autogenerate.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool


def _load_models_base():
    """Load ``sos.services.identity.models`` without triggering the
    parent ``sos`` package's ``__init__`` chain (which would pull in
    kernel + optional deps we don't need for schema work).
    """
    here = Path(__file__).resolve().parent
    models_path = here.parent / "models.py"
    spec = importlib.util.spec_from_file_location(
        "sos_services_identity_models_alembic", models_path
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.Base


Base = _load_models_base()

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _resolve_db_url() -> str:
    # 1. Programmatic override via Config.set_main_option.
    preset = config.get_main_option("sqlalchemy.url")
    if preset:
        return preset
    # 2. Env var.
    url = os.environ.get("IDENTITY_DB_URL")
    if url:
        return url
    # 3. Default matches IdentityCore's on-disk path.
    data_dir = Path(
        os.environ.get("SOS_DATA_DIR", str(Path.home() / ".sos" / "data"))
    )
    return f"sqlite:///{data_dir / 'identity.db'}"


config.set_main_option("sqlalchemy.url", _resolve_db_url())

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
