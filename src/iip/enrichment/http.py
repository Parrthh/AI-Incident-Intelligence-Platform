"""HTTP for evidence sources: a timeout on every call, one quick retry.

A connection error or a 5xx gets one retry after a short pause; anything else
(a 4xx, a bad query) fails at once, because retrying won't change the answer.
Errors are raised as ``SourceUnavailableError`` with a short, readable message
that ends up on the incident timeline.
"""

import asyncio
from typing import Any

import httpx

from iip.enrichment.base import SourceUnavailableError

DEFAULT_TIMEOUT_SECONDS = 10.0
RETRY_PAUSE_SECONDS = 0.5
_SERVER_ERROR = 500


class SourceClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout, transport=transport
        )

    async def get_json(self, path: str, params: dict[str, Any]) -> Any:
        last: Exception | None = None
        for attempt in range(2):
            try:
                response = await self._client.get(path, params=params)
            except httpx.TransportError as exc:
                last = exc
            else:
                if response.status_code < _SERVER_ERROR:
                    if response.is_error:
                        raise SourceUnavailableError(
                            f"HTTP {response.status_code}: {response.text[:200]}"
                        )
                    return response.json()
                last = SourceUnavailableError(f"HTTP {response.status_code}")
            if attempt == 0:
                await asyncio.sleep(RETRY_PAUSE_SECONDS)
        if isinstance(last, SourceUnavailableError):
            raise last
        raise SourceUnavailableError(f"{type(last).__name__}: {last}") from last

    async def aclose(self) -> None:
        await self._client.aclose()
