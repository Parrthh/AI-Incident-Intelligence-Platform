"""FastAPI application factory.

``create_app`` builds a fresh app from explicit settings. Tests call it with their
own settings; production calls it once through ``iip api``. Avoiding a
module-level ``app = FastAPI()`` keeps tests isolated from each other and from
the real environment.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from iip import __version__
from iip.api.health import ReadinessChecks
from iip.api.middleware import RequestContextMiddleware
from iip.api.routes import system
from iip.config import Settings, get_settings

logger = structlog.get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        logger.info(
            "api_starting",
            version=__version__,
            git_sha=settings.git_sha,
            env=settings.env.value,
        )
        yield
        logger.info("api_stopped")

    app = FastAPI(
        title="AI Incident Intelligence Platform",
        version=__version__,
        lifespan=lifespan,
        # Interactive docs are handy locally but expose the full API surface on a
        # public demo; keep them off in production.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    app.state.settings = settings
    app.state.readiness = ReadinessChecks()

    app.add_middleware(RequestContextMiddleware)
    app.include_router(system.router)
    return app
