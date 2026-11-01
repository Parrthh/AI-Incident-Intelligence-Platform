"""Redis client setup.

Every network call needs a timeout. Without ``socket_timeout``, a Redis server
that accepts the TCP connection but stops answering would hang the caller
forever. ``rediss://`` URLs (two s's, used by Upstash) turn on TLS automatically.
"""

from redis.asyncio import Redis

SOCKET_TIMEOUT_SECONDS = 5.0
CONNECT_TIMEOUT_SECONDS = 5.0


def create_redis(url: str) -> "Redis":
    return Redis.from_url(  # pyright: ignore[reportUnknownMemberType]
        url,
        socket_timeout=SOCKET_TIMEOUT_SECONDS,
        socket_connect_timeout=CONNECT_TIMEOUT_SECONDS,
        health_check_interval=30,  # ping idle connections so dead ones are noticed
        retry_on_timeout=True,
        decode_responses=True,
    )
