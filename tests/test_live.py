"""Opt-in smoke tests against ESPN's public college-football endpoints.

Historical assertions pin the completed 2025 season so they stay stable.
"""

from __future__ import annotations

import os

import pytest

from vgi_ncaa_football import api, parsers

pytestmark = pytest.mark.live
TITLE_GAME = 401769076


@pytest.fixture(autouse=True)
def _live_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(api.ROOT_ENV, raising=False)
    monkeypatch.setenv("NCAAF_MIN_REQUEST_INTERVAL", os.environ.get("NCAAF_MIN_REQUEST_INTERVAL", "0.25"))


def test_current_fbs_standings() -> None:
    rows = parsers.flatten_standings(api.fetch_standings(group=api.DIVISION_GROUPS["fbs"]).payload)
    assert len(rows) >= 120
    assert {row["division"] for row in rows} == {"FBS"}
    assert len({row["team_id"] for row in rows}) == len(rows)


def test_2025_fbs_standings_records_balance() -> None:
    rows = parsers.flatten_standings(api.fetch_standings(group=80, season=2025).payload)
    assert len(rows) == 136
    big_ten = [row for row in rows if row["conference_abbreviation"] == "big10"]
    assert len(big_ten) == 18
    indiana = next(row for row in big_ten if row["team_id"] == 84)
    assert (indiana["wins"], indiana["losses"], indiana["conference_record"]) == (16, 0, "9-0")
    # Within a conference, conference wins and losses must balance.
    for conference in {row["conference_id"] for row in rows if row["conference_abbreviation"] != "ind"}:
        members = [row for row in rows if row["conference_id"] == conference]
        assert sum(row["conference_wins"] for row in members) == sum(
            row["conference_losses"] for row in members
        )


def test_postseason_scoreboard_and_title_game() -> None:
    events = api.fetch_scoreboard(group=80, season=2025, season_type="postseason").payload["events"]
    rows = parsers.flatten_events(events)
    assert len(rows) >= 40
    final = next(row for row in rows if row["game_id"] == TITLE_GAME)
    assert (final["home_team_abbreviation"], final["home_score"], final["away_score"]) == ("IU", 27, 21)


def test_fcs_scoreboard_week_one() -> None:
    rows = parsers.flatten_events(api.fetch_scoreboard(group=81, season=2025, week=1).payload["events"])
    assert len(rows) >= 40


def test_game_detail() -> None:
    payload = api.fetch_summary(TITLE_GAME).payload
    [game] = parsers.flatten_summary_game(payload)
    assert sum(game["home_line_scores"]) == 27
    players = parsers.flatten_box_players(payload, TITLE_GAME)
    passing = {
        row["player_name"]: row["value"]
        for row in players
        if row["category"] == "passing" and row["stat_name"] == "passingYards"
    }
    assert passing["Fernando Mendoza"] == 186.0
    drives = parsers.flatten_drives(payload, TITLE_GAME)
    plays = parsers.flatten_plays(payload, TITLE_GAME)
    assert len(drives) >= 15 and len(plays) >= 120
    assert plays[-1]["home_score"] == 27 and plays[-1]["away_score"] == 21


def test_rankings_current_and_historical() -> None:
    current = parsers.flatten_poll(api.fetch_site_rankings().payload["rankings"][0])
    assert len([row for row in current if row["rank"]]) == 25
    directory = parsers.team_directory(api.fetch_team_directory().payload)
    response = api.fetch_core_poll(season=2025, season_type="regular", week=15, poll_id=21)
    assert response is not None
    cfp = parsers.flatten_poll(response.payload, directory)
    assert [row["team_name"] for row in cfp[:2]] == ["Ohio State Buckeyes", "Indiana Hoosiers"]
    assert 21 in api.fetch_core_poll_ids(season=2025, season_type="regular", week=15)


def test_team_schedule_roster_and_stats() -> None:
    regular = parsers.flatten_events(
        api.fetch_team_schedule(84, season=2025, season_type="regular").payload["events"]
    )
    post = parsers.flatten_events(
        api.fetch_team_schedule(84, season=2025, season_type="postseason").payload["events"]
    )
    assert len(regular) + len(post) == 16
    assert len(parsers.flatten_roster(api.fetch_roster(84).payload, 84)) >= 50
    stats = parsers.flatten_team_stats(api.fetch_team_statistics(84, season=2025).payload, 84)
    rushing = next(
        row
        for row in stats
        if (row["side"], row["category"], row["stat_name"]) == ("team", "rushing", "rushingYards")
    )
    assert rushing["value"] == 3406


def test_player_endpoints() -> None:
    leaders = parsers.flatten_leaders(api.fetch_leaders(season=2025).payload)
    top = next(row for row in leaders if row["category"] == "passingYards" and row["rank"] == 1)
    assert top["player_name"] == "Drew Mestemaker"
    log = parsers.flatten_game_log(api.fetch_player_game_log(4837248, season=2025).payload, 4837248, 2025)
    yards = [row["value"] for row in log if row["stat_name"] == "passingYards"]
    assert len(yards) == 16 and sum(yards) == 3535
    seasons = parsers.flatten_player_seasons(api.fetch_player_season_stats(4837248).payload, 4837248)
    assert {row["team_abbreviation"] for row in seasons} >= {"CAL", "IU"}
