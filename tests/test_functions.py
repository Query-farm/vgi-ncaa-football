"""Execute every table function's process() in-process against the fixture-backed mock ESPN."""

from __future__ import annotations

import httpx
import pytest

from tests.conftest import MockEspn, run
from vgi_ncaa_football import api, games, league, players

TITLE_GAME = 401769076


def test_scoreboard_divisions_and_conference(espn: MockEspn) -> None:
    fbs = run(
        games.ScoreboardFunction,
        games.ScoreboardArgs(
            season=2025, week=None, season_type="postseason", division="fbs", conference_id=None
        ),
    )
    assert len(fbs.rows()) == 3
    assert fbs.cache_controls[0].ttl == 5 or fbs.cache_controls[0].ttl == api.HISTORICAL_TTL
    both = run(
        games.ScoreboardFunction,
        games.ScoreboardArgs(season=2025, week=1, season_type=None, division="all", conference_id=None),
    )
    assert len(both.rows()) == 5
    assert {request.url.params["groups"] for request in espn.requests[-2:]} == {"80", "81"}
    assert espn.requests[-1].url.params["seasontype"] == "2"  # season/week imply the regular season
    run(
        games.ScoreboardFunction,
        games.ScoreboardArgs(season=None, week=None, season_type=None, division="fbs", conference_id=5),
    )
    assert espn.requests[-1].url.params["groups"] == "5"


def test_current_scoreboard(espn: MockEspn) -> None:
    out = run(games.CurrentScoreboardFunction)
    assert len(out.rows()) == 3
    assert "dates" not in espn.requests[0].url.params


def test_team_schedule_merges_postseason(espn: MockEspn) -> None:
    out = run(games.TeamScheduleFunction, games.TeamScheduleArgs(team_id=84, season=2025))
    rows = out.rows()
    assert len(rows) == 4
    assert {row["season_type"] for row in rows} == {"regular", "postseason"}
    assert len({row["game_id"] for row in rows}) == 4


def test_team_schedule_without_postseason(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("seasontype") == "3":
            return httpx.Response(404, text="none")
        return httpx.Response(200, json={"events": []})

    real_client = api._client

    def client(given):
        return real_client(given or httpx.Client(transport=httpx.MockTransport(handler)))

    monkeypatch.setattr(api, "_client", client)
    assert run(games.TeamScheduleFunction, games.TeamScheduleArgs(team_id=9, season=2025)).rows() == []

    def failing(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("seasontype") == "3":
            return httpx.Response(403, text="forbidden")
        return httpx.Response(200, json={"events": []})

    api.clear_cache()
    monkeypatch.setattr(
        api, "_client", lambda given: real_client(httpx.Client(transport=httpx.MockTransport(failing)))
    )
    with pytest.raises(api.EspnApiError, match="403"):
        run(games.TeamScheduleFunction, games.TeamScheduleArgs(team_id=9, season=2025))


@pytest.mark.parametrize(
    ("function", "expected"),
    [
        (games.GameSummaryFunction, 1),
        (games.BoxScoreTeamFunction, 30),
        (games.DrivesFunction, 2),
        (games.PlayByPlayFunction, 6),
    ],
)
def test_game_detail_functions(espn: MockEspn, function, expected: int) -> None:
    out = run(function, games.GameArgs(game_id=TITLE_GAME))
    assert len(out.rows()) == expected


def test_box_score_player_and_cache_reuse(espn: MockEspn) -> None:
    out = run(games.BoxScorePlayerFunction, games.GameArgs(game_id=TITLE_GAME))
    assert any(row["player_name"] == "Fernando Mendoza" for row in out.rows())
    run(games.DrivesFunction, games.GameArgs(game_id=TITLE_GAME))
    assert espn.paths().count("/site/summary") == 1  # the second function reused the cached summary


def test_standings_teams_conferences(espn: MockEspn) -> None:
    standings = run(league.StandingsFunction, league.StandingsArgs(season=2025, division="fbs")).rows()
    assert len(standings) == 9
    current = run(league.CurrentStandingsFunction).rows()
    assert {row["division"] for row in current} == {"FBS", "FCS"}
    teams = run(league.TeamsFunction, league.TeamsArgs(division="fcs", season=None)).rows()
    assert {row["division"] for row in teams} == {"FCS"}
    conferences = run(league.ConferencesFunction, league.ConferencesArgs(division="all", season=2025)).rows()
    assert {row["division"] for row in conferences} == {"FBS", "FCS"}


def test_current_rankings_add_committee_poll(espn: MockEspn) -> None:
    rows = run(league.CurrentRankingsFunction).rows()
    assert {row["poll_id"] for row in rows} == {1, 2, 21}
    cfp = [row for row in rows if row["poll_id"] == 21]
    assert all(row["team_name"] for row in cfp)  # resolved from the team directory
    assert "/site/teams" in espn.paths()


def test_rankings_modes(espn: MockEspn) -> None:
    weekly = run(
        league.RankingsFunction, league.RankingsArgs(season=2025, week=15, season_type=None, poll="all")
    ).rows()
    assert {row["poll_id"] for row in weekly} == {21, 1, 2}
    cfp = run(
        league.RankingsFunction, league.RankingsArgs(season=2025, week=15, season_type=None, poll="cfp")
    ).rows()
    assert [row["team_name"] for row in cfp if row["rank"] and row["rank"] <= 2] == [
        "Ohio State Buckeyes",
        "Indiana Hoosiers",
    ]
    final = run(
        league.RankingsFunction, league.RankingsArgs(season=2025, week=None, season_type=None, poll="ap")
    ).rows()
    assert final == []  # the final postseason AP poll is not among the fixtures (404)
    assert "/core/seasons/2025/types/3/weeks/1/rankings/1" in espn.paths()
    latest = run(
        league.RankingsFunction, league.RankingsArgs(season=None, week=None, season_type=None, poll="coaches")
    ).rows()
    assert {row["poll_id"] for row in latest} == {2}
    run(league.RankingsFunction, league.RankingsArgs(season=None, week=3, season_type=None, poll="all"))
    assert f"/core/seasons/{api.current_season()}/types/2/weeks/3/rankings" in espn.paths()


def test_player_and_team_functions(espn: MockEspn) -> None:
    assert len(run(players.RosterFunction, players.TeamArgs(team_id=84)).rows()) == 6
    stats = run(players.TeamSeasonStatsFunction, players.TeamSeasonArgs(team_id=84, season=2025)).rows()
    assert stats and {row["side"] for row in stats} == {"team", "opponent"}
    leaders = run(players.StatLeadersFunction, players.LeadersArgs(season=2025)).rows()
    assert leaders[0]["player_name"] == "Drew Mestemaker"
    assert run(players.CurrentStatLeadersFunction).rows()
    log = run(players.PlayerGameLogFunction, players.PlayerSeasonArgs(player_id=4837248, season=2025)).rows()
    assert {row["game_id"] for row in log} >= {TITLE_GAME}
    seasons = run(players.PlayerSeasonStatsFunction, players.PlayerArgs(player_id=4837248)).rows()
    assert {row["season"] for row in seasons} == {2023, 2024, 2025}


def test_cache_control_requires_every_ttl() -> None:
    from vgi_ncaa_football.common import cache_control

    assert cache_control([60, 30]).ttl == 30
    assert cache_control([60, 0]) is None
    assert cache_control([]) is None


def test_cardinality_estimates_are_sane() -> None:
    from vgi_ncaa_football.worker import NCAA_FOOTBALL_CATALOG

    for function in NCAA_FOOTBALL_CATALOG.schemas[0].functions:
        estimate = function.cardinality(None)
        assert 1 <= estimate.estimate <= estimate.max, function.Meta.name
