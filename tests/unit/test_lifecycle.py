from datetime import UTC, datetime, timedelta

import pytest

from iip.core.lifecycle import InvalidTransitionError, Status, check_transition, ready_to_resolve

NOW = datetime(2026, 11, 1, 15, 0, tzinfo=UTC)
QUIET = timedelta(minutes=5)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (Status.OPEN, Status.ANALYZING),
        (Status.OPEN, Status.RESOLVED),
        (Status.ANALYZING, Status.ANALYZED),
        (Status.ANALYZING, Status.OPEN),  # analysis failed; back to waiting
        (Status.ANALYZED, Status.ANALYZING),  # re-analysis after new alerts
        (Status.ANALYZED, Status.RESOLVED),
    ],
)
def test_allowed_transitions(current: Status, target: Status) -> None:
    check_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (Status.RESOLVED, Status.OPEN),  # a new alert opens a new incident instead
        (Status.RESOLVED, Status.ANALYZING),
        (Status.OPEN, Status.ANALYZED),  # can't skip analysis
        (Status.OPEN, Status.OPEN),
    ],
)
def test_forbidden_transitions(current: Status, target: Status) -> None:
    with pytest.raises(InvalidTransitionError):
        check_transition(current, target)


@pytest.mark.parametrize(
    ("statuses", "last_minutes_ago", "ready"),
    [
        (["resolved"], 6, True),
        (["resolved", "resolved"], 5, True),  # exactly the quiet period
        (["resolved"], 4, False),  # still in the quiet period
        (["resolved", "firing"], 60, False),  # one alert still firing
        ([], 60, False),  # no alerts at all: nothing to go on
    ],
)
def test_ready_to_resolve(statuses: list[str], last_minutes_ago: float, ready: bool) -> None:
    last = NOW - timedelta(minutes=last_minutes_ago)
    assert ready_to_resolve(statuses, last, NOW, QUIET) is ready


def test_not_ready_without_an_end_time() -> None:
    assert ready_to_resolve(["resolved"], None, NOW, QUIET) is False
