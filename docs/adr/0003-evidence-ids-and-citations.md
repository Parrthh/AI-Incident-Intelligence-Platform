# 0003: Evidence IDs and citation validation

- Status: accepted
- Date: 2026-11-01

## Context

The platform's root-cause analysis is written by a language model. Models state wrong things confidently, and an on-call engineer under pressure may act on them. Each claim has to be checkable, and the system must notice when the model makes something up.

## Decision

- Every piece of evidence gets a short, stable ID (`ev_trace_1`, ADR-0010). The prompt lists evidence as `[ev_trace_1] ...` and tells the model to cite only those IDs.
- The model answers in JSON (Gemini's JSON mode), and the reply is validated against a Pydantic schema: summary, timeline, ranked hypotheses (service, category from a fixed list, explanation, confidence 0 to 1, evidence IDs, next steps), and missing information. A reply that doesn't parse is asked for once more, then recorded as failed.
- **Citation validation in code:** IDs that don't exist are removed. A hypothesis left without a valid citation is dropped, and the rest are re-ranked. The counts (cited, invalid, which IDs, hypotheses dropped) are stored with the analysis and exported as `iip_analysis_citations_total{outcome}`. The invalid rate is the hallucination metric the eval reports.
- **Context budget:** evidence is ranked (the incident's own services, then deploys, traces, logs, metrics, alerts; closer to the start first; bigger counts first) and added until a token budget (6,000 by default) is full. What didn't fit is stored with the analysis.
- The analysis input is fingerprinted (`input_hash`), so the same evidence is never analyzed twice, and new evidence produces a new analysis.

## Consequences

- The UI shows only grounded hypotheses, each linking to its evidence, plus how many citations were checked and how many were removed.
- Dropping a hypothesis can leave an incident with none. The page says so instead of showing an unsupported guess.
- The fixed category list makes scoring against chaos scenarios possible (top-1 and top-3 accuracy on service and category).
- Removing a citation doesn't prove the rest of the explanation is right: the eval measures that.
