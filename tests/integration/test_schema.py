"""The SQLAlchemy models must describe exactly what the migrations create.

If someone changes a model without writing a migration (or the other way round),
production would run with a schema the code doesn't expect. This compares the
migrated database with the models, the same way `alembic revision --autogenerate`
does, and fails on any difference.
"""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection

from iip.db.engine import create_engine
from iip.db.models import Base

pytestmark = pytest.mark.integration


def _diff(connection: Connection) -> list[object]:
    context = MigrationContext.configure(connection)
    return list(compare_metadata(context, Base.metadata))


async def test_models_match_migrations(migrated_database_url: str) -> None:
    engine = create_engine(migrated_database_url)
    try:
        async with engine.connect() as conn:
            diff = await conn.run_sync(_diff)
    finally:
        await engine.dispose()

    assert diff == [], f"models and migrations differ: {diff}"
