"""Command-line entrypoint: ``iip <command>``.

Commands:
    iip api      run the HTTP API (``--migrate`` applies migrations first)
    iip worker   run the background worker
    iip migrate  apply database migrations (safe to run from several instances at once)
    iip queue    show job queue health, or prune old worker names
    iip dlq      list, replay or purge dead-lettered jobs
    iip version  print the installed version
"""

import argparse
import os
import sys
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


def _run_queue_command(args: argparse.Namespace) -> int:
    """``iip queue ...`` and ``iip dlq ...``: short-lived admin commands against Redis."""
    import asyncio
    import json
    from datetime import UTC, datetime

    from iip.queue import admin
    from iip.queue.redis import create_redis

    settings = get_settings()
    configure_logging(level=settings.log_level, json=settings.log_json)
    if settings.redis_url is None:
        print("REDIS_URL is not set", file=sys.stderr)
        return 2
    stream = settings.jobs_stream
    url = settings.redis_url.get_secret_value()

    async def run() -> int:
        redis = create_redis(url)
        try:
            if args.command == "queue" and args.action == "stats":
                stats = await admin.queue_stats(redis, stream)
                print(f"stream        {stream}")
                print(f"length        {stats.stream_length}")
                print(f"pending       {stats.pending}")
                print(f"dead letters  {stats.dead_letters}")
                print(f"consumers     {len(stats.consumers)}")
                for c in stats.consumers:
                    print(f"  {c['name']}  pending={c['pending']}  idle={int(c['idle']) // 1000}s")
            elif args.command == "queue" and args.action == "prune-consumers":
                removed = await admin.prune_consumers(redis, stream)
                print(f"removed {len(removed)} idle consumers")
            elif args.action == "list":
                for d in await admin.dlq_list(redis, stream, limit=args.limit):
                    when = datetime.fromtimestamp(float(d.dead_lettered_at or 0), UTC)
                    print(
                        json.dumps(
                            {
                                "id": d.id,
                                "type": d.type,
                                "payload": d.payload,
                                "deliveries": d.deliveries,
                                "error": d.error,
                                "dead_lettered_at": when.isoformat(timespec="seconds"),
                            }
                        )
                    )
            elif args.action == "replay":
                ids = None if args.all else list(args.ids)
                if ids is not None and not ids:
                    print("give dead letter IDs or --all", file=sys.stderr)
                    return 2
                try:
                    count = await admin.dlq_replay(redis, stream, ids)
                except KeyError as exc:
                    print(f"no dead letter with id {exc.args[0]}", file=sys.stderr)
                    return 1
                print(f"replayed {count}")
            elif args.action == "purge":
                if not args.yes:
                    print("purge deletes every dead letter; add --yes to confirm", file=sys.stderr)
                    return 2
                print(f"purged {await admin.dlq_purge(redis, stream)}")
            return 0
        finally:
            await redis.aclose()

    return asyncio.run(run())


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

    queue = sub.add_parser("queue", help="job queue health")
    queue_sub = queue.add_subparsers(dest="action", required=True)
    queue_sub.add_parser("stats", help="stream length, pending jobs, dead letters, workers")
    queue_sub.add_parser("prune-consumers", help="forget worker names idle for an hour")

    dlq = sub.add_parser("dlq", help="dead-lettered jobs")
    dlq_sub = dlq.add_subparsers(dest="action", required=True)
    dlq_list = dlq_sub.add_parser("list", help="newest dead letters, one JSON object per line")
    dlq_list.add_argument("--limit", type=int, default=20)
    dlq_replay = dlq_sub.add_parser("replay", help="put dead letters back on the queue")
    dlq_replay.add_argument("ids", nargs="*", help="dead letter IDs from `iip dlq list`")
    dlq_replay.add_argument("--all", action="store_true", help="replay every dead letter")
    dlq_purge = dlq_sub.add_parser("purge", help="delete every dead letter")
    dlq_purge.add_argument("--yes", action="store_true", help="confirm")

    args = parser.parse_args(argv)
    if args.command == "api":
        return _run_api(migrate=bool(args.migrate))
    if args.command == "worker":
        return _run_worker()
    if args.command == "migrate":
        return _run_migrate()
    if args.command in {"queue", "dlq"}:
        return _run_queue_command(args)
    if args.command == "version":
        print(__version__)
        return 0
    parser.error(f"unknown command {args.command}")  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
