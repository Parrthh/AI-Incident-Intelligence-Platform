"""Score analyses against ground truth, one snapshot at a time, then in total.

- **top-1**: the first hypothesis names the right service *and* category.
- **top-3**: one of the first three does.
- **service top-1**: the first hypothesis names the right service (any category).
- **citation validity**: share of citations that point at real evidence.
- **calibration**: mean confidence of the top hypothesis when right vs wrong.
  A well-calibrated model is more confident when it's right.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from statistics import mean, median

from iip.analysis.citations import CitationReport
from iip.analysis.schema import AnalysisResult
from iip.evals.snapshot import Snapshot


@dataclass(frozen=True)
class CaseResult:
    snapshot_id: str
    scenario: str
    ok: bool  # the model produced a valid analysis
    error: str | None = None
    top: tuple[str, str] | None = None
    top_confidence: float | None = None
    top1: bool = False
    top3: bool = False
    service_top1: bool = False
    service_top3: bool = False
    cited: int = 0
    invalid_citations: int = 0
    hypotheses_dropped: int = 0
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    evidence_included: int = 0
    evidence_dropped: int = 0


def score_case(
    snapshot: Snapshot,
    result: AnalysisResult,
    report: CitationReport,
    *,
    latency_ms: int | None,
    input_tokens: int | None,
    output_tokens: int | None,
    included: int,
    dropped: int,
) -> CaseResult:
    ranked = sorted(result.hypotheses, key=lambda h: h.rank)
    pairs = [(h.root_cause_service, h.category.value) for h in ranked]
    truth = snapshot.scenario
    return CaseResult(
        snapshot_id=snapshot.id,
        scenario=truth.name,
        ok=True,
        top=pairs[0] if pairs else None,
        top_confidence=ranked[0].confidence if ranked else None,
        top1=bool(pairs) and truth.accepts(*pairs[0]),
        top3=any(truth.accepts(*p) for p in pairs[:3]),
        service_top1=bool(pairs) and truth.accepts_service(pairs[0][0]),
        service_top3=any(truth.accepts_service(p[0]) for p in pairs[:3]),
        cited=report.cited,
        invalid_citations=report.invalid,
        hypotheses_dropped=report.hypotheses_dropped,
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        evidence_included=included,
        evidence_dropped=dropped,
    )


def failed_case(snapshot: Snapshot, error: str) -> CaseResult:
    return CaseResult(snapshot.id, snapshot.scenario.name, ok=False, error=error[:300])


def _rate(values: list[bool]) -> float | None:
    return None if not values else sum(values) / len(values)


@dataclass
class Summary:
    cases: int
    failed: int
    top1: float | None
    top3: float | None
    service_top1: float | None
    service_top3: float | None
    citation_validity: float | None
    hypotheses_dropped: int
    confidence_when_right: float | None
    confidence_when_wrong: float | None
    latency_ms_p50: float | None
    latency_ms_max: int | None
    input_tokens_mean: float | None
    output_tokens_mean: float | None
    per_scenario: dict[str, dict[str, float | int | None]] = field(
        default_factory=dict[str, dict[str, float | int | None]]
    )


def summarize(cases: list[CaseResult]) -> Summary:
    # A failed case counts as wrong: an unusable answer helps nobody.
    cited = sum(c.cited for c in cases)
    invalid = sum(c.invalid_citations for c in cases)
    right = [c.top_confidence for c in cases if c.top1 and c.top_confidence is not None]
    wrong = [
        c.top_confidence for c in cases if c.ok and not c.top1 and c.top_confidence is not None
    ]
    latencies = [c.latency_ms for c in cases if c.latency_ms is not None]
    inputs = [c.input_tokens for c in cases if c.input_tokens is not None]
    outputs = [c.output_tokens for c in cases if c.output_tokens is not None]
    by_scenario: dict[str, list[CaseResult]] = defaultdict(list)
    for c in cases:
        by_scenario[c.scenario].append(c)
    return Summary(
        cases=len(cases),
        failed=sum(not c.ok for c in cases),
        top1=_rate([c.top1 for c in cases]),
        top3=_rate([c.top3 for c in cases]),
        service_top1=_rate([c.service_top1 for c in cases]),
        service_top3=_rate([c.service_top3 for c in cases]),
        citation_validity=None if cited == 0 else (cited - invalid) / cited,
        hypotheses_dropped=sum(c.hypotheses_dropped for c in cases),
        confidence_when_right=mean(right) if right else None,
        confidence_when_wrong=mean(wrong) if wrong else None,
        latency_ms_p50=median(latencies) if latencies else None,
        latency_ms_max=max(latencies) if latencies else None,
        input_tokens_mean=mean(inputs) if inputs else None,
        output_tokens_mean=mean(outputs) if outputs else None,
        per_scenario={
            name: {
                "cases": len(group),
                "top1": _rate([c.top1 for c in group]),
                "top3": _rate([c.top3 for c in group]),
                "service_top1": _rate([c.service_top1 for c in group]),
            }
            for name, group in sorted(by_scenario.items())
        },
    )
