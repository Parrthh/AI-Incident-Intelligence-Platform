"""Redis client setup.

Every network call needs a timeout. Without ``socket_timeout``, a Redis server
that accepts the TCP connection but stops answering would hang the caller
forever. ``rediss://`` URLs (two s's, used by Upstash) turn on TLS automatically.
"""

from redis.asyncio import Redis

SOCKET_TIMEOUT_SECONDS = 5.0
CONNECT_TIMEOUT_SECONDS = 5.0


def create_redis(url: str, *, socket_timeout: float = SOCKET_TIMEOUT_SECONDS) -> "Redis":
    """``socket_timeout`` must be longer than any blocking read made on this client
    (``XREADGROUP ... BLOCK``), or the client gives up before Redis answers. Queue
    consumers therefore get their own client with a longer timeout."""
    return Redis.from_url(  # pyright: ignore[reportUnknownMemberType]
        url,
        socket_timeout=socket_timeout,
        socket_connect_timeout=CONNECT_TIMEOUT_SECONDS,
        health_check_interval=30,  # ping idle connections so dead ones are noticed
        retry_on_timeout=True,
        decode_responses=True,
    )
