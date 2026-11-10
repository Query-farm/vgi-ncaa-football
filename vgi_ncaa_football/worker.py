"""VGI worker exposing NCAA FBS and FCS college football as a read-only SQL catalog."""

from __future__ import annotations

import json
import os
import sys

from vgi import Worker
from vgi.catalog import Catalog, ConstraintInfo, Schema, Table, constraint_record

from vgi_ncaa_football.games import GAME_FUNCTIONS, CurrentScoreboardFunction
from vgi_ncaa_football.league import LEAGUE_FUNCTIONS, CurrentRankingsFunction, CurrentStandingsFunction
from vgi_ncaa_football.meta import column_comments
from vgi_ncaa_football.players import PLAYER_FUNCTIONS, CurrentStatLeadersFunction
from vgi_ncaa_football.schemas import GAME_SCHEMA, LEADER_SCHEMA, RANKING_SCHEMA, STANDINGS_SCHEMA

SOURCE_URL = "https://github.com/Query-farm/vgi-ncaa-football"
PROVIDER = "espn"


def _table_tags(
    *, category: str, title: str, keywords: list[str], llm: str, md: str, examples: list[tuple[str, str]]
) -> dict[str, str]:
    return {
        "provider": PROVIDER,
        "vgi.category": category,
        "vgi.title": title,
        "vgi.keywords": json.dumps(keywords),
        "vgi.doc_llm": llm,
        "vgi.doc_md": md,
        "vgi.example_queries": json.dumps([{"description": text, "sql": sql} for text, sql in examples]),
    }


_SCOREBOARD_TAGS = _table_tags(
    category="scores",
    title="Current FBS Scoreboard",
    keywords=["scores", "games", "this week", "kickoff", "live", "results"],
    llm=(
        "This week's FBS games, one row per game: kickoff time, status (pre/in/post), rankings, venue, "
        "broadcast and home/away scores. Start here for 'what games are on' or 'who won'. For another "
        "week, the postseason, FCS or one conference, call scoreboard()."
    ),
    md=(
        "ESPN's current FBS week as an ordinary read-only table. Scores are NULL before kickoff. Use "
        "`scoreboard()` for any other week and `game_summary()` for line scores."
    ),
    examples=[
        (
            "This week's ranked matchups.",
            "SELECT start_time, away_rank, away_team_name, home_rank, home_team_name, status_detail "
            "FROM ncaa_football.main.current_scoreboard "
            "WHERE home_rank IS NOT NULL OR away_rank IS NOT NULL ORDER BY start_time",
        ),
        (
            "This week's games grouped by state (scheduled, live, final).",
            "SELECT status_state, count(*) AS games FROM ncaa_football.main.current_scoreboard "
            "GROUP BY status_state ORDER BY status_state",
        ),
    ],
)

_STANDINGS_TAGS = _table_tags(
    category="standings",
    title="Current Conference Standings",
    keywords=["standings", "conference", "records", "wins", "losses", "FBS", "FCS"],
    llm=(
        "Current FBS and FCS conference standings, one row per team: division, conference, "
        "conference_rank, overall and conference records, points for/against and streak. Filter "
        "division = 'FBS' or 'FCS'. For a past season call standings(season := ...)."
    ),
    md=(
        "Current Division I standings as an ordinary table. `conference_rank` follows ESPN's "
        "standings order; split conferences carry `subdivision_name`."
    ),
    examples=[
        (
            "Current SEC standings in order.",
            "SELECT conference_rank, team_name, overall_record, conference_record "
            "FROM ncaa_football.main.current_standings WHERE conference_abbreviation = 'sec' "
            "ORDER BY conference_rank",
        ),
        (
            "Unbeaten FBS teams.",
            "SELECT team_name, conference_name, overall_record FROM ncaa_football.main.current_standings "
            "WHERE division = 'FBS' AND losses = 0 ORDER BY wins DESC",
        ),
    ],
)

_RANKINGS_TAGS = _table_tags(
    category="rankings",
    title="Current Poll Rankings",
    keywords=["rankings", "AP poll", "coaches poll", "CFP", "playoff committee", "top 25"],
    llm=(
        "The latest AP Top 25 (poll_id 1), AFCA Coaches Poll (2), College Football Playoff committee "
        "ranking (21), FCS Coaches Poll (20) and Division II/III polls (11/12): one row per team per poll "
        "with rank, points, first-place votes, trend and record. rank is NULL for teams only receiving "
        "votes. For past weeks call rankings(season := ..., week := ...)."
    ),
    md="Latest published college-football polls, including the CFP committee ranking once released.",
    examples=[
        (
            "The current AP top ten.",
            "SELECT rank, team_name, record, first_place_votes FROM ncaa_football.main.current_rankings "
            "WHERE poll_id = 1 AND rank <= 10 ORDER BY rank",
        ),
        (
            "Where the AP and CFP rankings disagree.",
            "SELECT ap.team_name, ap.rank AS ap_rank, cfp.rank AS cfp_rank "
            "FROM ncaa_football.main.current_rankings ap JOIN ncaa_football.main.current_rankings cfp "
            "USING (team_id) WHERE ap.poll_id = 1 AND cfp.poll_id = 21 AND ap.rank <> cfp.rank "
            "ORDER BY cfp.rank",
        ),
    ],
)

_LEADERS_TAGS = _table_tags(
    category="player-stats",
    title="Current National Stat Leaders",
    keywords=["leaders", "passing yards", "rushing yards", "receiving", "tackles", "sacks", "players"],
    llm=(
        "Current-season national top-ten individual leaders, one row per category and player: "
        "passingYards, passingTouchdowns, rushingYards, rushingTouchdowns, receivingYards, receptions, "
        "receivingTouchdowns, totalTackles, sacks, interceptions and more. Use stat_leaders(season := ...) "
        "for past seasons and player_game_log() for one player's games."
    ),
    md="ESPN's current national leaderboards, ten players per category.",
    examples=[
        (
            "Current rushing-yards leaders.",
            "SELECT rank, player_name, team_abbreviation, value FROM ncaa_football.main.current_stat_leaders "
            "WHERE category = 'rushingYards' ORDER BY rank",
        )
    ],
)

_EXECUTABLE_EXAMPLES = json.dumps(
    [
        {
            "name": "current_standings_are_populated",
            "description": "Current Division I standings contain FBS teams with stable ids.",
            "sql": (
                "SELECT count(*) > 100 AND count(team_id) = count(*) AS populated "
                "FROM ncaa_football.main.current_standings WHERE division = 'FBS'"
            ),
            "expected_result": [{"populated": True}],
        },
        {
            "name": "current_rankings_have_an_ap_top_25",
            "description": "The latest AP poll ranks 25 teams.",
            "sql": (
                "SELECT count(*) = 25 AS ranked FROM ncaa_football.main.current_rankings "
                "WHERE poll_id = 1 AND rank IS NOT NULL"
            ),
            "expected_result": [{"ranked": True}],
        },
        {
            "name": "national_title_game_2025",
            "description": "Indiana beat Miami 27-21 in the 2025 national championship game.",
            "sql": (
                "SELECT home_team_name, home_score, away_team_name, away_score "
                "FROM ncaa_football.main.game_summary(401769076)"
            ),
            "expected_result": [
                {
                    "home_team_name": "Indiana Hoosiers",
                    "home_score": 27,
                    "away_team_name": "Miami Hurricanes",
                    "away_score": 21,
                }
            ],
        },
        {
            "name": "cfp_week15_number_one",
            "description": "Ohio State topped the 2025 week-15 CFP committee ranking.",
            "sql": (
                "SELECT team_name "
                "FROM ncaa_football.main.rankings(season := 2025, week := 15, poll := 'cfp') "
                "WHERE rank = 1"
            ),
            "expected_result": [{"team_name": "Ohio State Buckeyes"}],
        },
    ]
)

AGENT_TASKS: list[dict[str, str]] = [
    {
        "name": "national_title_result",
        "prompt": (
            "Who won the College Football Playoff national championship game that ended the 2025 "
            "season, and what was the final score?"
        ),
    },
    {
        "name": "indiana_2025_schedule",
        "prompt": (
            "List every game Indiana played in the 2025 season, bowls and playoff included, with the "
            "opponent and final score."
        ),
    },
    {
        "name": "cfp_week15_top_four",
        "prompt": "Which four teams led the College Football Playoff committee rankings in week 15 of 2025?",
    },
    {
        "name": "big_ten_final_standings",
        "prompt": (
            "How many teams were in the Big Ten in 2025, and what were the final Big Ten standings with "
            "each team's conference record?"
        ),
    },
    {
        "name": "passing_yards_leader_2025",
        "prompt": "Who led the nation in passing yards in the 2025 college football season?",
    },
    {
        "name": "title_game_box_score",
        "prompt": (
            "In ESPN game 401769076, how many total yards did each team gain, and how many passing yards "
            "did each team's quarterback throw for?"
        ),
    },
    {
        "name": "title_game_scoring_drives",
        "prompt": "List the scoring drives in ESPN game 401769076 and describe the play that scored on each.",
    },
    {
        "name": "mendoza_passing_by_game_and_season",
        "prompt": (
            "Show Fernando Mendoza's (ESPN player id 4837248) passing yards in each 2025 game, and his "
            "passing yards for every college season he has played."
        ),
    },
    {
        "name": "indiana_roster_and_rushing",
        "prompt": (
            "How many players are on Indiana's current roster (ESPN team id 84), and how many rushing "
            "yards did Indiana gain in the 2025 season?"
        ),
    },
    {
        "name": "current_snapshot",
        "prompt": (
            "Give me a snapshot of college football right now: this week's FBS games, the current AP top "
            "five, each conference's current standings leader, and the national passing-yards leader."
        ),
    },
]


_CATEGORIES = json.dumps(
    [
        {
            "name": "scores",
            "title": "Scores and Schedules",
            "description": "Weekly scoreboards, postseason bowls and playoff, and team schedules.",
            "keywords": ["scores", "schedule", "games", "bowls", "playoff"],
        },
        {
            "name": "standings",
            "title": "Standings",
            "description": "Conference standings with overall and conference records.",
            "keywords": ["standings", "conference", "records"],
        },
        {
            "name": "rankings",
            "title": "Rankings",
            "description": "AP, Coaches, College Football Playoff committee and FCS polls.",
            "keywords": ["AP poll", "coaches poll", "CFP", "top 25"],
        },
        {
            "name": "teams",
            "title": "Teams and Conferences",
            "description": "Division I team and conference directories with ESPN ids.",
            "keywords": ["teams", "conferences", "ids"],
        },
        {
            "name": "games",
            "title": "Game Detail",
            "description": "Game summaries, team and player box scores, drives and play-by-play.",
            "keywords": ["box score", "drives", "play-by-play", "line score"],
        },
        {
            "name": "team-stats",
            "title": "Team Statistics",
            "description": "Team season totals and opponents' totals.",
            "keywords": ["team stats", "offense", "defense"],
        },
        {
            "name": "players",
            "title": "Players",
            "description": "Current team rosters.",
            "keywords": ["roster", "players", "positions"],
        },
        {
            "name": "player-stats",
            "title": "Player Statistics",
            "description": "National leaderboards, player game logs and career season lines.",
            "keywords": ["leaders", "game log", "career", "passing", "rushing"],
        },
    ]
)


def _key_constraints(table: str, key: tuple[str, ...]) -> tuple[ConstraintInfo, ...]:
    """Primary-key and NOT NULL records for a read-only table keyed by ``key``."""
    records = [
        constraint_record(
            ["main"],
            table,
            "pk",
            "PRIMARY_KEY",
            [[column] for column in key],
            enforcement="ENFORCED",
            validation="ALL_ROWS",
        )
    ]
    records += [
        constraint_record(
            ["main"],
            table,
            f"{column}_not_null",
            "NOT_NULL",
            [[column]],
            enforcement="ENFORCED",
            validation="ALL_ROWS",
        )
        for column in key
    ]
    return tuple(records)


NCAA_FOOTBALL_CATALOG = Catalog(
    name="ncaa_football",
    default_schema="main",
    source_url=SOURCE_URL,
    comment="Read-only NCAA FBS and FCS college football scores, standings, rankings and statistics",
    tags={
        "provider": PROVIDER,
        "domain": "sports",
        "vgi.title": "NCAA College Football Data",
        "vgi.subtitle": "FBS and FCS scores, standings, polls, box scores and player statistics as SQL.",
        "vgi.source_url": SOURCE_URL,
        "vgi.author": "Query Farm LLC <hello@query.farm>",
        "vgi.copyright": (
            "Worker (c) 2026 Query Farm LLC. Data is fetched on demand from ESPN's undocumented public "
            "endpoints and remains subject to ESPN's terms of use; not affiliated with ESPN, Disney "
            "or the NCAA."
        ),
        "vgi.license": "MIT",
        "vgi.support_contact": "https://github.com/Query-farm/vgi-ncaa-football/issues",
        "vgi.support_policy_url": "https://github.com/Query-farm/vgi-ncaa-football/blob/main/README.md",
        "vgi.keywords": json.dumps(
            ["college football", "NCAA", "FBS", "FCS", "CFP", "AP poll", "bowl games", "standings"]
        ),
        "vgi.executable_examples": _EXECUTABLE_EXAMPLES,
        "vgi.agent_test_tasks": json.dumps(AGENT_TASKS),
        "vgi.doc_llm": (
            "Read-only NCAA Division I college football (FBS and FCS) from ESPN's public JSON feeds. "
            "Current tables cover this week's FBS scoreboard, FBS/FCS conference standings, the latest "
            "AP/Coaches/CFP/FCS polls and national stat leaders. Functions take ESPN ids: teams() and "
            "conferences() look up team_id and conference_id; scoreboard() and team_schedule() return "
            "game_id values for game_summary(), box_score_team(), box_score_player(), drives() and "
            "play_by_play(). Seasons are labelled by their starting year (2025 = 2025-26, including "
            "January bowls and the CFP final). Statistics functions return long format: filter on "
            "category and stat_name, then use value."
        ),
        "vgi.doc_md": (
            "# NCAA college football\n\n"
            "Query FBS and FCS college football from SQL: weekly scoreboards, bowls and the College "
            "Football Playoff, conference standings, AP/Coaches/CFP polls, team and player box scores, "
            "drives, play-by-play, team season statistics, national leaders and player game logs.\n\n"
            "## Data model\n\n"
            "The `current_*` tables are the quickest entry point. Parameterized functions select seasons, "
            "weeks, divisions, conferences, teams, games or players by ESPN id. Statistics are long "
            "format (one row per statistic) because ESPN's stat sets differ by position and game.\n\n"
            "## Source\n\n"
            "Data is fetched on demand from ESPN's undocumented public endpoints, cached briefly in "
            "process and never stored or redistributed by the worker. ESPN's terms apply; this worker is "
            "not affiliated with ESPN, The Walt Disney Company or the NCAA."
        ),
    },
    schemas=[
        Schema(
            path=["main"],
            comment="Current and historical NCAA FBS and FCS football data",
            tags={
                "provider": PROVIDER,
                "domain": "sports",
                "vgi.title": "College Football",
                "vgi.keywords": json.dumps(
                    ["college football", "scores", "standings", "rankings", "box score", "play-by-play"]
                ),
                "vgi.categories": _CATEGORIES,
                "vgi.doc_llm": (
                    "Use current_scoreboard for this week's FBS games, current_standings for conference "
                    "tables, current_rankings for the latest polls and current_stat_leaders for national "
                    "leaders. Functions cover past weeks and seasons, FCS, conferences, team schedules, "
                    "rosters, team season stats, per-game detail and player game logs."
                ),
                "vgi.doc_md": (
                    "## College football analytical data\n\n"
                    "Game-, team-, poll-, drive-, play- and statistic-grain tables. Use the current tables "
                    "for the live season and the parameterized functions for history and detail.\n\n"
                    "## Identifiers\n\n"
                    "Every row carries ESPN ids: look teams up with `teams()`, conferences with "
                    "`conferences()`, and pass `game_id` values from `scoreboard()` or `team_schedule()` "
                    "to the per-game functions."
                ),
                "vgi.example_queries": _SCOREBOARD_TAGS["vgi.example_queries"],
            },
            functions=[*GAME_FUNCTIONS, *LEAGUE_FUNCTIONS, *PLAYER_FUNCTIONS],
            tables=[
                Table(
                    name="current_scoreboard",
                    function=CurrentScoreboardFunction,
                    comment="This week's FBS games with status and scores, one row per game",
                    tags=_SCOREBOARD_TAGS,
                    column_comments=column_comments(GAME_SCHEMA),
                    constraints=_key_constraints("current_scoreboard", ("game_id",)),
                    cardinality_estimate=70,
                ),
                Table(
                    name="current_standings",
                    function=CurrentStandingsFunction,
                    comment="Current FBS and FCS conference standings, one row per team",
                    tags=_STANDINGS_TAGS,
                    column_comments=column_comments(STANDINGS_SCHEMA),
                    constraints=_key_constraints("current_standings", ("team_id",)),
                    cardinality_estimate=265,
                ),
                Table(
                    name="current_rankings",
                    function=CurrentRankingsFunction,
                    comment="Latest AP, Coaches, CFP and lower-division polls, one row per team per poll",
                    tags=_RANKINGS_TAGS,
                    column_comments=column_comments(RANKING_SCHEMA),
                    constraints=_key_constraints("current_rankings", ("poll_id", "team_id")),
                    cardinality_estimate=200,
                ),
                Table(
                    name="current_stat_leaders",
                    function=CurrentStatLeadersFunction,
                    comment="Current-season national top-ten leaders, one row per category and player",
                    tags=_LEADERS_TAGS,
                    column_comments=column_comments(LEADER_SCHEMA),
                    constraints=_key_constraints("current_stat_leaders", ("category", "player_id")),
                    cardinality_estimate=120,
                ),
            ],
        )
    ],
)


class NcaaFootballWorker(Worker):
    """Worker process hosting the read-only college-football catalog."""

    catalog = NCAA_FOOTBALL_CATALOG


def main() -> None:
    """Run over stdio by default, or HTTP when VGI's ``--http`` flag is supplied."""
    NcaaFootballWorker.main()


def main_http() -> None:
    """Run HTTP with production-safe configuration requirements."""
    if not os.environ.get("VGI_SIGNING_KEY"):
        raise SystemExit(
            "VGI_SIGNING_KEY is required for HTTP mode so state remains valid across restarts and replicas"
        )
    auth_configured = bool(os.environ.get("VGI_BEARER_TOKENS") or os.environ.get("VGI_JWT_ISSUER"))
    auth_configured = auth_configured or os.environ.get("VGI_PROXY_PROOF_MODE", "").lower() == "require"
    if not auth_configured and os.environ.get("VGI_ALLOW_ANONYMOUS") != "1":
        raise SystemExit(
            "HTTP authentication is required; configure bearer/JWT/proxy proof, or explicitly set "
            "VGI_ALLOW_ANONYMOUS=1 for an intentionally public deployment"
        )
    argv = sys.argv[1:]
    if "--http" not in argv:
        argv = ["--http", *argv]
    if "--describe" not in argv and "--no-describe" not in argv:
        argv = ["--no-describe", *argv]
    if not any(value == "--cors-origins" or value.startswith("--cors-origins=") for value in argv):
        argv = ["--cors-origins", "", *argv]
    sys.argv = [sys.argv[0], *argv]
    NcaaFootballWorker.main()
