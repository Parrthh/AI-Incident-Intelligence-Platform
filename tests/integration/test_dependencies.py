import asyncio
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text

from iip.api.app import create_app
from iip.config import Environment, Settings
from iip.db.engine import create_engine

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]

# Latest migration. Update when adding one (test_schema.py catches model drift).
HEAD_REVISION = "0005"


def make_settings(**overrides: object) -> Settings:
    return Settings(env=Environment.TEST, _env_file=None, **overrides)  # pyright: ignore[reportCallIssue, reportArgumentType]


def test_readyz_checks_real_database_and_redis(database_url: str, redis_url: str) -> None:
    settings = make_settings(database_url=SecretStr(database_url), redis_url=SecretStr(redis_url))

    with TestClient(create_app(settings)) as client:
        response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {
            "database": {"ok": True, "error": None},
            "redis": {"ok": True, "error": None},
        },
    }


def test_readyz_reports_unreachable_database(redis_url: str) -> None:
    # Port 1 on localhost: nothing listens there, so the connection is refused fast.
    settings = make_settings(
        database_url=SecretStr("postgresql://u:secret-pw@127.0.0.1:1/iip"),
        redis_url=SecretStr(redis_url),
    )

    with TestClient(create_app(settings)) as client:
        response = client.get("/readyz")

    assert response.status_code == 503
    checks = response.json()["checks"]
    assert checks["database"]["ok"] is False
    assert checks["redis"]["ok"] is True
    assert "secret-pw" not in response.text


def test_readyz_reports_unreachable_redis(database_url: str) -> None:
    settings = make_settings(
        database_url=SecretStr(database_url),
        redis_url=SecretStr("redis://127.0.0.1:1/0"),
    )

    with TestClient(create_app(settings)) as client:
        response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["checks"]["redis"]["ok"] is False


async def _migrate(database_url: str) -> tuple[int, str]:
    """Run `iip migrate` in a separate process, like a deploy would."""
    env = {**os.environ, "IIP_DATABASE_URL": database_url, "IIP_LOG_JSON": "false"}
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "iip",
        "migrate",
        cwd=REPO_ROOT,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
    return proc.returncode or 0, output.decode()


async def _schema_state(database_url: str) -> tuple[str | None, str | None]:
    engine = create_engine(database_url)
    try:
        async with engine.connect() as conn:
            ext = await conn.scalar(
                text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
            )
            version = await conn.scalar(text("SELECT version_num FROM alembic_version"))
    finally:
        await engine.dispose()
    return ext, version


async def test_migrate_enables_pgvector_and_is_idempotent(fresh_database_url: str) -> None:
    code, output = await _migrate(fresh_database_url)
    assert code == 0, output

    # Running again must be a no-op, not an error: deploys run it every time.
    code, output = await _migrate(fresh_database_url)
    assert code == 0, output

    assert await _schema_state(fresh_database_url) == ("vector", HEAD_REVISION)


async def test_concurrent_migrations_do_not_conflict(fresh_database_url: str) -> None:
    # Several instances starting at once during a deploy, all racing to apply the
    # same migration to an empty database. The advisory lock makes them take turns.
    results = await asyncio.gather(*(_migrate(fresh_database_url) for _ in range(4)))

    for code, output in results:
        assert code == 0, output
    assert await _schema_state(fresh_database_url) == ("vector", HEAD_REVISION)
