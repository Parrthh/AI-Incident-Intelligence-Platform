"""Postgres connection setup.

Connection strings come in the libpq style that hosting providers hand out
(``postgresql://user:pass@host/db?sslmode=require``). SQLAlchemy's asyncpg driver
needs two adjustments before it can use them:

1. The scheme must name the driver: ``postgresql+asyncpg://``.
2. asyncpg doesn't understand libpq query options such as ``sslmode`` and
   ``channel_binding``; passed through, they make the connection fail. We strip
   them and translate ``sslmode`` into asyncpg's ``ssl`` argument.

Poolers: Neon's pooled endpoint (hostname contains ``-pooler``) runs PgBouncer in
transaction mode, where consecutive statements can run on different server
connections. asyncpg caches prepared statements per connection, so a cached
statement can vanish between uses and queries fail with "prepared statement does
not exist". Disabling the statement caches avoids that. It costs a little speed
per query, which doesn't matter at this scale.
"""

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# libpq options asyncpg rejects. sslmode is translated; the rest are dropped.
_LIBPQ_ONLY_OPTIONS = ("sslmode", "channel_binding", "sslrootcert", "sslcert", "sslkey")

# sslmode values that mean "use TLS". asyncpg accepts these same names for ``ssl``.
_SSL_MODES = {"require", "verify-ca", "verify-full", "prefer", "allow"}


@dataclass(frozen=True)
class DatabaseConfig:
    url: URL
    connect_args: dict[str, Any] = field(default_factory=dict[str, Any])
    uses_pooler: bool = False


def parse_database_url(raw_url: str) -> DatabaseConfig:
    """Turn a provider connection string into what SQLAlchemy + asyncpg need."""
    url = make_url(raw_url)

    if url.drivername in ("postgres", "postgresql", "postgresql+asyncpg"):
        url = url.set(drivername="postgresql+asyncpg")
    else:
        raise ValueError(f"unsupported database scheme: {url.drivername!r}")

    query = dict(url.query)
    connect_args: dict[str, Any] = {}

    sslmode = query.get("sslmode")
    if isinstance(sslmode, str) and sslmode in _SSL_MODES:
        connect_args["ssl"] = sslmode
    for option in _LIBPQ_ONLY_OPTIONS:
        query.pop(option, None)
    url = url.set(query=query)

    uses_pooler = "-pooler" in (url.host or "")
    if uses_pooler:
        connect_args["statement_cache_size"] = 0
        connect_args["prepared_statement_cache_size"] = 0
        # Unique names stop two sessions sharing a server connection from
        # colliding on SQLAlchemy's default statement names.
        connect_args["prepared_statement_name_func"] = lambda: f"__iip_{uuid4().hex}__"

    return DatabaseConfig(url=url, connect_args=connect_args, uses_pooler=uses_pooler)


def create_engine(raw_url: str, *, pool_size: int = 5) -> AsyncEngine:
    config = parse_database_url(raw_url)
    return create_async_engine(
        config.url,
        connect_args=config.connect_args,
        pool_size=pool_size,
        max_overflow=pool_size,
        pool_pre_ping=True,  # serverless Postgres drops idle connections; test before use
        pool_recycle=300,
        pool_timeout=10,
    )
