"""Print the chaos job matrix: one entry per scenario and repeat.

SCENARIOS=all REPEAT=2 python chaos/plan_matrix.py
"""

import json
import os
from pathlib import Path

SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios"


def main() -> None:
    available = sorted(p.stem for p in SCENARIO_DIR.glob("*.yaml"))
    raw = os.environ.get("SCENARIOS", "payment_failure,payment_unreachable").strip()
    wanted = available if raw == "all" else [s.strip() for s in raw.split(",") if s.strip()]
    unknown = sorted(set(wanted) - set(available))
    if unknown:
        raise SystemExit(f"unknown scenarios: {unknown}; available: {available}")
    repeat = int(os.environ.get("REPEAT", "1"))
    runs = [{"scenario": s, "run": r} for s in wanted for r in range(1, repeat + 1)]
    print(json.dumps({"include": runs}))


if __name__ == "__main__":
    main()
