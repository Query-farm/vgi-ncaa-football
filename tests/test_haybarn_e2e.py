"""Hermetic end-to-end coverage through Haybarn and the VGI transport.

A local HTTP server replays the trimmed real ESPN responses in ``tests/fixtures`` and the
worker is pointed at it with ``NCAAF_API_ROOT``; no request leaves the machine.
"""

from __future__ import annotations

import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

from tests import espn_fixtures
from vgi_ncaa_football import parsers


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


def _expected_counts() -> dict[str, int]:
    load = espn_fixtures.load
    rankings = load("rankings_current.json")["rankings"]
    polls = sum(len(parsers.flatten_poll(poll)) for poll in rankings)
    polls += len(parsers.flatten_poll(load("core_ranking_2026_w10_cfp.json")))
    return {
        "scoreboard": len(parsers.flatten_events(load("scoreboard_fbs_2025_post.json")["events"])),
        "standings": len(parsers.flatten_standings(load("standings_fbs_2025.json")))
        + len(parsers.flatten_standings(load("standings_fcs_2025.json"))),
        "rankings": polls,
        "leaders": len(parsers.flatten_leaders(load("leaders_current.json"))),
    }


def test_haybarn_attaches_and_queries_every_current_table() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _EspnHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    project = Path(__file__).resolve().parent.parent
    location = f"uv run --project {project} vgi-ncaa-football"
    sql = (
        f"INSTALL vgi FROM community; LOAD vgi; ATTACH 'ncaa_football' (TYPE vgi, LOCATION '{location}'); "
        "SELECT "
        "(SELECT count(*) FROM ncaa_football.main.current_scoreboard) AS scoreboard, "
        "(SELECT count(*) FROM ncaa_football.main.current_standings) AS standings, "
        "(SELECT count(*) FROM ncaa_football.main.current_rankings) AS rankings, "
        "(SELECT count(*) FROM ncaa_football.main.current_stat_leaders) AS leaders, "
        "(SELECT home_score || '-' || away_score FROM ncaa_football.main.game_summary(401769076)) AS final, "
        "(SELECT count(*) FROM ncaa_football.main.team_schedule(84, season := 2025)) AS schedule;"
    )
    env = {
        **os.environ,
        "NCAAF_API_ROOT": f"http://127.0.0.1:{server.server_port}",
        "NCAAF_MIN_REQUEST_INTERVAL": "0",
    }
    try:
        result = subprocess.run(
            ["uvx", "haybarn-cli", ":memory:", "-csv", "-c", sql],
            cwd=project,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    expected = _expected_counts()
    assert all(expected.values())
    assert "scoreboard,standings,rankings,leaders,final,schedule" in result.stdout
    counts = [expected[name] for name in ("scoreboard", "standings", "rankings", "leaders")]
    row = ",".join(str(count) for count in counts) + ",27-21,4"
    assert row in result.stdout, result.stdout + result.stderr
