"""Alembic environment for the Squad service.

Resolves the DB URL from SQUAD_DB_URL (if set), falling back to the
kernel-config default (``~/.sos/data/squads.db``). Loads ``Base.metadata``
from ``sos.services.squad.models`` as target_metadata so that
``alembic revision --autogenerate`` can diff ORM models against the live
DB.
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
    """Load ``sos.services.squad.models`` without triggering the parent
    ``sos`` package's ``__init__`` chain.

    The runtime package imports kernel/bus/etc., which aren't needed for
    schema work and would drag in every optional service dep. We load
    ``models.py`` as a standalone module instead.
    """
    here = Path(__file__).resolve().parent
    models_path = here.parent / "models.py"
    spec = importlib.util.spec_from_file_location(
        "sos_services_squad_models_alembic", models_path
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
    # 1. Honour an URL already injected into the Config object (e.g. via
    #    alembic.config.Config.set_main_option from a programmatic caller).
    preset = config.get_main_option("sqlalchemy.url")
    if preset:
        return preset
    # 2. Env var override.
    url = os.environ.get("SQUAD_DB_URL")
    if url:
        return url
    # 3. Default mirrors sos.kernel.config.DB_PATH (~/.sos/data/squads.db).
    #    Duplicated to avoid importing the full kernel at migration time.
    data_dir = Path(
        os.environ.get("SOS_DATA_DIR", str(Path.home() / ".sos" / "data"))
    )
    return f"sqlite:///{data_dir / 'squads.db'}"


# Inject the URL back into the config so engine_from_config sees it.
config.set_main_option("sqlalchemy.url", _resolve_db_url())

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Generate SQL without a live DB connection."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # SQLite needs batch mode for ALTER
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations with a live DB connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite needs batch mode for ALTER
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
