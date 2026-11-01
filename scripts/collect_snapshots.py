"""Collect eval snapshots printed by chaos runs into evals/datasets/.

Chaos runs print each snapshot (gzip, base64) between SNAPSHOT-BEGIN and
SNAPSHOT-END markers in the job log. This reads the logs of a workflow run's
jobs through the GitHub API and writes one JSON file per snapshot:

    GH_TOKEN=... python scripts/collect_snapshots.py --run-id 41350105091
"""

import argparse
import base64
import gzip
import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any

API = "https://api.github.com/repos/Parrthh/AI-Incident-Intelligence-Platform"
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z ")


def _get(url: str) -> bytes:
    token = os.environ["GH_TOKEN"]
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})  # noqa: S310
    with urllib.request.urlopen(request, timeout=60) as resp:  # noqa: S310
        return resp.read()


def snapshots_in_log(text: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    chunks: list[str] | None = None
    for raw in text.splitlines():
        line = STAMP.sub("", raw).strip()
        if line.startswith("SNAPSHOT-BEGIN"):
            chunks = []
        elif line == "SNAPSHOT-END" and chunks is not None:
            found.append(json.loads(gzip.decompress(base64.b64decode("".join(chunks)))))
            chunks = None
        elif chunks is not None:
            chunks.append(line)
    return found


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--out", default="evals/datasets")
    args = parser.parse_args()
    jobs = json.loads(_get(f"{API}/actions/runs/{args.run_id}/jobs?per_page=100"))["jobs"]
    written = 0
    for job in jobs:
        if job.get("conclusion") != "success" or not job["name"].startswith("chaos"):
            continue
        for snapshot in snapshots_in_log(_get(f"{API}/actions/jobs/{job['id']}/logs").decode()):
            path = Path(args.out) / snapshot["scenario"]["name"] / f"{snapshot['id']}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
            written += 1
            print(f"wrote {path}")
    print(f"{written} snapshots from {len(jobs)} jobs")


if __name__ == "__main__":
    main()
