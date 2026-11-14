"""Standings, team and conference directories, and poll rankings."""

from __future__ import annotations

from collections.abc import Callable, Mapping
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
from vgi_ncaa_football.common import SEASON_ARG, SEASON_TYPE_CHOICES, division_arg, emit, tags
from vgi_ncaa_football.composed import CFP_TOP_12_CONFERENCE_FINISH
from vgi_ncaa_football.parsers import (
    Row,
    flatten_conferences,
    flatten_poll,
    flatten_standings,
    flatten_teams,
    items,
    mapping,
    team_directory,
    to_int,
)
from vgi_ncaa_football.schemas import CONFERENCE_SCHEMA, RANKING_SCHEMA, STANDINGS_SCHEMA, TEAM_SCHEMA

POLL_IDS = {"ap": 1, "coaches": 2, "cfp": 21, "fcs": 20}


def _divisions(division: str) -> tuple[str, ...]:
    return ("fbs", "fcs") if division == "all" else (division,)


def _from_standings(
    division: str, season: int | None, flatten: Callable[[Mapping[str, Any]], list[Row]]
) -> tuple[list[Row], list[int]]:
    rows: list[Row] = []
    ttls: list[int] = []
    for name in _divisions(division):
        response = api.fetch_standings(group=api.DIVISION_GROUPS[name], season=season)
        rows.extend(flatten(response.payload))
        ttls.append(response.ttl)
    return rows, ttls


@dataclass(kw_only=True)
class StandingsArgs:
    """Arguments accepted by ``standings``."""

    season: Annotated[int | None, SEASON_ARG]
    division: Annotated[str, division_arg("fbs")]


_STANDINGS_SQL = (
    "SELECT conference_name, conference_rank, team_name, overall_record, conference_record "
    "FROM ncaa_football.main.standings(season := 2025) "
    "WHERE conference_abbreviation = 'big10' ORDER BY conference_rank"
)
_CURRENT_STANDINGS_SQL = (
    "SELECT conference_name, team_name, wins, losses, conference_record "
    "FROM ncaa_football.main.all_current_standings() WHERE conference_rank = 1 ORDER BY conference_name"
)


@init_single_worker
@bind_fixed_schema
class StandingsFunction(TableFunctionGenerator[StandingsArgs]):
    """Conference standings for a season and division."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = STANDINGS_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "standings"
        description = "Conference standings by season and division (FBS or FCS)"
        categories = ["football", "standings"]
        projection_pushdown = True
        tags = tags(
            category="standings",
            title="Conference Standings",
            llm=(
                "One row per team with conference, conference_rank (ESPN's standings order), overall and "
                "conference wins/losses, win percentages, home/away and vs-AP-Top-25 records, points for "
                "and against, and streak. season and division ('fbs', 'fcs' or 'all') are named and "
                "optional; the default is the current FBS season."
            ),
            md=(
                "Conference standings flattened to one row per team. `win_percentage` is computed exactly "
                "from overall wins and losses; `conference_win_percentage` is ESPN's conference figure. "
                "Split conferences carry their division in `subdivision_name`."
            ),
            schema=STANDINGS_SCHEMA,
            examples=[("The final 2025 Big Ten standings.", _STANDINGS_SQL)],
        )
        examples = [FunctionExample(sql=_STANDINGS_SQL, description="The final 2025 Big Ten standings")]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """FBS has about 136 teams and FCS about 128."""
        return TableCardinality(estimate=136, max=300)

    @classmethod
    def process(cls, params: ProcessParams[StandingsArgs], state: None, out: OutputCollector) -> None:
        """Fetch and emit standings."""
        rows, ttls = _from_standings(params.args.division, params.args.season, flatten_standings)
        emit(out, rows, params.output_schema, ttls)


@init_single_worker
@bind_fixed_schema
class CurrentStandingsFunction(TableFunctionGenerator[None]):
    """Current FBS and FCS standings backing the ``current_standings`` table."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = STANDINGS_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "all_current_standings"
        description = "Current FBS and FCS conference standings (scan behind current_standings)"
        categories = ["football", "standings"]
        projection_pushdown = True
        tags = tags(
            category="standings",
            title="Current Standings Scan",
            llm=(
                "The scan behind the current_standings table. Prefer the table; use standings() for a "
                "historical season or a single division."
            ),
            md="Current FBS and FCS standings. Selecting `current_standings` is normally clearer.",
            schema=STANDINGS_SCHEMA,
            examples=[("Current conference leaders from the backing scan.", _CURRENT_STANDINGS_SQL)],
        )
        examples = [FunctionExample(sql=_CURRENT_STANDINGS_SQL, description="Current conference leaders")]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """FBS plus FCS is about 265 teams."""
        return TableCardinality(estimate=265, max=400)

    @classmethod
    def process(cls, params: ProcessParams[None], state: None, out: OutputCollector) -> None:
        """Fetch and emit current standings for both divisions."""
        rows, ttls = _from_standings("all", None, flatten_standings)
        emit(out, rows, params.output_schema, ttls)


@dataclass(kw_only=True)
class TeamsArgs:
    """Arguments accepted by ``teams``."""

    division: Annotated[str, division_arg("fbs")]
    season: Annotated[int | None, SEASON_ARG]


_TEAMS_SQL = (
    "SELECT team_id, display_name, abbreviation, conference_name "
    "FROM ncaa_football.main.teams() WHERE conference_abbreviation = 'sec' ORDER BY display_name"
)


@init_single_worker
@bind_fixed_schema
class TeamsFunction(TableFunctionGenerator[TeamsArgs]):
    """Division I teams with their conference membership."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = TEAM_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "teams"
        description = "FBS or FCS teams with ESPN team ids and conference membership"
        categories = ["football", "teams"]
        projection_pushdown = True
        tags = tags(
            category="teams",
            title="Teams",
            llm=(
                "One row per FBS (default) or FCS team with the ESPN team_id needed by team_schedule(), "
                "roster() and team_season_stats(), plus names, abbreviation and conference. Use "
                "division := 'fcs' or 'all', and season for historical conference membership."
            ),
            md="Division I team directory with conference membership for the requested season.",
            schema=TEAM_SCHEMA,
            examples=[("Look up SEC team ids.", _TEAMS_SQL)],
        )
        examples = [FunctionExample(sql=_TEAMS_SQL, description="SEC team ids")]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """FBS has about 136 teams."""
        return TableCardinality(estimate=136, max=300)

    @classmethod
    def process(cls, params: ProcessParams[TeamsArgs], state: None, out: OutputCollector) -> None:
        """Fetch standings trees and emit their member teams."""
        rows, ttls = _from_standings(params.args.division, params.args.season, flatten_teams)
        emit(out, rows, params.output_schema, ttls)


@dataclass(kw_only=True)
class ConferencesArgs:
    """Arguments accepted by ``conferences``."""

    division: Annotated[str, division_arg("all")]
    season: Annotated[int | None, SEASON_ARG]


_CONFERENCES_SQL = (
    "SELECT division, conference_id, name, team_count FROM ncaa_football.main.conferences() "
    "ORDER BY division, name"
)


@init_single_worker
@bind_fixed_schema
class ConferencesFunction(TableFunctionGenerator[ConferencesArgs]):
    """FBS and FCS conferences."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = CONFERENCE_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "conferences"
        description = "FBS and FCS conferences with ESPN ids and member counts"
        categories = ["football", "teams"]
        projection_pushdown = True
        tags = tags(
            category="teams",
            title="Conferences",
            llm=(
                "One row per Division I conference (including FBS Independents) with the conference_id "
                "accepted by scoreboard(conference_id := ...), its division and team count. Defaults to "
                "both divisions for the current season."
            ),
            md="Conference directory derived from ESPN's standings tree.",
            schema=CONFERENCE_SCHEMA,
            examples=[("All Division I conferences.", _CONFERENCES_SQL)],
        )
        examples = [FunctionExample(sql=_CONFERENCES_SQL, description="Division I conferences")]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """Roughly 11 FBS and 14 FCS conferences."""
        return TableCardinality(estimate=25, max=60)

    @classmethod
    def process(cls, params: ProcessParams[ConferencesArgs], state: None, out: OutputCollector) -> None:
        """Fetch standings trees and emit their conferences."""
        rows, ttls = _from_standings(params.args.division, params.args.season, flatten_conferences)
        emit(out, rows, params.output_schema, ttls)


# --------------------------------------------------------------------------- rankings


def _directory() -> tuple[dict[int, Row], int]:
    response = api.fetch_team_directory()
    return team_directory(response.payload), response.ttl


def current_ranking_rows() -> tuple[list[Row], list[int]]:
    """Latest published polls, adding committee (CFP) rankings the site feed omits."""
    site = api.fetch_site_rankings()
    rows: list[Row] = []
    present: set[int] = set()
    for poll in items(site.payload.get("rankings")):
        rows.extend(flatten_poll(poll))
        poll_id = to_int(poll.get("id"))
        if poll_id is not None:
            present.add(poll_id)
    ttls = [site.ttl]
    latest = mapping(site.payload.get("latestSeason"))
    season = to_int(latest.get("year"))
    missing = [
        entry
        for entry in items(site.payload.get("availableRankings"))
        if to_int(entry.get("id")) not in present and to_int(entry.get("week")) and season
    ]
    if missing:
        directory, directory_ttl = _directory()
        ttls.append(directory_ttl)
        for entry in missing:
            poll = api.fetch_core_poll(
                season=season,  # type: ignore[arg-type]
                season_type=to_int(entry.get("seasonType")) or 2,
                week=to_int(entry.get("week")),  # type: ignore[arg-type]
                poll_id=to_int(entry.get("id")),  # type: ignore[arg-type]
            )
            if poll is not None:
                rows.extend(flatten_poll(poll.payload, directory))
                ttls.append(poll.ttl)
    return rows, ttls


@dataclass(kw_only=True)
class RankingsArgs:
    """Arguments accepted by ``rankings``."""

    season: Annotated[int | None, SEASON_ARG]
    week: Annotated[
        int | None,
        Arg(
            "week",
            default=None,
            arrow_type=pa.int32(),
            ge=1,
            le=20,
            doc="Poll week. NULL with a season = the final (postseason) polls; NULL with no season = latest.",
        ),
    ]
    season_type: Annotated[
        str | None,
        Arg(
            "season_type",
            default=None,
            arrow_type=pa.string(),
            choices=SEASON_TYPE_CHOICES,
            doc="regular or postseason. NULL = regular when week is given, postseason (final) otherwise.",
        ),
    ]
    poll: Annotated[
        str,
        Arg(
            "poll",
            default="all",
            arrow_type=pa.string(),
            choices=("all", *POLL_IDS),
            doc="all, ap (AP Top 25), coaches (AFCA Coaches), cfp (Playoff Committee) or fcs.",
        ),
    ]


_RANKINGS_SQL = (
    "SELECT rank, team_name, record "
    "FROM ncaa_football.main.rankings(season := 2025, week := 15, poll := 'cfp') "
    "WHERE rank IS NOT NULL ORDER BY rank"
)
_FINAL_AP_SQL = (
    "SELECT rank, team_name, first_place_votes, points "
    "FROM ncaa_football.main.rankings(season := 2025, poll := 'ap') WHERE rank <= 10 ORDER BY rank"
)


@init_single_worker
@bind_fixed_schema
class RankingsFunction(TableFunctionGenerator[RankingsArgs]):
    """AP, Coaches, College Football Playoff and FCS polls for any week."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = RANKING_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "rankings"
        description = "AP, Coaches, CFP committee and FCS poll rankings for a season week"
        categories = ["football", "rankings"]
        projection_pushdown = True
        tags = tags(
            category="rankings",
            title="Poll Rankings",
            llm=(
                "Historical poll rankings: one row per team per poll with rank, points, first-place "
                "votes, trend and record. Give season and week for a weekly poll (season_type defaults "
                "to regular); give only season for the final postseason polls. poll := 'ap', 'coaches', "
                "'cfp' or 'fcs' narrows the result. Teams merely receiving votes have rank NULL and "
                "receiving_votes = true."
            ),
            md=(
                "Weekly and final college-football polls from ESPN's poll archive. CFP committee "
                "rankings exist only for the weeks the committee publishes them (from late October)."
            ),
            schema=RANKING_SCHEMA,
            examples=[
                (
                    "The 2025 week-15 CFP committee top 25, the last ranking before conference "
                    "championship weekend.",
                    _RANKINGS_SQL,
                ),
                ("The final 2025 AP top ten.", _FINAL_AP_SQL),
                CFP_TOP_12_CONFERENCE_FINISH,
            ],
        )
        examples = [
            FunctionExample(sql=_RANKINGS_SQL, description="The 2025 week-15 CFP rankings"),
            FunctionExample(sql=CFP_TOP_12_CONFERENCE_FINISH[1], description=CFP_TOP_12_CONFERENCE_FINISH[0]),
        ]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """Up to five polls of 25 teams plus vote-getters."""
        return TableCardinality(estimate=150, max=400)

    @classmethod
    def process(cls, params: ProcessParams[RankingsArgs], state: None, out: OutputCollector) -> None:
        """Fetch each requested poll for the week and emit its rows."""
        args = params.args
        if args.season is None and args.week is None:
            rows, ttls = current_ranking_rows()
            if args.poll != "all":
                rows = [row for row in rows if row["poll_id"] == POLL_IDS[args.poll]]
            emit(out, rows, params.output_schema, ttls)
            return
        season = args.season if args.season is not None else api.current_season()
        if args.week is None:
            week, season_type = 1, args.season_type or "postseason"
        else:
            week, season_type = args.week, args.season_type or "regular"
        wanted = [POLL_IDS[args.poll]] if args.poll != "all" else None
        poll_ids = wanted or api.fetch_core_poll_ids(season=season, season_type=season_type, week=week)
        directory, directory_ttl = _directory()
        rows: list[Row] = []
        ttls = [directory_ttl]
        for poll_id in poll_ids:
            poll = api.fetch_core_poll(season=season, season_type=season_type, week=week, poll_id=poll_id)
            if poll is not None:
                rows.extend(flatten_poll(poll.payload, directory))
                ttls.append(poll.ttl)
        emit(out, rows, params.output_schema, ttls)


_CURRENT_RANKINGS_SQL = (
    "SELECT poll_short_name, rank, team_name FROM ncaa_football.main.all_current_rankings() "
    "WHERE rank <= 5 ORDER BY poll_id, rank"
)


@init_single_worker
@bind_fixed_schema
class CurrentRankingsFunction(TableFunctionGenerator[None]):
    """Latest published polls backing the ``current_rankings`` table."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = RANKING_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "all_current_rankings"
        description = "Latest AP, Coaches, CFP and lower-division polls (scan behind current_rankings)"
        categories = ["football", "rankings"]
        projection_pushdown = True
        tags = tags(
            category="rankings",
            title="Current Rankings Scan",
            llm=(
                "The scan behind the current_rankings table. Prefer the table; use rankings() for a "
                "historical week or the final polls of a past season."
            ),
            md="Latest polls including the CFP committee ranking. Selecting `current_rankings` is clearer.",
            schema=RANKING_SCHEMA,
            examples=[("The current top five in every poll.", _CURRENT_RANKINGS_SQL)],
        )
        examples = [FunctionExample(sql=_CURRENT_RANKINGS_SQL, description="Current top five in each poll")]

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """Up to six polls of 25 teams plus vote-getters."""
        return TableCardinality(estimate=200, max=400)

    @classmethod
    def process(cls, params: ProcessParams[None], state: None, out: OutputCollector) -> None:
        """Fetch and emit the latest polls."""
        rows, ttls = current_ranking_rows()
        emit(out, rows, params.output_schema, ttls)


LEAGUE_FUNCTIONS = [
    StandingsFunction,
    CurrentStandingsFunction,
    TeamsFunction,
    ConferencesFunction,
    RankingsFunction,
    CurrentRankingsFunction,
]
