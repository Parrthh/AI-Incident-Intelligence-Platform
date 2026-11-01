import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent


def load_alertmanager(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / "alertmanager" / f"{name}.json").read_text())
