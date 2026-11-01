"""Command-line entrypoint: ``iip <command>``.

Commands:
    iip api      run the HTTP API (``--migrate`` applies migrations first)
    iip worker   run the background worker
    iip migrate  apply database migrations (safe to run from several instances at once)
    iip version  print the installed version
"""

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from iip import __version__
from iip.config import get_settings
from iip.observability.logging import configure_logging


def _run_api(*, migrate: bool = False) -> int:
    settings = get_settings()
    configure_logging(level=settings.log_level, json=settings.log_json)

    if migrate:
        # One process instead of `sh -c "iip migrate && iip api"`: hosting platforms
        # differ in how they split and quote start commands, and a single argv with
        # no shell avoids that. Migrations finish before the server binds its port,
        # so the platform never routes traffic to an un-migrated instance.
        _apply_migrations()

    from iip.api.app import create_app  # imported after logging is configured

    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_config=None,  # keep our logging setup instead of uvicorn's default
        access_log=False,  # replaced by RequestContextMiddleware
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
        timeout_graceful_shutdown=20,
    )
    return 0


def _run_worker() -> int:
    settings = get_settings()
    configure_logging(level=settings.log_level, json=settings.log_json)

    import asyncio

    from iip.worker.runner import run_worker_until_signalled

    asyncio.run(run_worker_until_signalled())
    return 0


def _run_migrate() -> int:
    settings = get_settings()
    configure_logging(level=settings.log_level, json=settings.log_json)
    _apply_migrations()
    return 0


def _apply_migrations() -> None:
    import structlog
    from alembic import command
    from alembic.config import Config

    # alembic.ini sits at the repo root locally and in /app inside the image.
    ini = Path(os.environ.get("IIP_ALEMBIC_CONFIG", "alembic.ini"))
    if not ini.is_file():
        raise SystemExit(f"alembic config not found at {ini.resolve()}")
    log = structlog.get_logger("iip.migrate")
    log.info("migrations_starting")
    command.upgrade(Config(str(ini)), "head")
    log.info("migrations_done")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="iip", description="AI Incident Intelligence Platform")
    sub = parser.add_subparsers(dest="command", required=True)
    api = sub.add_parser("api", help="run the HTTP API")
    api.add_argument(
        "--migrate",
        action="store_true",
        help="apply database migrations before starting (used by single-command hosts)",
    )
    sub.add_parser("worker", help="run the background worker")
    sub.add_parser("migrate", help="apply database migrations")
    sub.add_parser("version", help="print the installed version")

    args = parser.parse_args(argv)
    if args.command == "api":
        return _run_api(migrate=bool(args.migrate))
    if args.command == "worker":
        return _run_worker()
    if args.command == "migrate":
        return _run_migrate()
    if args.command == "version":
        print(__version__)
        return 0
    parser.error(f"unknown command {args.command}")  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
