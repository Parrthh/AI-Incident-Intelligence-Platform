"""Fit an incident's evidence into the model's prompt, most useful first.

The prompt has a token budget. Evidence is ranked, then added until the budget
is full; what didn't fit is recorded, so it's visible what the model never saw.

Ranking, most useful first:
1. evidence about the incident's own services before evidence about others;
2. by kind: deploys (the classic cause), then failing traces, log patterns,
   metrics, and the alerts (already summarized in the header);
3. closer to the incident start before further away;
4. bigger counts (more failing spans, more log lines) first.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime

from iip.db.models import Evidence

KIND_RANK = {"deploy": 0, "trace": 1, "log": 2, "metric": 3, "alert": 4}
CHARS_PER_TOKEN = 4  # rough, but stable and provider-independent


@dataclass(frozen=True)
class RenderedEvidence:
    text: str
    included: list[str]
    dropped: list[str]
    tokens_estimate: int

    def fingerprint(self, prompt_id: str) -> str:
        digest = hashlib.sha256(f"{prompt_id}\n{self.text}".encode()).hexdigest()
        return digest[:16]


def rank(evidence: list[Evidence], services: set[str], started_at: datetime) -> list[Evidence]:
    def key(e: Evidence) -> tuple[bool, int, float, int]:
        service = str(e.payload.get("service") or "")
        count = int(e.payload.get("count") or 0)
        distance = abs((e.observed_at - started_at).total_seconds())
        return (service not in services, KIND_RANK.get(e.kind, 9), distance, -count)

    return sorted(evidence, key=key)


def line(e: Evidence) -> str:
    text = f"[{e.id}] {e.kind}, {e.observed_at:%H:%M:%S}: {e.summary}"
    example = e.payload.get("example")
    if e.kind == "log" and example and str(example) not in e.summary:
        text += f"\n    example line: {str(example)[:240]}"
    return text


def render_evidence(
    evidence: list[Evidence], services: set[str], started_at: datetime, budget_tokens: int
) -> RenderedEvidence:
    lines: list[str] = []
    included: list[str] = []
    dropped: list[str] = []
    used = 0
    for item in rank(evidence, services, started_at):
        entry = line(item)
        cost = len(entry) // CHARS_PER_TOKEN + 1
        if used + cost > budget_tokens:
            dropped.append(item.id)
            continue
        lines.append(entry)
        included.append(item.id)
        used += cost
    return RenderedEvidence("\n".join(lines) or "(no evidence)", included, dropped, used)
