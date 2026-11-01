"""Citation validation: every claim must point at evidence that exists (ADR-0003).

Models sometimes cite evidence IDs they were never given. Those citations are
removed, and a hypothesis left with no valid citation is dropped: an ungrounded
root cause is worse than none. The counts are kept with the analysis, because
the rate of invented citations is a direct measure of hallucination.
"""

from dataclasses import dataclass

from iip.analysis.schema import AnalysisResult


@dataclass(frozen=True)
class CitationReport:
    cited: int  # citations in the reply
    invalid: int  # citations to IDs that don't exist
    invalid_ids: list[str]
    hypotheses_dropped: int

    @property
    def validity(self) -> float | None:
        return None if self.cited == 0 else (self.cited - self.invalid) / self.cited


def validate_citations(
    result: AnalysisResult, known_ids: set[str]
) -> tuple[AnalysisResult, CitationReport]:
    cited = 0
    invalid: list[str] = []
    kept = []
    dropped = 0
    for hypothesis in sorted(result.hypotheses, key=lambda h: h.rank):
        cited += len(hypothesis.evidence_ids)
        valid = [i for i in dict.fromkeys(hypothesis.evidence_ids) if i in known_ids]
        invalid += [i for i in hypothesis.evidence_ids if i not in known_ids]
        if not valid:
            dropped += 1
            continue
        kept.append(hypothesis.model_copy(update={"evidence_ids": valid, "rank": len(kept) + 1}))
    timeline = []
    for entry in result.timeline:
        cited += len(entry.evidence_ids)
        invalid += [i for i in entry.evidence_ids if i not in known_ids]
        timeline.append(
            entry.model_copy(
                update={"evidence_ids": [i for i in entry.evidence_ids if i in known_ids]}
            )
        )
    cleaned = result.model_copy(update={"hypotheses": kept, "timeline": timeline})
    report = CitationReport(
        cited=cited,
        invalid=len(invalid),
        invalid_ids=sorted(set(invalid))[:20],
        hypotheses_dropped=dropped,
    )
    return cleaned, report
