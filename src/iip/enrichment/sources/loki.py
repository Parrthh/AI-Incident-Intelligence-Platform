"""Error logs from Loki, grouped into patterns.

Thousands of error lines usually come from a handful of code paths. This source
pulls error-looking lines for the affected services in the incident window
(capped), masks the variable parts (IDs, numbers, quoted values) and counts each
resulting pattern. The top patterns become evidence, for example:

    payment log pattern seen 143 times since 14:38:21:
    "Payment request failed. Invalid token. app.loyalty.level=<str>"

Services are matched on Loki's ``service_name`` label, which Loki sets from the
OpenTelemetry resource attribute ``service.name``.
"""

import re
from datetime import UTC, datetime
from typing import Any, cast

from iip.enrichment.analysis import group_logs
from iip.enrichment.base import EnrichmentContext, EvidenceItem, EvidenceKind
from iip.enrichment.http import SourceClient

MAX_LINES = 2000
TOP_PATTERNS = 10
ERROR_WORDS = r"(?i)(error|exception|fail|fatal|panic|timeout|refused|unavailable)"


def _selector(services: tuple[str, ...]) -> str:
    names = "|".join(re.escape(s) for s in services)
    return f'{{service_name=~"{names}"}} |~ `{ERROR_WORDS}`'


class LokiSource:
    name = "logs"

    def __init__(self, base_url: str, **client: Any) -> None:
        self._http = SourceClient(base_url, **client)

    async def collect(self, ctx: EnrichmentContext) -> list[EvidenceItem]:
        if not ctx.services:
            return []
        data = await self._http.get_json(
            "/loki/api/v1/query_range",
            {
                "query": _selector(ctx.services),
                "start": int(ctx.window_start.timestamp() * 1e9),
                "end": int(ctx.window_end.timestamp() * 1e9),
                "limit": MAX_LINES,
                "direction": "forward",
            },
        )
        lines: list[tuple[str, datetime, str, str | None]] = []
        for stream in cast(list[dict[str, Any]], data.get("data", {}).get("result", [])):
            labels = cast(dict[str, str], stream.get("stream", {}))
            service = labels.get("service_name", "?")
            level = labels.get("detected_level") or labels.get("severity_text")
            for ts, line, *_ in cast(list[list[Any]], stream.get("values", [])):
                at = datetime.fromtimestamp(int(ts) / 1e9, UTC)
                lines.append((service, at, str(line), level))
        total = len(lines)
        items: list[EvidenceItem] = []
        for pattern in group_logs(lines, top=TOP_PATTERNS):
            first = pattern.first_seen or ctx.started_at
            times = "once" if pattern.count == 1 else f"{pattern.count} times"
            items.append(
                EvidenceItem(
                    kind=EvidenceKind.LOG,
                    summary=(
                        f"{pattern.service} log pattern seen {times} since {first:%H:%M:%S}: "
                        f'"{pattern.template[:160]}"'
                    ),
                    observed_at=first,
                    dedupe_key=f"{pattern.service}:{pattern.template}",
                    payload={
                        "service": pattern.service,
                        "template": pattern.template,
                        "count": pattern.count,
                        "example": pattern.example,
                        "levels": sorted(pattern.levels),
                        "first_seen": first.isoformat(),
                        "last_seen": (pattern.last_seen or first).isoformat(),
                        "lines_scanned": total,
                        "capped": total >= MAX_LINES,
                    },
                )
            )
        return items

    async def aclose(self) -> None:
        await self._http.aclose()
