"""Integration tests run against real Postgres and Redis.

CI provides them as service containers. Locally, `make dev` starts them. If the
URLs aren't set, the tests are skipped rather than failing, so `make test` still
works on a machine without Docker.

The variables are deliberately named IIP_TEST_* so they never leak into the
Settings that unit tests build.
"""

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from iip.db.engine import create_engine

DATABASE_URL = os.environ.get("IIP_TEST_DATABASE_URL")
REDIS_URL = os.environ.get("IIP_TEST_REDIS_URL")
REQUIRED = os.environ.get("IIP_REQUIRE_INTEGRATION") == "1"


def _unavailable(name: str) -> None:
    if REQUIRED:
        pytest.fail(f"{name} not set but IIP_REQUIRE_INTEGRATION=1")
    pytest.skip(f"{name} not set")


@pytest.fixture
def database_url() -> str:
    if not DATABASE_URL:
        _unavailable("IIP_TEST_DATABASE_URL")
    assert DATABASE_URL
    return DATABASE_URL


@pytest.fixture
def redis_url() -> str:
    if not REDIS_URL:
        _unavailable("IIP_TEST_REDIS_URL")
    assert REDIS_URL
    return REDIS_URL


@pytest.fixture
async def fresh_database_url(database_url: str) -> AsyncIterator[str]:
    """A brand-new, empty database for one test, dropped afterwards.

    Migration tests need an empty schema; otherwise a second run would find the
    work already done and wouldn't test anything.
    """
    name = f"iip_test_{uuid.uuid4().hex[:12]}"
    admin = create_engine(database_url).execution_options(isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield make_url(database_url).set(database=name).render_as_string(hide_password=False)
    finally:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await admin.dispose()
