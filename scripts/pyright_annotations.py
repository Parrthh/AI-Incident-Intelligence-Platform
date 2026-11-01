"""Run pyright and print each error as a GitHub Actions annotation.

GitHub shows annotations inline on the PR diff, so type errors are visible
without opening the job log. Exit code matches pyright's.
"""

import json
import subprocess
import sys


def main() -> int:
    proc = subprocess.run(  # noqa: S603 - fixed command, no user input
        ["pyright", "--outputjson", *sys.argv[1:]],  # noqa: S607 - resolved from the venv PATH
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        return proc.returncode or 1

    for diag in report.get("generalDiagnostics", []):
        severity = diag.get("severity", "error")
        level = {"error": "error", "warning": "warning"}.get(severity, "notice")
        start = diag.get("range", {}).get("start", {})
        line = start.get("line", 0) + 1
        col = start.get("character", 0) + 1
        rule = diag.get("rule", "")
        message = diag.get("message", "").replace("\n", " ")
        file = diag.get("file", "")
        print(f"::{level} file={file},line={line},col={col},title=pyright {rule}::{message}")

    summary = report.get("summary", {})
    print(
        f"pyright: {summary.get('errorCount', 0)} errors, "
        f"{summary.get('warningCount', 0)} warnings, "
        f"{summary.get('filesAnalyzed', 0)} files"
    )
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
