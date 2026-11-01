# syntax=docker/dockerfile:1.7

# ---- builder: install dependencies into a virtualenv ------------------------
FROM python:3.12-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependencies first, project code second. Docker caches each layer, so editing
# source code doesn't re-download every dependency: only the last layers rebuild.
COPY pyproject.toml README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-install-project

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-editable

# ---- runtime: small image, no build tools, non-root user --------------------
FROM python:3.12-slim-bookworm AS runtime

# A fixed, unprivileged user. If the app is ever compromised, the attacker
# doesn't get root inside the container.
RUN groupadd --system --gid 10001 iip \
    && useradd --system --uid 10001 --gid iip --no-create-home --shell /usr/sbin/nologin iip

WORKDIR /app
COPY --from=builder --chown=iip:iip /app/.venv /app/.venv
COPY --chown=iip:iip alembic.ini ./
COPY --chown=iip:iip migrations ./migrations

# Empty unless the build passes it (CI and GHCR do). On Render the app falls back
# to RENDER_GIT_COMMIT at runtime.
ARG GIT_SHA=""
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    IIP_GIT_SHA=${GIT_SHA} \
    IIP_ENV=production \
    IIP_LOG_JSON=true

USER iip
EXPOSE 8000

# Liveness only: a database outage must not make Docker restart the container.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT') or os.environ.get('IIP_PORT') or 8000}/healthz\", timeout=2)"]

CMD ["iip", "api"]
