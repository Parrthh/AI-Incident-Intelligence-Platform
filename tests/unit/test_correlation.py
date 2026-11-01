"""Correlation rules, table-driven. See src/iip/core/correlation.py."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from iip.core.correlation import AlertFacts, OpenIncident, Rule, correlate
from iip.core.graph import ServiceGraph, load_graph

T0 = datetime(2026, 11, 1, 14, 0, tzinfo=UTC)
WINDOW = timedelta(minutes=10)
GRAPH = ServiceGraph.from_calls({"checkout": ["payment", "cart"], "frontend": ["checkout"]})

PAYMENT = uuid.UUID(int=1)
CART = uuid.UUID(int=2)
OLD_PAYMENT = uuid.UUID(int=3)


def alert(service: str | None, minutes: float = 0, fingerprint: str = "new") -> AlertFacts:
    return AlertFacts(
        fingerprint=fingerprint,
        alertname="HighErrorRate",
        service=service,
        starts_at=T0 + timedelta(minutes=minutes),
    )


def incident(
    id_: uuid.UUID, services: set[str], minutes: float = 0, fingerprints: set[str] | None = None
) -> OpenIncident:
    return OpenIncident(
        id=id_,
        started_at=T0 + timedelta(minutes=minutes),
        services=frozenset(services),
        fingerprints=frozenset(fingerprints or set()),
    )


CASES = [
    # (name, alert, open incidents, expected incident or None, expected rule)
    ("nothing open", alert("payment"), [], None, Rule.NEW),
    (
        "same service",
        alert("payment", 2),
        [incident(PAYMENT, {"payment"})],
        PAYMENT,
        Rule.SAME_SERVICE,
    ),
    (
        "caller of an affected service",
        alert("checkout", 1),
        [incident(PAYMENT, {"payment"})],
        PAYMENT,
        Rule.CONNECTED_SERVICE,
    ),
    (
        "callee of an affected service",
        alert("payment", 1),
        [incident(CART, {"checkout"})],
        CART,
        Rule.CONNECTED_SERVICE,
    ),
    (
        "two hops away is not connected",
        alert("frontend", 1),
        [incident(PAYMENT, {"payment"})],
        None,
        Rule.NEW,
    ),
    ("unrelated service", alert("email", 1), [incident(PAYMENT, {"payment"})], None, Rule.NEW),
    (
        "outside the window",
        alert("payment", 11),
        [incident(PAYMENT, {"payment"})],
        None,
        Rule.NEW,
    ),
    (
        "exactly at the window edge",
        alert("payment", 10),
        [incident(PAYMENT, {"payment"})],
        PAYMENT,
        Rule.SAME_SERVICE,
    ),
    (
        "alert that started a little before the incident (late delivery)",
        alert("checkout", -3),
        [incident(PAYMENT, {"payment"})],
        PAYMENT,
        Rule.CONNECTED_SERVICE,
    ),
    (
        "same fingerprint wins even outside the window (flapping alert)",
        alert("payment", 120, fingerprint="fp-pay"),
        [incident(PAYMENT, {"payment"}, fingerprints={"fp-pay"})],
        PAYMENT,
        Rule.SAME_FINGERPRINT,
    ),
    (
        "same service beats connected service",
        alert("checkout", 5),
        [incident(PAYMENT, {"payment"}, minutes=4), incident(CART, {"checkout"}, minutes=1)],
        CART,
        Rule.SAME_SERVICE,
    ),
    (
        "two connected incidents: the most recent wins",
        alert("checkout", 5),
        [incident(PAYMENT, {"payment"}, minutes=1), incident(CART, {"cart"}, minutes=3)],
        CART,
        Rule.CONNECTED_SERVICE,
    ),
    (
        "an old incident on the same service doesn't capture a new alert",
        alert("payment", 60),
        [incident(OLD_PAYMENT, {"payment"}), incident(PAYMENT, {"payment"}, minutes=55)],
        PAYMENT,
        Rule.SAME_SERVICE,
    ),
    ("no service label", alert(None, 1), [incident(PAYMENT, {"payment"})], None, Rule.NEW),
    (
        "no service label but a known fingerprint",
        alert(None, 1, fingerprint="fp-x"),
        [incident(PAYMENT, {"payment"}, fingerprints={"fp-x"})],
        PAYMENT,
        Rule.SAME_FINGERPRINT,
    ),
]


@pytest.mark.parametrize(
    ("alert_facts", "open_incidents", "expected", "rule"),
    [c[1:] for c in CASES],
    ids=[c[0] for c in CASES],
)
def test_correlation_rules(
    alert_facts: AlertFacts,
    open_incidents: list[OpenIncident],
    expected: uuid.UUID | None,
    rule: Rule,
) -> None:
    decision = correlate(alert_facts, open_incidents, GRAPH, WINDOW)

    assert decision.incident_id == expected
    assert decision.rule == rule
    assert decision.is_new == (expected is None)
    assert decision.reason  # every decision explains itself


def test_reasons_are_readable() -> None:
    decision = correlate(alert("checkout", 1), [incident(PAYMENT, {"payment"})], GRAPH, WINDOW)

    assert decision.reason == (
        "HighErrorRate is on checkout, which is connected to payment in the service graph, "
        "and it started within 10 minutes of the incident."
    )


def test_without_a_graph_only_same_service_groups() -> None:
    empty = ServiceGraph()
    assert correlate(alert("checkout", 1), [incident(PAYMENT, {"payment"})], empty, WINDOW).is_new


def test_graph_is_undirected_and_ignores_self_calls() -> None:
    graph = ServiceGraph.from_calls({"a": ["b", "a"]})
    assert graph.connected("a", "b")
    assert graph.connected("b", "a")
    assert not graph.connected("a", "a")
    assert graph.neighbors("missing") == frozenset()


def test_builtin_otel_demo_graph_links_payment_and_checkout() -> None:
    graph = load_graph("builtin:otel-demo")
    assert graph.connected("checkout", "payment")
    assert graph.connected("frontend", "checkout")
    assert not graph.connected("payment", "frontend")
    assert load_graph(None) == ServiceGraph()
