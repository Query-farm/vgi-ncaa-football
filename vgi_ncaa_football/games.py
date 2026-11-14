"""Scoreboards, team schedules, and per-game detail (summary, box scores, drives, plays)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, ClassVar

import pyarrow as pa
from vgi.arguments import Arg
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
from vgi_ncaa_football.common import SEASON_ARG, SEASON_TYPE_CHOICES, division_arg, emit, id_arg, tags
from vgi_ncaa_football.composed import RANKED_BOWL_MATCHUPS, SCHEDULE_OPPONENT_RECORDS
from vgi_ncaa_football.parsers import (
    Row,
    flatten_box_players,
    flatten_box_team,
    flatten_drives,
    flatten_events,
    flatten_plays,
    flatten_summary_game,
)
from vgi_ncaa_football.schemas import (
    BOX_PLAYER_SCHEMA,
    BOX_TEAM_SCHEMA,
    DRIVE_SCHEMA,
    GAME_SCHEMA,
    GAME_SUMMARY_SCHEMA,
    PLAY_SCHEMA,
)

NATIONAL_TITLE_GAME = 401769076  # Indiana 27, Miami 21 (2025 season CFP final)


def _scoreboard_rows(
    *,
    division: str,
    season: int | None,
    week: int | None,
    season_type: str | None,
    conference_id: int | None,
) -> tuple[list[Row], list[int]]:
    if season_type is None and (season is not None or week is not None):
        season_type = "regular"
    groups = (
        [conference_id]
        if conference_id is not None
        else [api.DIVISION_GROUPS[name] for name in (("fbs", "fcs") if division == "all" else (division,))]
    )
    rows: list[Row] = []
    ttls: list[int] = []
    for group in groups:
        response = api.fetch_scoreboard(group=group, season=season, week=week, season_type=season_type)
        rows.extend(flatten_events(response.payload.get("events")))
        ttls.append(response.ttl)
    seen: set[int] = set()
    unique = [row for row in rows if not (row["game_id"] in seen or seen.add(row["game_id"]))]
    return unique, ttls


@dataclass(kw_only=True)
class ScoreboardArgs:
    """Arguments accepted by ``scoreboard``."""

    season: Annotated[int | None, SEASON_ARG]
    week: Annotated[
        int | None,
        Arg(
            "week",
            default=None,
            arrow_type=pa.int32(),
            ge=1,
            le=20,
            doc="Week within the season type. NULL = ESPN's current week (or week 1 of a postseason).",
        ),
    ]
    season_type: Annotated[
        str | None,
        Arg(
            "season_type",
            default=None,
            arrow_type=pa.string(),
            choices=SEASON_TYPE_CHOICES,
            doc=(
                "regular or postseason (bowls and playoff). NULL = current, or regular when "
                "season/week is set."
            ),
        ),
    ]
    division: Annotated[str, division_arg("fbs")]
    conference_id: Annotated[
        int | None,
        Arg(
            "conference_id",
            default=None,
            arrow_type=pa.int32(),
            ge=1,
            doc="Restrict to one conference's games (see conferences()); overrides division.",
        ),
    ]


_SCOREBOARD_SQL = (
    "SELECT start_time, notes, away_team_name, away_score, home_team_name, home_score "
    "FROM ncaa_football.main.scoreboard(season := 2025, season_type := 'postseason') "
    "ORDER BY start_time"
)
_SCOREBOARD_WEEK_SQL = (
    "SELECT away_team_abbreviation, away_score, home_team_abbreviation, home_score, status "
    "FROM ncaa_football.main.scoreboard(season := 2025, week := 1, division := 'fcs') ORDER BY start_time"
)


@init_single_worker
@bind_fixed_schema
class ScoreboardFunction(TableFunctionGenerator[ScoreboardArgs]):
    """One week's FBS or FCS games with scores and status."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = GAME_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "scoreboard"
        description = "College football games for one week: matchups, kickoff, status, and scores"
        categories = ["football", "scores"]
        projection_pushdown = True
        tags = tags(
            category="scores",
            title="Weekly Scoreboard",
            llm=(
                "One row per game for a season week with kickoff time, status, venue, rankings and "
                "home/away scores. Use season and week for a regular-season week, season_type := "
                "'postseason' for bowls and the College Football Playoff, division := 'fcs' for FCS, "
                "or conference_id for one conference. All arguments are named and optional."
            ),
            md=(
                "Weekly college-football scoreboard, one row per game. Scores are NULL before kickoff; "
                "`notes` names bowls and playoff rounds. Games between an FBS and an FCS team appear in "
                "both divisions; `division := 'all'` returns each game once."
            ),
            schema=GAME_SCHEMA,
            examples=[
                ("Every 2025 bowl and playoff result.", _SCOREBOARD_SQL),
                ("FCS opening-week results for 2025.", _SCOREBOARD_WEEK_SQL),
                RANKED_BOWL_MATCHUPS,
            ],
        )
        examples = [
            FunctionExample(sql=_SCOREBOARD_SQL, description="The 2025 bowl and playoff results"),
            FunctionExample(sql=RANKED_BOWL_MATCHUPS[1], description=RANKED_BOWL_MATCHUPS[0]),
        ]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """A full FBS week has roughly 60-100 games."""
        return TableCardinality(estimate=70, max=300)

    @classmethod
    def process(cls, params: ProcessParams[ScoreboardArgs], state: None, out: OutputCollector) -> None:
        """Fetch and emit the requested scoreboard."""
        args = params.args
        rows, ttls = _scoreboard_rows(
            division=args.division,
            season=args.season,
            week=args.week,
            season_type=args.season_type,
            conference_id=args.conference_id,
        )
        emit(out, rows, params.output_schema, ttls)


@init_single_worker
@bind_fixed_schema
class CurrentScoreboardFunction(TableFunctionGenerator[None]):
    """Current-week FBS scoreboard backing the ``current_scoreboard`` table."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = GAME_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "all_current_scoreboard"
        description = "Current-week FBS scoreboard (scan behind current_scoreboard)"
        categories = ["football", "scores"]
        projection_pushdown = True
        tags = tags(
            category="scores",
            title="Current FBS Scoreboard Scan",
            llm=(
                "The scan behind the current_scoreboard table. Prefer the table; use scoreboard() for "
                "another week, the postseason, FCS, or one conference."
            ),
            md="Returns ESPN's current FBS week. Selecting `current_scoreboard` is normally clearer.",
            schema=GAME_SCHEMA,
            examples=[
                (
                    "Read the current week's games directly from the backing scan.",
                    "SELECT short_name, status_detail FROM ncaa_football.main.all_current_scoreboard() "
                    "ORDER BY start_time",
                )
            ],
        )
        examples = [
            FunctionExample(
                sql=(
                    "SELECT short_name, status_detail FROM ncaa_football.main.all_current_scoreboard() "
                    "ORDER BY start_time"
                ),
                description="Current FBS games from the backing scan",
            )
        ]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """A full FBS week has roughly 60-100 games."""
        return TableCardinality(estimate=70, max=300)

    @classmethod
    def process(cls, params: ProcessParams[None], state: None, out: OutputCollector) -> None:
        """Fetch and emit the current FBS week."""
        rows, ttls = _scoreboard_rows(
            division="fbs", season=None, week=None, season_type=None, conference_id=None
        )
        emit(out, rows, params.output_schema, ttls)


@dataclass(kw_only=True)
class TeamScheduleArgs:
    """Arguments accepted by ``team_schedule``."""

    team_id: Annotated[int, id_arg(0, "team")]
    season: Annotated[int | None, SEASON_ARG]


_SCHEDULE_SQL = (
    "SELECT season_type, week, start_time, away_team_abbreviation, away_score, "
    "home_team_abbreviation, home_score, notes "
    "FROM ncaa_football.main.team_schedule(84, season := 2025) ORDER BY start_time"
)


@init_single_worker
@bind_fixed_schema
class TeamScheduleFunction(TableFunctionGenerator[TeamScheduleArgs]):
    """A team's full regular-season and postseason schedule."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = GAME_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "team_schedule"
        description = "One team's regular-season and postseason games with results"
        categories = ["football", "scores", "teams"]
        projection_pushdown = True
        tags = tags(
            category="scores",
            title="Team Schedule",
            llm=(
                "Every game for one team in a season, regular season and postseason, with opponent, "
                "venue, rankings and scores. The first argument is the ESPN team_id (look it up in "
                "teams()); season is named and optional. Example: team_schedule(84, season := 2025) for "
                "Indiana's 2025 season."
            ),
            md=(
                "A team's schedule in game-row form (same columns as `scoreboard()`). The team can be "
                "home or away; filter on `home_team_id`/`away_team_id` to orient results. ESPN's team "
                "schedule feed omits conference and venue ids, so `conference_game`, `conference_id`, "
                "`conference_name` and `venue_id` are NULL here; use `scoreboard()` or `game_summary()` "
                "for those."
            ),
            schema=GAME_SCHEMA,
            examples=[
                ("Indiana's unbeaten 2025 national-championship season.", _SCHEDULE_SQL),
                SCHEDULE_OPPONENT_RECORDS,
            ],
        )
        examples = [
            FunctionExample(sql=_SCHEDULE_SQL, description="Indiana's 2025 schedule and results"),
            FunctionExample(sql=SCHEDULE_OPPONENT_RECORDS[1], description=SCHEDULE_OPPONENT_RECORDS[0]),
        ]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """Teams play 12-17 games."""
        return TableCardinality(estimate=13, max=20)

    @classmethod
    def process(cls, params: ProcessParams[TeamScheduleArgs], state: None, out: OutputCollector) -> None:
        """Fetch and emit both schedule halves."""
        args = params.args
        regular = api.fetch_team_schedule(args.team_id, season=args.season, season_type="regular")
        rows = flatten_events(regular.payload.get("events"))
        ttls = [regular.ttl]
        try:
            post = api.fetch_team_schedule(args.team_id, season=args.season, season_type="postseason")
        except api.EspnApiError as error:
            if error.status not in {400, 404}:
                raise
        else:
            known = {row["game_id"] for row in rows}
            rows += [row for row in flatten_events(post.payload.get("events")) if row["game_id"] not in known]
            ttls.append(post.ttl)
        emit(out, rows, params.output_schema, ttls)


@dataclass(kw_only=True)
class GameArgs:
    """Arguments accepted by the per-game functions."""

    game_id: Annotated[int, id_arg(0, "game (event)")]


class _GameBase:
    """Shared process implementation for functions that read one game summary."""

    FLATTEN: ClassVar[Callable[[dict[str, Any], int], list[Row]]]
    ESTIMATE: ClassVar[int]
    MAXIMUM: ClassVar[int]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        return TableCardinality(estimate=cls.ESTIMATE, max=cls.MAXIMUM)

    @classmethod
    def process(cls, params: ProcessParams[GameArgs], state: None, out: OutputCollector) -> None:
        game_id = params.args.game_id
        response = api.fetch_summary(game_id)
        rows = cls.FLATTEN(response.payload, game_id)
        emit(out, rows, params.output_schema, [response.ttl])


def _summary_rows(payload: dict[str, Any], game_id: int) -> list[Row]:
    rows = flatten_summary_game(payload)
    return [row for row in rows if row["game_id"] == game_id] or rows


_SUMMARY_SQL = (
    "SELECT name, notes, venue_name, attendance, home_score, away_score, home_line_scores, away_line_scores "
    f"FROM ncaa_football.main.game_summary({NATIONAL_TITLE_GAME})"
)
_BOX_TEAM_SQL = (
    "SELECT team_abbreviation, stat_label, display_value "
    f"FROM ncaa_football.main.box_score_team({NATIONAL_TITLE_GAME}) ORDER BY stat_name, team_abbreviation"
)
_BOX_PLAYER_SQL = (
    "SELECT player_name, team_abbreviation, value AS passing_yards "
    f"FROM ncaa_football.main.box_score_player({NATIONAL_TITLE_GAME}) "
    "WHERE category = 'passing' AND stat_name = 'passingYards' ORDER BY value DESC"
)
_DRIVES_SQL = (
    "SELECT drive_number, team_abbreviation, start_text, offensive_plays, yards, display_result "
    f"FROM ncaa_football.main.drives({NATIONAL_TITLE_GAME}) ORDER BY drive_number"
)
_PLAYS_SQL = (
    "SELECT period, clock, play_type, text "
    f"FROM ncaa_football.main.play_by_play({NATIONAL_TITLE_GAME}) WHERE scoring_play ORDER BY sequence_number"
)


@init_single_worker
@bind_fixed_schema
class GameSummaryFunction(_GameBase, TableFunctionGenerator[GameArgs]):
    """One game's header: teams, final score, line scores, venue and attendance."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = GAME_SUMMARY_SCHEMA
    FLATTEN = staticmethod(_summary_rows)
    ESTIMATE = 1
    MAXIMUM = 1

    class Meta:
        """Function discovery metadata."""

        name = "game_summary"
        description = "One game's result, rankings, line scores, venue and attendance"
        categories = ["football", "games"]
        projection_pushdown = True
        tags = tags(
            category="games",
            title="Game Summary",
            llm=(
                "A single row describing one game by ESPN game_id (from scoreboard() or team_schedule()): "
                "teams, ranks, final score, points by quarter in home_line_scores/away_line_scores, "
                "status, venue, attendance and bowl/playoff note."
            ),
            md="One game header row. Line-score lists include overtime periods.",
            schema=GAME_SUMMARY_SCHEMA,
            examples=[("The 2025 CFP national championship game.", _SUMMARY_SQL)],
        )
        examples = [FunctionExample(sql=_SUMMARY_SQL, description="The 2025 national championship result")]


@init_single_worker
@bind_fixed_schema
class BoxScoreTeamFunction(_GameBase, TableFunctionGenerator[GameArgs]):
    """Team box-score statistics for one game, one row per team per statistic."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = BOX_TEAM_SCHEMA
    FLATTEN = staticmethod(flatten_box_team)
    ESTIMATE = 30
    MAXIMUM = 80

    class Meta:
        """Function discovery metadata."""

        name = "box_score_team"
        description = "Team statistics for one game (first downs, yards, turnovers, possession)"
        categories = ["football", "games", "team-stats"]
        projection_pushdown = True
        tags = tags(
            category="games",
            title="Team Box Score",
            llm=(
                "Long-format team box score for one game_id: one row per team and statistic. stat_name "
                "keys include totalYards, netPassingYards, rushingYards, firstDowns, thirdDownEff, "
                "turnovers, totalPenaltiesYards and possessionTime. value is numeric when the display "
                "value is a single number; compound values such as '6-14' stay in display_value."
            ),
            md="Team statistics in long form; pivot on `stat_name` for a side-by-side comparison.",
            schema=BOX_TEAM_SCHEMA,
            examples=[("Side-by-side team statistics from the 2025 title game.", _BOX_TEAM_SQL)],
        )
        examples = [FunctionExample(sql=_BOX_TEAM_SQL, description="Team box score for the 2025 title game")]


@init_single_worker
@bind_fixed_schema
class BoxScorePlayerFunction(_GameBase, TableFunctionGenerator[GameArgs]):
    """Player box-score statistics for one game, one row per player per statistic."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = BOX_PLAYER_SCHEMA
    FLATTEN = staticmethod(flatten_box_players)
    ESTIMATE = 400
    MAXIMUM = 2_000

    class Meta:
        """Function discovery metadata."""

        name = "box_score_player"
        description = "Player statistics for one game (passing, rushing, receiving, defense, kicking)"
        categories = ["football", "games", "player-stats"]
        projection_pushdown = True
        tags = tags(
            category="games",
            title="Player Box Score",
            llm=(
                "Long-format player box score for one game_id: one row per player, category and stat. "
                "category is passing, rushing, receiving, fumbles, defensive, interceptions, kickReturns, "
                "puntReturns, kicking or punting; stat_name keys include passingYards, passingTouchdowns, "
                "rushingYards, receivingYards, receptions, totalTackles and sacks. Filter on both "
                "category and stat_name, then use value."
            ),
            md=(
                "Every individual box-score line in long form. Compound values such as completions/"
                "attempts ('19/32') keep a NULL `value` and the original `display_value`."
            ),
            schema=BOX_PLAYER_SCHEMA,
            examples=[("Passing yards by quarterback in the 2025 title game.", _BOX_PLAYER_SQL)],
        )
        examples = [FunctionExample(sql=_BOX_PLAYER_SQL, description="Title-game passing yards")]


@init_single_worker
@bind_fixed_schema
class DrivesFunction(_GameBase, TableFunctionGenerator[GameArgs]):
    """Every offensive drive in one game."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = DRIVE_SCHEMA
    FLATTEN = staticmethod(flatten_drives)
    ESTIMATE = 25
    MAXIMUM = 60

    class Meta:
        """Function discovery metadata."""

        name = "drives"
        description = "Every drive in one game with start, end, plays, yards and result"
        categories = ["football", "games", "play-by-play"]
        projection_pushdown = True
        tags = tags(
            category="games",
            title="Game Drives",
            llm=(
                "One row per drive for a game_id, in order, with the offense, starting and ending field "
                "position, plays, yards, elapsed time and result (TD, FG, PUNT, INT, DOWNS, ...)."
            ),
            md="Drive chart for one game. `start_yard_line`/`end_yard_line` use ESPN's 0-100 scale.",
            schema=DRIVE_SCHEMA,
            examples=[("The drive chart of the 2025 title game.", _DRIVES_SQL)],
        )
        examples = [FunctionExample(sql=_DRIVES_SQL, description="Title-game drive chart")]


@init_single_worker
@bind_fixed_schema
class PlayByPlayFunction(_GameBase, TableFunctionGenerator[GameArgs]):
    """Every play in one game."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = PLAY_SCHEMA
    FLATTEN = staticmethod(flatten_plays)
    ESTIMATE = 180
    MAXIMUM = 400

    class Meta:
        """Function discovery metadata."""

        name = "play_by_play"
        description = "Every play in one game with down, distance, yardage, score and narrative"
        categories = ["football", "games", "play-by-play"]
        projection_pushdown = True
        tags = tags(
            category="games",
            title="Play-by-Play",
            llm=(
                "One row per play for a game_id with period, clock, play_type, narrative text, down and "
                "distance before and after, yards, running score, and scoring/turnover/penalty flags. "
                "Order by sequence_number for game order."
            ),
            md="Play-by-play for one game, drawn from ESPN's drive feed.",
            schema=PLAY_SCHEMA,
            examples=[("Every scoring play of the 2025 title game.", _PLAYS_SQL)],
        )
        examples = [FunctionExample(sql=_PLAYS_SQL, description="Title-game scoring plays")]


GAME_FUNCTIONS = [
    ScoreboardFunction,
    CurrentScoreboardFunction,
    TeamScheduleFunction,
    GameSummaryFunction,
    BoxScoreTeamFunction,
    BoxScorePlayerFunction,
    DrivesFunction,
    PlayByPlayFunction,
]
