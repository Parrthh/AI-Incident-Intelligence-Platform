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
    ):
        monkeypatch.delenv(name, raising=False)


def test_defaults() -> None:
    settings = make_settings()

    assert settings.env is Environment.DEVELOPMENT
    assert settings.port == 8000
    assert settings.git_sha == "unknown"
    assert settings.is_production is False


def test_reads_prefixed_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IIP_ENV", "production")
    monkeypatch.setenv("IIP_PORT", "9000")

    settings = make_settings()

    assert settings.is_production is True
    assert settings.port == 9000


def test_accepts_platform_port_and_commit_names(monkeypatch: pytest.MonkeyPatch) -> None:
    # Render injects PORT and RENDER_GIT_COMMIT without our prefix.
    monkeypatch.setenv("PORT", "10000")
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc123")

    settings = make_settings()

    assert settings.port == 10000
    assert settings.git_sha == "abc123"


def test_prefixed_name_wins_over_platform_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IIP_GIT_SHA", "from-ci")
    monkeypatch.setenv("RENDER_GIT_COMMIT", "from-render")

    assert make_settings().git_sha == "from-ci"


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
