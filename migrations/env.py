"""Alembic environment: runs migrations with the app's own settings.

Two things here matter in production:

1. **Advisory lock.** Several instances can start at once during a deploy. If
   each one ran migrations, two could try to create the same table and one would
   crash halfway. ``pg_advisory_xact_lock`` makes them queue up: the first runs
   the migrations, the others wait, then find nothing left to do. All migrations
   run in one transaction, so a failure leaves the schema unchanged.
2. **Same URL handling as the app** (``iip.db.engine.parse_database_url``), so
   provider URLs with ``sslmode=require`` work for migrations too.
"""

import asyncio

from alembic import context
from sqlalchemy import Connection, pool, text
from sqlalchemy.ext.asyncio import create_async_engine

from iip.config import get_settings
from iip.db.engine import parse_database_url
from iip.db.models import Base

# Arbitrary constant shared by every instance of this app. Any 64-bit integer works
# as long as nothing else in the same database uses it.
MIGRATION_LOCK_ID = 7_392_001

# Lets `alembic revision --autogenerate` and the schema drift test compare the
# models with the migrated database.
target_metadata = Base.metadata


def _database_url() -> str:
    url = get_settings().database_url
    if url is None:
        raise RuntimeError("DATABASE_URL (or IIP_DATABASE_URL) must be set to run migrations")
    return url.get_secret_value()


def _run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        # Transaction-scoped lock: released automatically on commit or rollback, and
        # safe behind a transaction-mode pooler (a session-level lock could be taken
        # on one server connection and released on another).
        connection.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": MIGRATION_LOCK_ID})
        context.run_migrations()


async def _run_online() -> None:
    config = parse_database_url(_database_url())
    engine = create_async_engine(
        config.url, connect_args=config.connect_args, poolclass=pool.NullPool
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("offline (--sql) migrations are not supported")

asyncio.run(_run_online())
