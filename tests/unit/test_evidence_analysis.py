from datetime import UTC, datetime, timedelta

from iip.enrichment.analysis import describe_series, group_logs, log_template

T0 = datetime(2026, 11, 1, 14, 0, tzinfo=UTC)


def series(values: list[float], start_minute: int = -5) -> list[tuple[datetime, float]]:
    return [(T0 + timedelta(minutes=start_minute + i), v) for i, v in enumerate(values)]


def test_rise_is_found_with_its_start_and_peak() -> None:
    # baseline 2% for 5 minutes, then it climbs to 100%
    points = series([0.02, 0.02, 0.03, 0.02, 0.02, 0.4, 1.0, 1.0, 0.9])
    change = describe_series(points, T0, min_absolute_change=0.05)

    assert change is not None
    assert change.baseline == 0.02
    assert change.peak == 1.0
    assert change.peak_at == T0 + timedelta(minutes=1)
    assert change.changed
    assert change.changed_at == T0  # first point above the threshold


def test_small_wiggle_is_not_a_change() -> None:
    points = series([0.02, 0.03, 0.02, 0.02, 0.02, 0.04, 0.05, 0.03])
    change = describe_series(points, T0, min_absolute_change=0.05)

    assert change is not None
    assert not change.changed
    assert change.changed_at is None


def test_no_data_after_the_start() -> None:
    assert describe_series(series([0.1, 0.1], start_minute=-5), T0) is None


def test_no_baseline_counts_as_changed() -> None:
    change = describe_series(series([5.0, 9.0], start_minute=0), T0)
    assert change is not None
    assert change.baseline is None
    assert change.changed


def test_log_template_masks_variable_parts() -> None:
    line = (
        "2026-11-01T14:38:21.510Z Payment request failed. Invalid token. "
        "user=42 order 'abc-123' trace 4bf92f3577b34da6a3ce929d0e0e4736 from 10.0.3.7:5050 "
        "id 3f2a9c1e-1b2c-4d5e-8f90-123456789abc took 532ms"
    )
    assert log_template(line) == (
        "<time> Payment request failed. Invalid token. user=<num> order <str> trace <hex> "
        "from <ip> id <uuid> took <num>"
    )


def test_group_logs_counts_patterns_per_service_biggest_first() -> None:
    lines = [
        ("payment", T0 + timedelta(seconds=3), "charge failed for user 7", "ERROR"),
        ("payment", T0 + timedelta(seconds=1), "charge failed for user 42", "ERROR"),
        ("payment", T0 + timedelta(seconds=2), "charge failed for user 8", None),
        ("checkout", T0, "payment call failed: timeout after 5s", "warn"),
        ("payment", T0, "   ", "ERROR"),  # blank lines are ignored
    ]

    patterns = group_logs(lines)

    assert [(p.service, p.template, p.count) for p in patterns] == [
        ("payment", "charge failed for user <num>", 3),
        ("checkout", "payment call failed: timeout after <num>", 1),
    ]
    first = patterns[0]
    assert first.first_seen == T0 + timedelta(seconds=1)
    assert first.last_seen == T0 + timedelta(seconds=3)
    assert first.example == "charge failed for user 42"  # the earliest line
    assert first.levels == {"ERROR"}
