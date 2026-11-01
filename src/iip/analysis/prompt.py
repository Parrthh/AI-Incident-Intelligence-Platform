"""Build the analysis prompt from plain data.

Shared by the live analyzer (data from the database) and the offline eval (data
from recorded snapshots), so the eval measures exactly the prompt production
sends.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from iip.analysis.context import RenderedEvidence, render_evidence
from iip.db.models import Alert, Evidence
from iip.llm.prompting import Prompt
from iip.llm.redact import redact
from iip.llm.summarize import format_alerts


@dataclass(frozen=True)
class BuiltPrompt:
    text: str  # redacted
    redactions: int
    rendered: RenderedEvidence


def build_analysis_prompt(
    prompt: Prompt,
    *,
    title: str,
    status: str,
    started_at: datetime,
    services: Sequence[str],
    alerts: Sequence[Alert],
    evidence: Sequence[Evidence],
    missing: Mapping[str, str],
    budget_tokens: int,
) -> BuiltPrompt:
    rendered = render_evidence(list(evidence), set(services), started_at, budget_tokens)
    text = prompt.render(
        title=title,
        status=status,
        started_at=started_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
        services=", ".join(services) or "unknown",
        alerts=format_alerts(list(alerts)),
        missing="\n".join(f"- {r}" for r in missing.values()) or "(none)",
        evidence=rendered.text,
    )
    redacted = redact(text)
    return BuiltPrompt(redacted.text, redacted.total, rendered)
