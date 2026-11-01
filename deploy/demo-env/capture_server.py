"""Tiny HTTP receiver that saves every POST body to a file (chaos runs only).

Alertmanager sends each webhook here too, so the exact payloads from a real
Alertmanager can be committed as test fixtures. Standard library only.
"""

import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

OUT = Path(os.environ.get("CAPTURE_DIR", "/captures"))


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        try:
            parsed = json.loads(body)
            name = f"{time.time_ns()}_{parsed.get('status', 'unknown')}.json"
            (OUT / name).write_text(json.dumps(parsed, indent=2))
        except ValueError:
            (OUT / f"{time.time_ns()}_invalid.txt").write_bytes(body)
        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        print(format % args, flush=True)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()  # noqa: S104
