"""Route ESPN-shaped request paths to the trimmed real responses in ``tests/fixtures``.

Paths are relative to ``NCAAF_API_ROOT``: ``/site/...``, ``/site_v3/...``, ``/standings/...``,
``/core/...`` and ``/common/...`` mirror the five ESPN API roots used by the worker.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import cache
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@cache
def load(name: str) -> dict:
    """Load one fixture document."""
    return json.loads((FIXTURES / name).read_text())


def route(path: str, query: Mapping[str, str]) -> str | None:
    """Return the fixture filename for a request, or None for a 404."""
    if path == "/site/scoreboard":
        return (
            "scoreboard_fcs_2025_w1.json" if query.get("groups") == "81" else "scoreboard_fbs_2025_post.json"
        )
    if path == "/standings/standings":
        return "standings_fcs_2025.json" if query.get("group") == "81" else "standings_fbs_2025.json"
    if path == "/site/rankings":
        return "rankings_current.json"
    if path == "/site/teams":
        return "teams.json"
    if path == "/site/teams/84/schedule":
        return (
            "team_schedule_84_2025_post.json"
            if query.get("seasontype") == "3"
            else "team_schedule_84_2025_regular.json"
        )
    if path == "/site/teams/84/roster":
        return "roster_84.json"
    if path == "/site/teams/84/statistics":
        return "team_stats_84_2025.json"
    if path == "/site/summary" and query.get("event") == "401769076":
        return "summary_401769076.json"
    if path == "/site_v3/leaders":
        return "leaders_2025.json" if query.get("season") == "2025" else "leaders_current.json"
    if path == "/common/athletes/4837248/gamelog":
        return "gamelog_4837248_2025.json"
    if path == "/common/athletes/4837248/stats":
        return "athlete_stats_4837248.json"
    core = {
        "/core/seasons/2025/types/2/weeks/15/rankings": "core_rankings_2025_w15.json",
        "/core/seasons/2025/types/2/weeks/15/rankings/21": "core_ranking_2025_w15_21.json",
        "/core/seasons/2025/types/2/weeks/15/rankings/1": "core_ranking_2025_w15_1.json",
        "/core/seasons/2025/types/2/weeks/15/rankings/2": "core_ranking_2025_w15_2.json",
        "/core/seasons/2026/types/2/weeks/10/rankings/21": "core_ranking_2026_w10_cfp.json",
    }
    return core.get(path)


def body(path: str, query: Mapping[str, str]) -> bytes | None:
    """Return the fixture body for a request, or None for a 404."""
    name = route(path, query)
    return (FIXTURES / name).read_bytes() if name else None
