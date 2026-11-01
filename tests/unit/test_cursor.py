import uuid
from datetime import UTC, datetime

import pytest

from iip.incidents.queries import InvalidCursorError, decode_cursor, encode_cursor


def test_cursor_round_trips() -> None:
    started = datetime(2026, 11, 1, 4, 10, 30, 123456, tzinfo=UTC)
    ident = uuid.uuid4()

    assert decode_cursor(encode_cursor(started, ident)) == (started, ident)


@pytest.mark.parametrize("bad", ["", "not-base64!!", "aGVsbG8", "MjAyNi0xMS0wMXxub3QtYS11dWlk"])
def test_garbage_cursor_is_rejected(bad: str) -> None:
    with pytest.raises(InvalidCursorError):
        decode_cursor(bad)
