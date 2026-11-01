"""Service dependency graph: which services call which.

Correlation only needs to know whether two services are directly connected, in
either direction: if checkout calls payment and payment breaks, checkout's
errors are a symptom of the same problem.

A graph is a YAML file with a ``calls`` map (``caller: [callee, ...]``). One for
the OpenTelemetry Demo ships with the package (``builtin:otel-demo``). M3's
Jaeger connector can later build the same structure from real traces.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path

import yaml

BUILTIN_PREFIX = "builtin:"


@dataclass(frozen=True)
class ServiceGraph:
    _neighbors: Mapping[str, frozenset[str]] = field(default_factory=dict[str, frozenset[str]])

    @classmethod
    def from_calls(cls, calls: Mapping[str, Iterable[str]]) -> "ServiceGraph":
        neighbors: dict[str, set[str]] = {}
        for caller, callees in calls.items():
            for callee in callees:
                if callee == caller:
                    continue
                neighbors.setdefault(caller, set()).add(callee)
                neighbors.setdefault(callee, set()).add(caller)
        return cls({name: frozenset(peers) for name, peers in neighbors.items()})

    def connected(self, a: str, b: str) -> bool:
        """True if one of the two services calls the other."""
        return b in self._neighbors.get(a, frozenset())

    def neighbors(self, service: str) -> frozenset[str]:
        return self._neighbors.get(service, frozenset())

    def __len__(self) -> int:
        return len(self._neighbors)


EMPTY_GRAPH = ServiceGraph()


def load_graph(source: str | None) -> ServiceGraph:
    """Load ``builtin:<name>`` or a YAML file path. ``None`` means no graph."""
    if not source:
        return EMPTY_GRAPH
    if source.startswith(BUILTIN_PREFIX):
        name = source.removeprefix(BUILTIN_PREFIX)
        text = files("iip.core").joinpath(f"graphs/{name}.yaml").read_text(encoding="utf-8")
    else:
        text = Path(source).read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    calls = data.get("calls") or {}
    if not isinstance(calls, dict):
        raise ValueError("service graph: `calls` must be a map of caller to a list of callees")
    return ServiceGraph.from_calls(
        {str(k): [str(v) for v in (vs or [])] for k, vs in calls.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    )
