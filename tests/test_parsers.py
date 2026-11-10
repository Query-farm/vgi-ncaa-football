"""Parsing and schema tests against trimmed real ESPN responses."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pyarrow as pa
import pytest

from tests.espn_fixtures import load
from vgi_ncaa_football import parsers as p
from vgi_ncaa_football import schemas as s
from vgi_ncaa_football.meta import comment_of, result_columns_schema, sql_type

TITLE_GAME = 401769076


def _batch(rows, schema: pa.Schema) -> pa.RecordBatch:
    batch = p.batch_from_rows(rows, schema)
    assert batch.schema == schema
    return batch


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("27", 27),
        (27.0, 27),
        (27.5, None),
        ("1,234", 1234),
        ("", None),
        (True, None),
        ("x", None),
        (None, None),
    ],
)
def test_to_int(value, expected) -> None:
    assert p.to_int(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("4,379", 4379.0),
        ("6.3", 6.3),
        ("-12", -12.0),
        ("6-14", None),
        ("29:05", None),
        ("--", None),
        ("-", None),
    ],
)
def test_to_float_accepts_only_single_numbers(value, expected) -> None:
    assert p.to_float(value) == expected
    assert p.to_float(float("nan")) is None
    assert p.to_float(False) is None
    assert p.to_float(["1"]) is None


def test_scalar_helpers() -> None:
    assert p.parse_record("13-2") == (13, 2, 0)
    assert p.parse_record("5-4-1") == (5, 4, 1)
    assert p.parse_record("n/a") == (None, None, None)
    assert p.to_decimal("0.8666", "0.001") == Decimal("0.867")
    assert p.to_decimal("Infinity", "0.1") is None
    assert p.to_decimal("abc", "0.1") is None
    assert p.to_timestamp("2026-01-20T00:30Z") == datetime(2026, 1, 20, 0, 30, tzinfo=UTC)
    assert p.to_timestamp("2025-12-27T22:45:00.000+00:00") == datetime(2025, 12, 27, 22, 45, tzinfo=UTC)
    assert p.to_timestamp("2025-01-01T00:00:00") == datetime(2025, 1, 1, tzinfo=UTC)
    assert p.to_timestamp("garbage") is None
    assert p.text("  ") is None
    assert p.text(7) == "7"
    assert p.text(["x"]) is None
    assert p.season_type_name({"type": 3}) == "postseason"
    assert p.season_type_name(9) is None
    assert p.ref_season("x/seasons/2024/types/3/weeks/1") == 2024
    assert p.ref_id_type("x/seasons/2024/types/3/weeks/1") == 3


def test_scoreboard_events_flatten_to_game_rows() -> None:
    rows = p.flatten_events(load("scoreboard_fbs_2025_post.json")["events"])
    batch = _batch(rows, s.GAME_SCHEMA)
    assert batch.num_rows == 3
    final = next(row for row in rows if row["game_id"] == TITLE_GAME)
    assert final["season"] == 2025
    assert final["season_type"] == "postseason"
    assert final["home_team_name"] == "Indiana Hoosiers"
    assert (final["home_score"], final["away_score"]) == (27, 21)
    assert final["home_winner"] is True and final["away_winner"] is False
    assert final["home_rank"] == 1 and final["away_rank"] == 10
    assert final["notes"].startswith("College Football Playoff National Championship")
    assert final["venue_name"] == "Hard Rock Stadium"
    assert final["completed"] is True


def test_pregame_scores_are_null_and_unranked_is_null() -> None:
    event = load("scoreboard_fcs_2025_w1.json")["events"][0]
    pregame = {**event, "competitions": [dict(event["competitions"][0])]}
    pregame["competitions"][0]["status"] = {"type": {"state": "pre", "name": "STATUS_SCHEDULED"}}
    row = p.flatten_event(pregame)
    assert row["home_score"] is None and row["away_winner"] is None
    assert row["home_rank"] is None  # ESPN's 99 means unranked
    assert p.flatten_event({"competitions": []}) is None
    assert p.flatten_events([{"id": "1"}, {"id": "1"}, "junk"]) == [p.flatten_event({"id": "1"})]


def test_schedule_events_use_object_scores() -> None:
    rows = p.flatten_events(load("team_schedule_84_2025_regular.json")["events"])
    assert rows[0]["home_team_abbreviation"] == "IU"
    assert rows[0]["home_score"] == 27 and rows[0]["away_score"] == 14
    assert rows[0]["broadcast"] == "FS1"
    _batch(rows, s.GAME_SCHEMA)


def test_summary_header_with_line_scores() -> None:
    rows = p.flatten_summary_game(load("summary_401769076.json"))
    batch = _batch(rows, s.GAME_SUMMARY_SCHEMA)
    row = batch.to_pylist()[0]
    assert row["name"] == "Miami Hurricanes at Indiana Hoosiers"
    assert row["short_name"] == "MIA @ IU"
    assert row["home_line_scores"] == [3, 7, 7, 10]
    assert sum(row["away_line_scores"]) == row["away_score"] == 21
    assert row["period"] == 4
    assert row["attendance"] == 67227
    assert "ERADM" not in (row["broadcast"] or "")  # radio feeds are not TV broadcasts
    assert p.flatten_summary_game({"header": {}}) == []


def test_box_scores_are_long_format() -> None:
    summary = load("summary_401769076.json")
    team = p.flatten_box_team(summary, TITLE_GAME)
    _batch(team, s.BOX_TEAM_SCHEMA)
    yards = {row["team_abbreviation"]: row["value"] for row in team if row["stat_name"] == "totalYards"}
    assert yards == {"MIA": 342.0, "IU": 317.0}
    third = next(row for row in team if row["stat_name"] == "thirdDownEff")
    assert third["value"] is None and "-" in third["display_value"]

    players = p.flatten_box_players(summary, TITLE_GAME)
    _batch(players, s.BOX_PLAYER_SCHEMA)
    passing = {
        row["player_name"]: row["value"]
        for row in players
        if row["category"] == "passing" and row["stat_name"] == "passingYards"
    }
    assert passing == {"Carson Beck": 232.0, "Fernando Mendoza": 186.0}


def test_drives_and_plays() -> None:
    summary = load("summary_401769076.json")
    drives = p.flatten_drives(summary, TITLE_GAME)
    _batch(drives, s.DRIVE_SCHEMA)
    assert [row["drive_number"] for row in drives] == [1, 2]
    assert drives[0]["result"] == "PUNT"
    plays = p.flatten_plays(summary, TITLE_GAME)
    _batch(plays, s.PLAY_SCHEMA)
    assert plays[0]["play_type"] == "Kickoff"
    assert plays[0]["offense_team_id"] == 2390 and plays[0]["start_team_id"] == 84
    assert all(row["drive_id"] for row in plays)


def test_standings_tree_flattens_conferences_and_divisions() -> None:
    rows = p.flatten_standings(load("standings_fbs_2025.json"))
    _batch(rows, s.STANDINGS_SCHEMA)
    sec = [row for row in rows if row["conference_abbreviation"] == "sec"]
    assert sec[0]["team_name"] == "Ole Miss Rebels"
    assert (sec[0]["wins"], sec[0]["losses"]) == (13, 2)
    assert sec[0]["win_percentage"] == Decimal("0.867")
    assert sec[0]["conference_games_back"] == Decimal("0.0")
    assert sec[1]["conference_games_back"] == Decimal("0.5")
    sun_belt = [row for row in rows if row["conference_id"] == 37]
    assert {row["subdivision_name"] for row in sun_belt} == {"Sun Belt - East", "Sun Belt - West"}
    fcs = p.flatten_standings(load("standings_fcs_2025.json"))
    assert {row["division"] for row in fcs} == {"FCS"}


def test_teams_and_conferences_from_standings() -> None:
    payload = load("standings_fbs_2025.json")
    teams = p.flatten_teams(payload)
    _batch(teams, s.TEAM_SCHEMA)
    assert {row["division"] for row in teams} == {"FBS"}
    assert len({row["team_id"] for row in teams}) == len(teams)
    conferences = p.flatten_conferences(payload)
    _batch(conferences, s.CONFERENCE_SCHEMA)
    by_id = {row["conference_id"]: row for row in conferences}
    assert by_id[37]["subdivision_count"] == 2
    assert by_id[37]["team_count"] == 6
    assert by_id[8]["subdivision_count"] == 0


def test_site_poll_and_receiving_votes() -> None:
    rows = p.flatten_poll(load("rankings_current.json")["rankings"][0])
    _batch(rows, s.RANKING_SCHEMA)
    assert rows[0]["rank"] == 1 and rows[0]["team_name"] == "Georgia Bulldogs"
    assert rows[0]["poll_id"] == 1 and rows[0]["season_type"] == "regular"
    others = [row for row in rows if row["receiving_votes"]]
    assert others and all(row["rank"] is None for row in others)


def test_core_poll_resolves_team_names_from_directory() -> None:
    directory = p.team_directory(load("teams.json"))
    rows = p.flatten_poll(load("core_ranking_2025_w15_21.json"), directory)
    _batch(rows, s.RANKING_SCHEMA)
    assert [(row["rank"], row["team_name"]) for row in rows[:2]] == [
        (1, "Ohio State Buckeyes"),
        (2, "Indiana Hoosiers"),
    ]
    assert rows[0]["season"] == 2025 and rows[0]["week"] == 15 and rows[0]["season_type"] == "regular"
    unresolved = p.flatten_poll(load("core_ranking_2025_w15_21.json"))
    assert unresolved[0]["team_id"] == 194 and unresolved[0]["team_name"] is None


def test_roster_and_team_stats() -> None:
    roster = p.flatten_roster(load("roster_84.json"), 84)
    _batch(roster, s.ROSTER_SCHEMA)
    assert {row["unit"] for row in roster} >= {"offense", "defense"}
    assert all(row["height_inches"] for row in roster)
    stats = p.flatten_team_stats(load("team_stats_84_2025.json"), 84)
    _batch(stats, s.TEAM_STATS_SCHEMA)
    assert {row["side"] for row in stats} == {"team", "opponent"}
    assert all(row["season"] == 2025 for row in stats)
    keys = [(row["side"], row["category"], row["stat_name"]) for row in stats]
    assert len(keys) == len(set(keys))


def test_leaders() -> None:
    rows = p.flatten_leaders(load("leaders_2025.json"))
    _batch(rows, s.LEADER_SCHEMA)
    top = next(row for row in rows if row["category"] == "passingYards" and row["rank"] == 1)
    assert top["player_name"] == "Drew Mestemaker" and top["value"] == 4379.0
    assert top["team_abbreviation"] == "UNT"
    assert not [row for row in rows if row["category"] == "kickoffYards"]  # ESPN sends it empty


def test_game_log_expands_category_runs() -> None:
    rows = p.flatten_game_log(load("gamelog_4837248_2025.json"), 4837248, 2025)
    _batch(rows, s.GAME_LOG_SCHEMA)
    final = [row for row in rows if row["game_id"] == TITLE_GAME]
    by_stat = {row["stat_name"]: row for row in final}
    assert by_stat["passingYards"]["category"] == "passing"
    assert by_stat["rushingYards"]["category"] == "rushing"
    assert by_stat["passingYards"]["value"] == 186.0
    assert final[0]["season_type"] == "postseason" and final[0]["result"] == "W"
    assert final[0]["home_away"] == "home" and final[0]["opponent_abbreviation"] == "MIA"


def test_player_seasons_include_transfer_schools() -> None:
    rows = p.flatten_player_seasons(load("athlete_stats_4837248.json"), 4837248)
    _batch(rows, s.PLAYER_SEASON_SCHEMA)
    passing = {
        (row["season"], row["team_abbreviation"]): row["value"]
        for row in rows
        if row["stat_name"] == "passingYards"
    }
    assert passing[(2023, "CAL")] == 1708.0
    assert passing[(2025, "IU")] == 3535.0


def test_every_schema_is_fully_commented_and_renderable() -> None:
    for name in dir(s):
        if name.endswith("_SCHEMA"):
            schema = getattr(s, name)
            assert all(comment_of(column) for column in schema), name
            assert result_columns_schema(schema)
    assert sql_type(pa.list_(pa.int32())) == "INTEGER[]"
    assert sql_type(pa.date32()) == "DATE"
    assert sql_type(pa.timestamp("us")) == "TIMESTAMP"
    with pytest.raises(ValueError):
        sql_type(pa.binary())


def test_projected_schema_batches() -> None:
    rows = p.flatten_events(load("scoreboard_fbs_2025_post.json")["events"])
    projected = pa.schema([s.GAME_SCHEMA.field("home_score"), s.GAME_SCHEMA.field("game_id")])
    batch = p.batch_from_rows(rows, projected)
    assert batch.schema.names == ["home_score", "game_id"]
