"""Alembic environment: async engine, URL from DATABASE_URL, run as ``alembic upgrade head``.

DATABASE_URL is read like the app reads it: the environment first, then ``.env``
(local development only). Logs go to stdout as JSON, like the web and worker.
"""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.logging import configure_logging
from app.models.orm import Base

config = context.config
target_metadata = Base.metadata


class _MigrationSettings(BaseSettings):
    """Only what migrations need, so they run without SECRET_KEY or Redis settings."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str | None = None
    log_level: str = "INFO"
    app_env: str = "development"
    service_version: str | None = None


def _url() -> str:
    url = _settings.database_url
    if not url:
        raise RuntimeError("DATABASE_URL must be set to run migrations")
    return url


def run_migrations_offline() -> None:
    """Emit SQL without connecting (``alembic upgrade head --sql``)."""
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Connect with the async engine and apply migrations."""
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _url()
    engine = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


_settings = _MigrationSettings()
configure_logging(
    "storyfeta-migrate",
    level=_settings.log_level,
    service_version=_settings.service_version,
    env=_settings.app_env,
)

if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
