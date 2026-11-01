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

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
LLMProviderName = Literal["none", "fake", "gemini"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="IIP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        validate_by_name=True,  # allow Settings(git_sha=...) in tests despite aliases
        # Validation errors normally echo the bad input. For connection strings
        # that would print passwords into the logs, so never include inputs.
        hide_input_in_errors=True,
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

    # Commit SHA of the running build, reported by /version so a deploy can be
    # verified. Docker builds bake in IIP_GIT_SHA when a GIT_SHA build arg is given
    # (CI, GHCR images); Render sets RENDER_GIT_COMMIT at runtime. Kept as two
    # fields so an empty IIP_GIT_SHA from a build without the arg doesn't hide
    # Render's value. Read the result through `commit_sha`.
    git_sha: str = Field(default="", validation_alias=AliasChoices("IIP_GIT_SHA", "git_sha"))
    render_git_commit: str = Field(default="", validation_alias="RENDER_GIT_COMMIT")

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

    # Bearer token(s) that Alertmanager must send on the webhook. Comma-separated so
    # a token can be rotated without downtime: add the new one, switch Alertmanager,
    # then remove the old one. Unset means the webhook is disabled (503).
    webhook_token: SecretStr | None = Field(default=None, validation_alias="IIP_WEBHOOK_TOKEN")

    # LLM used for incident summaries (ADR-0005). "none" turns summaries off;
    # "fake" returns a canned summary (local development without a key).
    llm_provider: LLMProviderName = "none"
    llm_model: str = "gemini-2.5-flash-lite"
    llm_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    gemini_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("IIP_GEMINI_API_KEY", "GEMINI_API_KEY")
    )

    # Run the worker loop inside the API process. Only for hosts without a
    # separate background worker (the free Render demo, ADR-0004). Self-hosted
    # installs run `iip worker` as its own container.
    run_worker_in_api: bool = False

    # Connection pool size per process. Free-tier databases allow few
    # connections, so keep this small.
    db_pool_size: int = Field(default=5, ge=1, le=50)

    @field_validator("database_url")
    @classmethod
    def _check_database_scheme(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            scheme = value.get_secret_value().strip().split("://", 1)[0].lower()
            if scheme not in {"postgres", "postgresql", "postgresql+asyncpg"}:
                raise ValueError("DATABASE_URL must start with postgresql:// (or postgres://)")
        return value

    @field_validator("redis_url")
    @classmethod
    def _check_redis_scheme(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            scheme = value.get_secret_value().strip().split("://", 1)[0].lower()
            if scheme not in {"redis", "rediss", "unix"}:
                raise ValueError(
                    "REDIS_URL must start with redis:// or rediss:// (for TLS, e.g. Upstash). "
                    "An https:// URL is a REST endpoint, not a Redis connection string."
                )
        return value

    @property
    def webhook_tokens(self) -> list[str]:
        if self.webhook_token is None:
            return []
        return [t.strip() for t in self.webhook_token.get_secret_value().split(",") if t.strip()]

    @property
    def commit_sha(self) -> str:
        return self.git_sha.strip() or self.render_git_commit.strip() or "unknown"

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
        if self.llm_provider == "gemini" and self.gemini_api_key is None:
            raise ValueError("IIP_LLM_PROVIDER=gemini requires GEMINI_API_KEY")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings (read from the environment once)."""
    return Settings()
