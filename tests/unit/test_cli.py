import pytest

from iip import __version__
from iip.cli import main


def test_version_command_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == __version__


def test_unknown_command_exits_with_usage_error() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["nope"])
    assert exc_info.value.code == 2


def test_command_is_required() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 2
