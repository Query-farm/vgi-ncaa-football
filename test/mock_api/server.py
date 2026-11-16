"""Serve the trimmed real ESPN responses in tests/fixtures over HTTP for the SQL tests.

``ci/run-integration.sh`` starts this server, points ``NCAAF_API_ROOT`` at it,
and runs ``test/sql`` through DuckDB, so no request leaves the machine.
Prints ``PORT:<n>`` once listening.
"""

from __future__ import annotations

import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests import espn_fixtures  # noqa: E402


class _EspnHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        content = espn_fixtures.body(parsed.path, dict(parse_qsl(parsed.query)))
        if content is None:
            content = b'{"error":{"message":"no instance found","code":404}}'
            self.send_response(404)
        else:
            self.send_response(200)
            self.send_header("Cache-Control", "max-age=60")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def main() -> None:
    """Serve until killed."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _EspnHandler)
    print(f"PORT:{server.server_port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
