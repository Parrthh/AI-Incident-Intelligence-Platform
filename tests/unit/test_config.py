import pytest
from pydantic import ValidationError

from iip.config import Environment, Settings


def make_settings() -> Settings:
    return Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "IIP_ENV",
        "IIP_PORT",
        "PORT",
        "IIP_GIT_SHA",
        "RENDER_GIT_COMMIT",
        "IIP_LOG_LEVEL",
        "IIP_DATABASE_URL",
        "DATABASE_URL",
        "IIP_REDIS_URL",
        "REDIS_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_defaults() -> None:
    settings = make_settings()

    assert settings.env is Environment.DEVELOPMENT
    assert settings.port == 8000
    assert settings.commit_sha == "unknown"
    assert settings.is_production is False


def test_reads_prefixed_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IIP_ENV", "production")
    monkeypatch.setenv("IIP_PORT", "9000")
    monkeypatch.setenv("IIP_DATABASE_URL", "postgresql://u:p@db/iip")
    monkeypatch.setenv("IIP_REDIS_URL", "redis://cache:6379/0")

    settings = make_settings()

    assert settings.is_production is True
    assert settings.port == 9000


def test_accepts_platform_port_and_commit_names(monkeypatch: pytest.MonkeyPatch) -> None:
    # Render injects PORT and RENDER_GIT_COMMIT without our prefix.
    monkeypatch.setenv("PORT", "10000")
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc123")

    settings = make_settings()

    assert settings.port == 10000
    assert settings.commit_sha == "abc123"


def test_prefixed_name_wins_over_platform_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IIP_GIT_SHA", "from-ci")
    monkeypatch.setenv("RENDER_GIT_COMMIT", "from-render")

    assert make_settings().commit_sha == "from-ci"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("IIP_PORT", "not-a-number"),
        ("IIP_PORT", "70000"),
        ("IIP_ENV", "staging"),
        ("IIP_LOG_LEVEL", "LOUD"),
    ],
)
def test_invalid_values_fail_fast(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        make_settings()


def test_accepts_platform_database_and_redis_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db/iip")
    monkeypatch.setenv("REDIS_URL", "rediss://default:tok@cache:6379")

    settings = make_settings()

    assert settings.database_url is not None
    assert settings.database_url.get_secret_value() == "postgresql://u:p@db/iip"
    assert settings.redis_url is not None
    assert settings.redis_url.get_secret_value() == "rediss://default:tok@cache:6379"


def test_connection_strings_are_hidden_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:hunter2@db/iip")

    settings = make_settings()

    assert "hunter2" not in repr(settings)
    assert "hunter2" not in str(settings.model_dump())


@pytest.mark.parametrize(
    ("set_vars", "missing"),
    [
        ({}, "DATABASE_URL, REDIS_URL"),
        ({"DATABASE_URL": "postgresql://u:p@db/iip"}, "REDIS_URL"),
        ({"REDIS_URL": "redis://cache"}, "DATABASE_URL"),
    ],
)
def test_production_requires_database_and_redis(
    monkeypatch: pytest.MonkeyPatch, set_vars: dict[str, str], missing: str
) -> None:
    monkeypatch.setenv("IIP_ENV", "production")
    for name, value in set_vars.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError, match=missing):
        make_settings()


def test_development_runs_without_database_or_redis() -> None:
    settings = make_settings()

    assert settings.database_url is None
    assert settings.redis_url is None


def test_empty_baked_in_sha_falls_back_to_render_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    # The Docker image sets IIP_GIT_SHA="" when built without a GIT_SHA arg, as on
    # Render. That must not hide the commit Render provides at runtime.
    monkeypatch.setenv("IIP_GIT_SHA", "")
    monkeypatch.setenv("RENDER_GIT_COMMIT", "7d87db6")

    assert make_settings().commit_sha == "7d87db6"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        # Upstash's REST endpoint, a common copy-paste mistake.
        ("REDIS_URL", "https://example-12345.upstash.io"),
        # The redis-cli command instead of the URL.
        ("REDIS_URL", "redis-cli --tls -u redis://default:topsecret@example.upstash.io:6379"),
        ("DATABASE_URL", "mysql://u:topsecret@db/iip"),
    ],
)
def test_wrong_connection_string_fails_without_leaking_it(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError) as exc_info:
        make_settings()

    message = str(exc_info.value)
    assert "must start with" in message
    assert "topsecret" not in message
    assert "upstash.io" not in message


def test_accepts_tls_redis_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "rediss://default:tok@example.upstash.io:6379")

    assert make_settings().redis_url is not None
