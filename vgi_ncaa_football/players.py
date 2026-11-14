"""Rosters, team season statistics, national leaderboards, and per-player statistics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

import pyarrow as pa
from vgi.metadata import FunctionExample
from vgi.table_function import (
    ProcessParams,
    TableCardinality,
    TableFunctionGenerator,
    bind_fixed_schema,
    init_single_worker,
)
from vgi_rpc.rpc import OutputCollector

from vgi_ncaa_football import api
from vgi_ncaa_football.common import SEASON_ARG, emit, id_arg, tags
from vgi_ncaa_football.composed import PASSING_LEADERS_TEAM_RECORDS
from vgi_ncaa_football.parsers import (
    flatten_game_log,
    flatten_leaders,
    flatten_player_seasons,
    flatten_roster,
    flatten_team_stats,
)
from vgi_ncaa_football.schemas import (
    GAME_LOG_SCHEMA,
    LEADER_SCHEMA,
    PLAYER_SEASON_SCHEMA,
    ROSTER_SCHEMA,
    TEAM_STATS_SCHEMA,
)

MENDOZA = 4837248  # Fernando Mendoza, Indiana quarterback on the 2025 national champions


@dataclass(kw_only=True)
class TeamArgs:
    """Arguments accepted by ``roster``."""

    team_id: Annotated[int, id_arg(0, "team")]


_ROSTER_SQL = (
    "SELECT jersey, full_name, position, class_year, height_inches, weight_lbs "
    "FROM ncaa_football.main.roster(84) WHERE unit = 'offense' ORDER BY position, full_name"
)


@init_single_worker
@bind_fixed_schema
class RosterFunction(TableFunctionGenerator[TeamArgs]):
    """A team's current roster."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = ROSTER_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "roster"
        description = "A team's current roster with positions, class, height and weight"
        categories = ["football", "teams", "players"]
        projection_pushdown = True
        tags = tags(
            category="players",
            title="Team Roster",
            llm=(
                "One row per player on a team's current roster (ESPN team_id from teams()): player_id, "
                "name, jersey, position, unit (offense/defense/specialTeam), class year, height in inches, "
                "weight in pounds and hometown. ESPN publishes current rosters only."
            ),
            md="Current roster for one team. Historical rosters are not available from ESPN.",
            schema=ROSTER_SCHEMA,
            examples=[("Indiana's current offensive roster.", _ROSTER_SQL)],
        )
        examples = [FunctionExample(sql=_ROSTER_SQL, description="Indiana's offensive roster")]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """FBS rosters carry roughly 100-130 players."""
        return TableCardinality(estimate=110, max=200)

    @classmethod
    def process(cls, params: ProcessParams[TeamArgs], state: None, out: OutputCollector) -> None:
        """Fetch and emit the roster."""
        team_id = params.args.team_id
        response = api.fetch_roster(team_id)
        emit(out, flatten_roster(response.payload, team_id), params.output_schema, [response.ttl])


@dataclass(kw_only=True)
class TeamSeasonArgs:
    """Arguments accepted by ``team_season_stats``."""

    team_id: Annotated[int, id_arg(0, "team")]
    season: Annotated[int | None, SEASON_ARG]


_TEAM_STATS_SQL = (
    "SELECT side, category, stat_name, display_value, per_game_value, rank "
    "FROM ncaa_football.main.team_season_stats(84, season := 2025) "
    "WHERE (category, stat_name) IN (('passing', 'passingYards'), ('rushing', 'rushingYards'), "
    "('scoring', 'totalPoints')) ORDER BY side, category"
)


@init_single_worker
@bind_fixed_schema
class TeamSeasonStatsFunction(TableFunctionGenerator[TeamSeasonArgs]):
    """A team's season statistics and its opponents' totals."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = TEAM_STATS_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "team_season_stats"
        description = "A team's season statistics and opponents' totals, one row per statistic"
        categories = ["football", "teams", "team-stats"]
        projection_pushdown = True
        tags = tags(
            category="team-stats",
            title="Team Season Statistics",
            llm=(
                "Long-format season statistics for one ESPN team_id: side = 'team' for its own totals or "
                "'opponent' for what it allowed; category such as passing, rushing, receiving, defensive, "
                "kicking, punting, returning, scoring or miscellaneous; stat_name such as passingYards, "
                "rushingYards, totalPoints, sacks or firstDowns; value, per_game_value and rank. ESPN "
                "repeats some statistics (totalPoints, for example) under several categories, so filter "
                "on category as well as stat_name. ESPN supplies per_game_value only for side = 'team' "
                "and a national rank only for side = 'opponent'."
            ),
            md="Team season totals in long form; season defaults to the current season.",
            schema=TEAM_STATS_SCHEMA,
            examples=[("Indiana's 2025 offense versus its opponents.", _TEAM_STATS_SQL)],
        )
        examples = [FunctionExample(sql=_TEAM_STATS_SQL, description="Indiana 2025 team statistics")]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """About 150 statistics per side."""
        return TableCardinality(estimate=300, max=600)

    @classmethod
    def process(cls, params: ProcessParams[TeamSeasonArgs], state: None, out: OutputCollector) -> None:
        """Fetch and emit season statistics."""
        args = params.args
        response = api.fetch_team_statistics(args.team_id, season=args.season)
        rows = flatten_team_stats(response.payload, args.team_id)
        emit(out, rows, params.output_schema, [response.ttl])


@dataclass(kw_only=True)
class LeadersArgs:
    """Arguments accepted by ``stat_leaders``."""

    season: Annotated[int | None, SEASON_ARG]


_LEADERS_SQL = (
    "SELECT category_name, rank, player_name, team_abbreviation, display_value "
    "FROM ncaa_football.main.stat_leaders(season := 2025) WHERE rank <= 3 ORDER BY category, rank"
)
_CURRENT_LEADERS_SQL = (
    "SELECT player_name, team_abbreviation, value FROM ncaa_football.main.all_current_stat_leaders() "
    "WHERE category = 'passingYards' ORDER BY rank"
)


@init_single_worker
@bind_fixed_schema
class StatLeadersFunction(TableFunctionGenerator[LeadersArgs]):
    """National individual leaderboards for a season."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = LEADER_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "stat_leaders"
        description = "National top-ten individual leaders by category for a season"
        categories = ["football", "player-stats", "leaders"]
        projection_pushdown = True
        tags = tags(
            category="player-stats",
            title="National Stat Leaders",
            llm=(
                "ESPN's national top-ten leaderboards for a season: category keys passingYards, "
                "passingTouchdowns, rushingYards, rushingTouchdowns, receivingYards, receptions, "
                "receivingTouchdowns, totalTackles, sacks, interceptions, interceptionYards and "
                "quarterbackRating. Values are full-season totals including postseason games."
            ),
            md=(
                "Top ten per category, one row per player. Filter `category` and order by `rank`; ties "
                "keep ESPN's order."
            ),
            schema=LEADER_SCHEMA,
            examples=[
                ("The top three in every 2025 category.", _LEADERS_SQL),
                PASSING_LEADERS_TEAM_RECORDS,
            ],
        )
        examples = [
            FunctionExample(sql=_LEADERS_SQL, description="2025 national leaders"),
            FunctionExample(sql=PASSING_LEADERS_TEAM_RECORDS[1], description=PASSING_LEADERS_TEAM_RECORDS[0]),
        ]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """Thirteen categories of up to ten players."""
        return TableCardinality(estimate=120, max=200)

    @classmethod
    def process(cls, params: ProcessParams[LeadersArgs], state: None, out: OutputCollector) -> None:
        """Fetch and emit the leaderboards."""
        response = api.fetch_leaders(season=params.args.season)
        emit(out, flatten_leaders(response.payload), params.output_schema, [response.ttl])


@init_single_worker
@bind_fixed_schema
class CurrentStatLeadersFunction(TableFunctionGenerator[None]):
    """Current-season leaderboards backing the ``current_stat_leaders`` table."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = LEADER_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "all_current_stat_leaders"
        description = "Current-season national leaders (scan behind current_stat_leaders)"
        categories = ["football", "player-stats", "leaders"]
        projection_pushdown = True
        tags = tags(
            category="player-stats",
            title="Current Stat Leaders Scan",
            llm=(
                "The scan behind the current_stat_leaders table. Prefer the table; use stat_leaders() "
                "for a past season."
            ),
            md="Current-season national leaders. Selecting `current_stat_leaders` is normally clearer.",
            schema=LEADER_SCHEMA,
            examples=[("Current passing-yards leaders.", _CURRENT_LEADERS_SQL)],
        )
        examples = [FunctionExample(sql=_CURRENT_LEADERS_SQL, description="Current passing leaders")]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """Thirteen categories of up to ten players."""
        return TableCardinality(estimate=120, max=200)

    @classmethod
    def process(cls, params: ProcessParams[None], state: None, out: OutputCollector) -> None:
        """Fetch and emit the current leaderboards."""
        response = api.fetch_leaders()
        emit(out, flatten_leaders(response.payload), params.output_schema, [response.ttl])


@dataclass(kw_only=True)
class PlayerSeasonArgs:
    """Arguments accepted by ``player_game_log``."""

    player_id: Annotated[int, id_arg(0, "athlete")]
    season: Annotated[int | None, SEASON_ARG]


_GAME_LOG_SQL = (
    "SELECT game_date, opponent_abbreviation, result, score, display_value AS passing_yards "
    f"FROM ncaa_football.main.player_game_log({MENDOZA}, season := 2025) "
    "WHERE stat_name = 'passingYards' ORDER BY game_date"
)


@init_single_worker
@bind_fixed_schema
class PlayerGameLogFunction(TableFunctionGenerator[PlayerSeasonArgs]):
    """One player's per-game statistics for a season."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = GAME_LOG_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "player_game_log"
        description = "One player's per-game statistics for a season, one row per game and statistic"
        categories = ["football", "player-stats", "game-log"]
        projection_pushdown = True
        tags = tags(
            category="player-stats",
            title="Player Game Log",
            llm=(
                "Long-format game log for one ESPN player_id (from roster(), box_score_player() or "
                "stat_leaders()): one row per game and statistic with opponent, home_away, result, score "
                "and bowl note. stat_name keys depend on position, for example passingYards, "
                "passingTouchdowns, rushingYards, receivingYards or totalTackles."
            ),
            md="Per-game player statistics, regular season and postseason, in long form.",
            schema=GAME_LOG_SCHEMA,
            examples=[("Fernando Mendoza's 2025 passing yards by game.", _GAME_LOG_SQL)],
        )
        examples = [FunctionExample(sql=_GAME_LOG_SQL, description="A quarterback's 2025 game log")]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """About 15 games of 10-20 statistics."""
        return TableCardinality(estimate=200, max=600)

    @classmethod
    def process(cls, params: ProcessParams[PlayerSeasonArgs], state: None, out: OutputCollector) -> None:
        """Fetch and emit the game log."""
        args = params.args
        response = api.fetch_player_game_log(args.player_id, season=args.season)
        rows = flatten_game_log(response.payload, args.player_id, args.season)
        emit(out, rows, params.output_schema, [response.ttl])


@dataclass(kw_only=True)
class PlayerArgs:
    """Arguments accepted by ``player_season_stats``."""

    player_id: Annotated[int, id_arg(0, "athlete")]


_PLAYER_SEASONS_SQL = (
    "SELECT season, team_abbreviation, stat_name, display_value "
    f"FROM ncaa_football.main.player_season_stats({MENDOZA}) "
    "WHERE category = 'passing' AND stat_name IN ('passingYards', 'passingTouchdowns') "
    "ORDER BY season, stat_name"
)


@init_single_worker
@bind_fixed_schema
class PlayerSeasonStatsFunction(TableFunctionGenerator[PlayerArgs]):
    """One player's season-by-season college statistics."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = PLAYER_SEASON_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "player_season_stats"
        description = "One player's college career by season and team, one row per statistic"
        categories = ["football", "player-stats", "career"]
        projection_pushdown = True
        tags = tags(
            category="player-stats",
            title="Player Season Statistics",
            llm=(
                "Season-by-season statistics for one ESPN player_id, including seasons at previous "
                "schools (transfers): one row per season, team, category (passing, rushing, receiving, "
                "defensive, scoring, ...) and stat_name."
            ),
            md="A player's college career lines in long form, split by season and team.",
            schema=PLAYER_SEASON_SCHEMA,
            examples=[("Fernando Mendoza's passing by season, Cal and Indiana.", _PLAYER_SEASONS_SQL)],
        )
        examples = [FunctionExample(sql=_PLAYER_SEASONS_SQL, description="A transfer quarterback's career")]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """A few seasons of a few dozen statistics."""
        return TableCardinality(estimate=100, max=400)

    @classmethod
    def process(cls, params: ProcessParams[PlayerArgs], state: None, out: OutputCollector) -> None:
        """Fetch and emit the career lines."""
        player_id = params.args.player_id
        response = api.fetch_player_season_stats(player_id)
        emit(out, flatten_player_seasons(response.payload, player_id), params.output_schema, [response.ttl])


PLAYER_FUNCTIONS = [
    RosterFunction,
    TeamSeasonStatsFunction,
    StatLeadersFunction,
    CurrentStatLeadersFunction,
    PlayerGameLogFunction,
    PlayerSeasonStatsFunction,
]
