import pytest

from iip.web.pages import prompt_version


@pytest.mark.parametrize(
    ("stored", "shown"),
    [("summary_v0", "0"), ("summary_v12", "12"), ("root_cause_v3", "3"), ("custom", "custom")],
)
def test_prompt_version_filter(stored: str, shown: str) -> None:
    assert prompt_version(stored) == shown
