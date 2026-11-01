"""Print failed and errored tests from a JUnit XML report as GitHub annotations.

Annotations show up on the PR page, so a failing test is visible without
opening the job log. Always exits 0: the pytest step already failed the job.
"""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def _escape(text: str) -> str:
    # GitHub workflow commands: %, CR and LF must be encoded in the message.
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "junit.xml")
    if not path.is_file():
        print(f"no report at {path}")
        return 0
    root = ET.parse(path).getroot()  # noqa: S314 - trusted local file
    for case in root.iter("testcase"):
        for kind in ("failure", "error"):
            node = case.find(kind)
            if node is None:
                continue
            name = f"{case.get('classname', '')}.{case.get('name', '')}"
            file = case.get("file") or case.get("classname", "").replace(".", "/") + ".py"
            line = int(case.get("line") or 0) + 1
            detail = (node.text or node.get("message") or "")[-3000:]
            print(f"::error file={file},line={line},title={kind}: {name}::{_escape(detail)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
