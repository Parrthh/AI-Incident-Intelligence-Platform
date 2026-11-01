"""The shape of an analysis, and how a model's reply is parsed into it.

``category`` is a fixed list on purpose: free-text causes can't be scored, a
fixed list can (the eval in M4 compares it with the ground truth of each chaos
scenario).
"""

import json
import re
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, ValidationError


class Category(StrEnum):
    BAD_DEPLOY = "bad_deploy"
    CODE_BUG = "code_bug"
    DEPENDENCY_FAILURE = "dependency_failure"
    SERVICE_DOWN = "service_down"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    NETWORK_LATENCY = "network_latency"
    CONFIG_ERROR = "config_error"
    UNKNOWN = "unknown"


class TimelineEntry(BaseModel):
    at: Annotated[str, Field(max_length=40)]
    description: Annotated[str, Field(max_length=500)]
    evidence_ids: list[str] = Field(default_factory=list[str])


class Hypothesis(BaseModel):
    rank: Annotated[int, Field(ge=1, le=10)]
    root_cause_service: Annotated[str, Field(min_length=1, max_length=200)]
    category: Category
    explanation: Annotated[str, Field(min_length=1, max_length=2000)]
    confidence: Annotated[float, Field(ge=0, le=1)]
    evidence_ids: list[str] = Field(default_factory=list[str])
    next_steps: list[Annotated[str, Field(max_length=500)]] = Field(default_factory=list[str])


class AnalysisResult(BaseModel):
    summary: Annotated[str, Field(min_length=1, max_length=2000)]
    timeline: list[TimelineEntry] = Field(default_factory=list[TimelineEntry])
    hypotheses: list[Hypothesis] = Field(default_factory=list[Hypothesis], max_length=10)
    missing_information: list[Annotated[str, Field(max_length=500)]] = Field(
        default_factory=list[str]
    )


class InvalidAnalysisError(ValueError):
    """The model's reply isn't a valid analysis (not JSON, or the wrong shape)."""


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_analysis(text: str) -> AnalysisResult:
    """Parse a reply. Tolerates code fences around the JSON; nothing else."""
    cleaned = _FENCE.sub("", text.strip())
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise InvalidAnalysisError(f"reply is not JSON: {exc.msg} at char {exc.pos}") from exc
    try:
        return AnalysisResult.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise InvalidAnalysisError(
            f"reply has the wrong shape: {where}: {first['msg']} ({exc.error_count()} errors)"
        ) from exc
