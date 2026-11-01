"""Structured logging.

All logs go to stdout, one event per line. In production each line is a JSON
object, so a log platform can filter by field (``request_id``, ``level``...)
instead of grepping text.

Two kinds of loggers exist in the process:
- our code, which uses ``structlog.get_logger()``
- libraries (uvicorn, sqlalchemy...), which use the stdlib ``logging`` module

Both are routed through the same structlog processors, so every line has the same
shape no matter who wrote it. Context bound with ``structlog.contextvars`` (for
example the request ID) is attached to every line logged while handling that
request, including lines from libraries.
"""

import logging
import sys

import structlog
from structlog.typing import Processor


def configure_logging(*, level: str = "INFO", json: bool = True) -> None:
    """Configure structlog and the stdlib root logger. Safe to call more than once."""
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    renderer: Processor
    if json:
        renderer = structlog.processors.JSONRenderer()
        shared.append(structlog.processors.format_exc_info)
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Uvicorn installs its own handlers; drop them so its lines use our format.
    # Its access log is replaced by our request middleware, which includes the
    # request ID and duration.
    for name in ("uvicorn", "uvicorn.error"):
        lib_logger = logging.getLogger(name)
        lib_logger.handlers.clear()
        lib_logger.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
    access.disabled = True
