from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from iip.api.app import create_app
from iip.config import Environment, Settings


@pytest.fixture
def settings() -> Settings:
    # Built explicitly (not from the environment) so a developer's .env or shell
    # variables can't change test results.
    return Settings(env=Environment.TEST, git_sha="test-sha", log_json=True, _env_file=None)  # pyright: ignore[reportCallIssue]


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:  # "with" runs startup/shutdown (lifespan)
        yield test_client
