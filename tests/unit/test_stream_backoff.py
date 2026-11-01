from typing import Any, cast

from iip.queue.streams import Message, StreamQueue


def make_queue() -> StreamQueue:
    return StreamQueue(redis=cast(Any, None), stream="s", group="g", consumer="c")


def test_backoff_doubles_per_delivery_and_is_capped() -> None:
    queue = make_queue()
    assert [queue.backoff_s(n) for n in range(1, 5)] == [120, 240, 480, 600]
    assert queue.backoff_s(50) == queue.max_backoff_s


def test_worst_case_time_to_dead_letter_is_bounded() -> None:
    # Total wait across all retries before giving up: 2+4+8+10 = 24 minutes.
    queue = make_queue()
    assert sum(queue.backoff_s(n) for n in range(1, queue.max_deliveries)) == 24 * 60


def test_dlq_name() -> None:
    assert make_queue().dlq == "s.dlq"


def test_message_from_fields() -> None:
    message = Message.from_fields(
        "1-0",
        {"type": "summarize", "payload": '{"incident_id":"x"}', "created_at": "12.5"},
        deliveries=3,
    )
    assert message == Message(
        id="1-0", type="summarize", payload={"incident_id": "x"}, created_at=12.5, deliveries=3
    )
