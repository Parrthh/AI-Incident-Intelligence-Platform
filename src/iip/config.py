"""Application settings, loaded from environment variables.

Every setting has an ``IIP_`` prefixed name. A few also accept the bare name that
hosting platforms use by convention (``DATABASE_URL``, ``REDIS_URL``, ``PORT``),
so the same image runs on Render, Docker Compose or a laptop without renaming.

Settings are validated once at startup. A bad value raises immediately instead of
failing later on the first request that touches it ("fail fast").
"""

from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="IIP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        validate_by_name=True,  # allow Settings(git_sha=...) in tests despite aliases
    )

    env: Environment = Environment.DEVELOPMENT

    host: str = "0.0.0.0"  # noqa: S104 - binding all interfaces is required inside containers
    port: int = Field(
        default=8000,
        ge=1,
        le=65535,
        validation_alias=AliasChoices("IIP_PORT", "PORT"),
    )

    # Which proxies may set X-Forwarded-For / X-Forwarded-Proto. Only trust
    # forwarded headers from a load balancer you control: "*" is right behind
    # Render's proxy, wrong for a server exposed directly to the internet.
    forwarded_allow_ips: str = "127.0.0.1"

    log_level: LogLevel = "INFO"
    # JSON logs in production; human-readable console logs are easier locally.
    log_json: bool = True

    # Commit SHA of the running build. Render sets RENDER_GIT_COMMIT; CI and Docker
    # builds set IIP_GIT_SHA. Used by /version so deploys can be verified.
    git_sha: str = Field(
        default="unknown",
        validation_alias=AliasChoices("IIP_GIT_SHA", "RENDER_GIT_COMMIT"),
    )

    # Connection strings are SecretStr so they never show up in reprs, logs or
    # error messages by accident. Call .get_secret_value() only where connecting.
    database_url: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("IIP_DATABASE_URL", "DATABASE_URL"),
    )
    redis_url: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("IIP_REDIS_URL", "REDIS_URL"),
    )

    # Connection pool size per process. Free-tier databases allow few
    # connections, so keep this small.
    db_pool_size: int = Field(default=5, ge=1, le=50)

    @property
    def is_production(self) -> bool:
        return self.env is Environment.PRODUCTION

    @model_validator(mode="after")
    def _require_dependencies_in_production(self) -> "Settings":
        if self.is_production:
            missing = [
                name
                for name, value in (
                    ("DATABASE_URL", self.database_url),
                    ("REDIS_URL", self.redis_url),
                )
                if value is None
            ]
            if missing:
                raise ValueError(f"missing required settings in production: {', '.join(missing)}")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings (read from the environment once)."""
    return Settings()
