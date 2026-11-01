"""Command-line entrypoint: ``iip <command>``.

Commands:
    iip api      run the HTTP API
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


def _run_api() -> int:
    settings = get_settings()
    configure_logging(level=settings.log_level, json=settings.log_json)

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


def _run_migrate() -> int:
    settings = get_settings()
    configure_logging(level=settings.log_level, json=settings.log_json)

    from alembic import command
    from alembic.config import Config

    # alembic.ini sits at the repo root locally and in /app inside the image.
    ini = Path(os.environ.get("IIP_ALEMBIC_CONFIG", "alembic.ini"))
    if not ini.is_file():
        raise SystemExit(f"alembic config not found at {ini.resolve()}")
    command.upgrade(Config(str(ini)), "head")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="iip", description="AI Incident Intelligence Platform")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("api", help="run the HTTP API")
    sub.add_parser("migrate", help="apply database migrations")
    sub.add_parser("version", help="print the installed version")

    args = parser.parse_args(argv)
    if args.command == "api":
        return _run_api()
    if args.command == "migrate":
        return _run_migrate()
    if args.command == "version":
        print(__version__)
        return 0
    parser.error(f"unknown command {args.command}")  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
