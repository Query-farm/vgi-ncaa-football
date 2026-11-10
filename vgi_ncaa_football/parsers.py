"""Flatten ESPN college-football JSON documents into typed rows."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

import pyarrow as pa

from vgi_ncaa_football.api import SEASON_TYPE_NAMES, ref_id

Row = dict[str, Any]
_RECORD = re.compile(r"^\s*(\d+)-(\d+)(?:-(\d+))?\s*$")
_NUMBER = re.compile(r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)?(?:\.\d+)?$")


# --------------------------------------------------------------------------- scalars


def mapping(value: Any) -> Mapping[str, Any]:
    """Return ``value`` when it is a mapping, else an empty mapping."""
    return value if isinstance(value, Mapping) else {}


def items(value: Any) -> list[Mapping[str, Any]]:
    """Return the mapping elements of a list-like value."""
    return [entry for entry in value if isinstance(entry, Mapping)] if isinstance(value, list) else []


def to_int(value: Any) -> int | None:
    """Parse an integer-like value without treating booleans or fractions as integers."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        value = value.strip().replace(",", "")
        if not value:
            return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return int(number) if number.is_integer() else None


def to_float(value: Any) -> float | None:
    """Parse a single finite number, accepting thousands separators; NULL otherwise."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text or not _NUMBER.match(text) or text in {"+", "-", "."}:
            return None
        number = float(text.replace(",", ""))
    else:
        return None
    return number if number == number and abs(number) != float("inf") else None


def to_bool(value: Any) -> bool | None:
    """Return booleans unchanged and anything else as NULL."""
    return value if isinstance(value, bool) else None


def to_decimal(value: Any, places: str) -> Decimal | None:
    """Parse a finite decimal rounded to ``places`` (for example '0.001')."""
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not result.is_finite():
        return None
    return result.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def to_timestamp(value: Any) -> datetime | None:
    """Parse ESPN's ISO-8601 timestamps (with or without seconds) into UTC."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        result = datetime.fromisoformat(text)
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def text(value: Any) -> str | None:
    """Return a non-empty string or NULL."""
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    return None


def parse_record(summary: Any) -> tuple[int | None, int | None, int | None]:
    """Split a W-L or W-L-T record string into integers."""
    match = _RECORD.match(summary) if isinstance(summary, str) else None
    if not match:
        return None, None, None
    ties = int(match.group(3)) if match.group(3) is not None else 0
    return int(match.group(1)), int(match.group(2)), ties


def season_type_name(value: Any) -> str | None:
    """Map ESPN's numeric season type to regular/postseason."""
    if isinstance(value, Mapping):
        value = value.get("type", value.get("id"))
    number = to_int(value)
    return SEASON_TYPE_NAMES.get(number) if number is not None else None


def batch_from_rows(rows: Sequence[Row], schema: pa.Schema) -> pa.RecordBatch:
    """Build a record batch in the declared (possibly projected) column order."""
    return pa.RecordBatch.from_arrays(
        [pa.array([row.get(column.name) for row in rows], type=column.type) for column in schema],
        schema=schema,
    )


def unique_by(rows: Iterable[Row], key: Callable[[Row], tuple[Any, ...]]) -> list[Row]:
    """Drop rows whose key is incomplete or already seen, keeping the first occurrence."""
    seen: set[tuple[Any, ...]] = set()
    result: list[Row] = []
    for row in rows:
        value = key(row)
        if any(part is None for part in value) or value in seen:
            continue
        seen.add(value)
        result.append(row)
    return result


# --------------------------------------------------------------------------- games


def _record_summary(competitor: Mapping[str, Any]) -> str | None:
    for key in ("records", "record"):
        for record in items(competitor.get(key)):
            if record.get("type") == "total" or record.get("name") == "overall":
                return text(record.get("summary")) or text(record.get("displayValue"))
    return None


def _score(competitor: Mapping[str, Any]) -> int | None:
    score = competitor.get("score")
    if isinstance(score, Mapping):
        score = score.get("value", score.get("displayValue"))
    return to_int(score)


def _rank(competitor: Mapping[str, Any]) -> int | None:
    rank = to_int(mapping(competitor.get("curatedRank")).get("current"))
    if rank is None:
        rank = to_int(competitor.get("rank"))
    return rank if rank is not None and 1 <= rank <= 25 else None


def _broadcast(values: Any) -> str | None:
    names: list[str] = []
    for value in items(values):
        kind = mapping(value.get("type"))
        if "Radio" in {kind.get("shortName"), kind.get("longName")}:
            continue
        for name in value.get("names") or []:
            if isinstance(name, str) and name not in names:
                names.append(name)
        media = text(mapping(value.get("media")).get("shortName"))
        market = mapping(value.get("market"))
        national = market.get("type") in {None, "National"} or value.get("market") == "national"
        if media and national and media not in names:
            names.append(media)
    return ", ".join(names) or None


def _competitor_columns(prefix: str, competitor: Mapping[str, Any], *, pregame: bool) -> Row:
    team = mapping(competitor.get("team"))
    return {
        f"{prefix}_team_id": to_int(team.get("id", competitor.get("id"))),
        f"{prefix}_team_name": text(team.get("displayName")),
        f"{prefix}_team_abbreviation": text(team.get("abbreviation")),
        f"{prefix}_rank": _rank(competitor),
        f"{prefix}_score": None if pregame else _score(competitor),
        f"{prefix}_winner": None if pregame else to_bool(competitor.get("winner")),
        f"{prefix}_record": _record_summary(competitor),
    }


def flatten_event(event: Mapping[str, Any], *, header: bool = False) -> Row | None:
    """Flatten a scoreboard event, schedule event or summary header into one game row."""
    competition = next(iter(items(event.get("competitions"))), {})
    game_id = to_int(event.get("id", competition.get("id")))
    if game_id is None:
        return None
    status = mapping(competition.get("status") or event.get("status"))
    status_type = mapping(status.get("type"))
    state = text(status_type.get("state"))
    pregame = state == "pre"
    competitors = items(competition.get("competitors"))
    home = next((value for value in competitors if value.get("homeAway") == "home"), {})
    away = next((value for value in competitors if value.get("homeAway") == "away"), {})
    venue = mapping(competition.get("venue"))
    address = mapping(venue.get("address"))
    season = mapping(event.get("season"))
    week = event.get("week")
    week_number = to_int(mapping(week).get("number")) if isinstance(week, Mapping) else to_int(week)
    groups = mapping(competition.get("groups"))
    note = next((text(value.get("headline")) for value in items(competition.get("notes"))), None)
    attendance = to_int(competition.get("attendance"))
    row: Row = {
        "game_id": game_id,
        "season": to_int(season.get("year")),
        "season_type": season_type_name(event.get("seasonType") or season.get("type")),
        "week": week_number,
        "start_time": to_timestamp(competition.get("date") or event.get("date")),
        "name": text(event.get("name")),
        "short_name": text(event.get("shortName")),
        "status": text(status_type.get("name")),
        "status_state": state,
        "status_detail": text(status_type.get("detail")) or text(status_type.get("description")),
        "completed": to_bool(status_type.get("completed")),
        "period": to_int(status.get("period")),
        "clock": text(status.get("displayClock")),
        "neutral_site": to_bool(competition.get("neutralSite")),
        "conference_game": to_bool(competition.get("conferenceCompetition")),
        "conference_id": to_int(groups.get("id")) if groups.get("isConference", True) else None,
        "conference_name": text(groups.get("name")) if groups.get("isConference", True) else None,
        "notes": note or text(event.get("gameNote")),
        "venue_id": to_int(venue.get("id")),
        "venue_name": text(venue.get("fullName")),
        "venue_city": text(address.get("city")),
        "venue_state": text(address.get("state")),
        "attendance": attendance if attendance else None,
        "broadcast": _broadcast(competition.get("broadcasts")),
        **_competitor_columns("home", home, pregame=pregame),
        **_competitor_columns("away", away, pregame=pregame),
    }
    if header:
        row["home_line_scores"] = _line_scores(home)
        row["away_line_scores"] = _line_scores(away)
    return row


def _line_scores(competitor: Mapping[str, Any]) -> list[int] | None:
    values = [
        to_int(entry.get("value", entry.get("displayValue"))) for entry in items(competitor.get("linescores"))
    ]
    return [value for value in values if value is not None] or None


def flatten_events(events: Any) -> list[Row]:
    """Flatten a list of events, dropping duplicates and rows without an id."""
    rows = [flatten_event(event) for event in items(events)]
    return unique_by((row for row in rows if row is not None), lambda row: (row["game_id"],))


def flatten_summary_game(payload: Mapping[str, Any]) -> list[Row]:
    """Flatten a summary document's header plus venue details into one game row."""
    header = mapping(payload.get("header"))
    event = dict(header)
    competitions = [dict(value) for value in items(header.get("competitions"))]
    info = mapping(payload.get("gameInfo"))
    if competitions:
        competitions[0].setdefault("venue", info.get("venue"))
        competitions[0].setdefault("attendance", info.get("attendance"))
        if not competitions[0].get("broadcasts"):
            competitions[0]["broadcasts"] = payload.get("broadcasts")
    event["competitions"] = competitions
    row = flatten_event(event, header=True)
    if row is None:
        return []
    home, away = row["home_team_name"], row["away_team_name"]
    if row["name"] is None and home and away:
        row["name"] = f"{away} {'vs' if row['neutral_site'] else 'at'} {home}"
    home_abbr, away_abbr = row["home_team_abbreviation"], row["away_team_abbreviation"]
    if row["short_name"] is None and home_abbr and away_abbr:
        row["short_name"] = f"{away_abbr} {'VS' if row['neutral_site'] else '@'} {home_abbr}"
    if row["period"] is None and row["completed"] and row["home_line_scores"]:
        row["period"] = len(row["home_line_scores"])
    return [row]


# --------------------------------------------------------------------------- box score / drives / plays


def flatten_box_team(payload: Mapping[str, Any], game_id: int) -> list[Row]:
    """One row per team statistic from a summary box score."""
    rows: list[Row] = []
    for team_block in items(mapping(payload.get("boxscore")).get("teams")):
        team = mapping(team_block.get("team"))
        for stat in items(team_block.get("statistics")):
            display = text(stat.get("displayValue"))
            rows.append(
                {
                    "game_id": game_id,
                    "team_id": to_int(team.get("id")),
                    "team_abbreviation": text(team.get("abbreviation")),
                    "home_away": text(team_block.get("homeAway")),
                    "stat_name": text(stat.get("name")),
                    "stat_label": text(stat.get("label")),
                    "display_value": display,
                    "value": to_float(display),
                }
            )
    return unique_by(rows, lambda row: (row["team_id"], row["stat_name"]))


def flatten_box_players(payload: Mapping[str, Any], game_id: int) -> list[Row]:
    """One row per player statistic from a summary box score."""
    rows: list[Row] = []
    for team_block in items(mapping(payload.get("boxscore")).get("players")):
        team = mapping(team_block.get("team"))
        for group in items(team_block.get("statistics")):
            keys = group.get("keys") if isinstance(group.get("keys"), list) else []
            labels = group.get("labels") if isinstance(group.get("labels"), list) else []
            for entry in items(group.get("athletes")):
                athlete = mapping(entry.get("athlete"))
                stats = entry.get("stats") if isinstance(entry.get("stats"), list) else []
                for index, key in enumerate(keys):
                    display = text(stats[index]) if index < len(stats) else None
                    rows.append(
                        {
                            "game_id": game_id,
                            "team_id": to_int(team.get("id")),
                            "team_abbreviation": text(team.get("abbreviation")),
                            "player_id": to_int(athlete.get("id")),
                            "player_name": text(athlete.get("displayName")),
                            "jersey": text(athlete.get("jersey")),
                            "category": text(group.get("name")),
                            "stat_name": text(key),
                            "stat_label": text(labels[index]) if index < len(labels) else None,
                            "display_value": display,
                            "value": to_float(display),
                        }
                    )
    return unique_by(rows, lambda row: (row["category"], row["player_id"], row["stat_name"]))


def _drives(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return items(mapping(payload.get("drives")).get("previous"))


def flatten_drives(payload: Mapping[str, Any], game_id: int) -> list[Row]:
    """One row per drive from a summary document."""
    rows: list[Row] = []
    for number, drive in enumerate(_drives(payload), start=1):
        team = mapping(drive.get("team"))
        start = mapping(drive.get("start"))
        end = mapping(drive.get("end"))
        rows.append(
            {
                "game_id": game_id,
                "drive_id": to_int(drive.get("id")),
                "drive_number": number,
                "team_id": to_int(team.get("id")),
                "team_abbreviation": text(team.get("abbreviation")),
                "description": text(drive.get("description")),
                "start_period": to_int(mapping(start.get("period")).get("number")),
                "start_clock": text(mapping(start.get("clock")).get("displayValue")),
                "start_yard_line": to_int(start.get("yardLine")),
                "start_text": text(start.get("text")),
                "end_period": to_int(mapping(end.get("period")).get("number")),
                "end_clock": text(mapping(end.get("clock")).get("displayValue")),
                "end_yard_line": to_int(end.get("yardLine")),
                "end_text": text(end.get("text")),
                "time_elapsed": text(mapping(drive.get("timeElapsed")).get("displayValue")),
                "yards": to_int(drive.get("yards")),
                "offensive_plays": to_int(drive.get("offensivePlays")),
                "is_score": to_bool(drive.get("isScore")),
                "result": text(drive.get("shortDisplayResult")) or text(drive.get("result")),
                "display_result": text(drive.get("displayResult")),
            }
        )
    return unique_by(rows, lambda row: (row["drive_id"],))


def flatten_plays(payload: Mapping[str, Any], game_id: int) -> list[Row]:
    """One row per play, in game order, from the drives of a summary document."""
    rows: list[Row] = []
    for number, drive in enumerate(_drives(payload), start=1):
        for play in items(drive.get("plays")):
            start = mapping(play.get("start"))
            end = mapping(play.get("end"))
            offense = next(
                (
                    to_int(value.get("id"))
                    for value in items(play.get("teamParticipants"))
                    if value.get("type") == "offense"
                ),
                None,
            )
            rows.append(
                {
                    "game_id": game_id,
                    "play_id": to_int(play.get("id")),
                    "sequence_number": to_int(play.get("sequenceNumber")),
                    "drive_id": to_int(drive.get("id")),
                    "drive_number": number,
                    "period": to_int(mapping(play.get("period")).get("number")),
                    "clock": text(mapping(play.get("clock")).get("displayValue")),
                    "play_type": text(mapping(play.get("type")).get("text")),
                    "text": text(play.get("text")),
                    "start_team_id": to_int(mapping(start.get("team")).get("id")),
                    "offense_team_id": offense,
                    "home_score": to_int(play.get("homeScore")),
                    "away_score": to_int(play.get("awayScore")),
                    "scoring_play": to_bool(play.get("scoringPlay")),
                    "is_turnover": to_bool(play.get("isTurnover")),
                    "is_penalty": to_bool(play.get("isPenalty")),
                    "yards": to_int(play.get("statYardage")),
                    "start_down": to_int(start.get("down")),
                    "start_distance": to_int(start.get("distance")),
                    "start_yards_to_endzone": to_int(start.get("yardsToEndzone")),
                    "end_down": to_int(end.get("down")),
                    "end_distance": to_int(end.get("distance")),
                    "end_yards_to_endzone": to_int(end.get("yardsToEndzone")),
                    "end_text": text(end.get("downDistanceText")),
                    "wallclock": to_timestamp(play.get("wallclock")),
                }
            )
    return unique_by(rows, lambda row: (row["play_id"],))


# --------------------------------------------------------------------------- standings / teams / conferences


def _division_name(payload: Mapping[str, Any]) -> str | None:
    return text(payload.get("abbreviation")) or text(payload.get("shortName")) or text(payload.get("name"))


def _conference_groups(
    payload: Mapping[str, Any],
) -> list[tuple[Mapping[str, Any], Mapping[str, Any] | None]]:
    """Return (conference, subdivision-or-None) pairs that carry standings entries."""
    result: list[tuple[Mapping[str, Any], Mapping[str, Any] | None]] = []
    for conference in items(payload.get("children")):
        if mapping(conference.get("standings")).get("entries") is not None:
            result.append((conference, None))
        for sub in items(conference.get("children")):
            result.append((conference, sub))
    return result


def _stats_by_type(entry: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {str(stat.get("type")): stat for stat in items(entry.get("stats")) if stat.get("type")}


def flatten_standings(payload: Mapping[str, Any]) -> list[Row]:
    """One row per team from a division standings tree, in conference order."""
    division = _division_name(payload)
    season = to_int(mapping(payload.get("season")).get("year"))
    rows: list[Row] = []
    for conference, sub in _conference_groups(payload):
        holder = sub if sub is not None else conference
        standings = mapping(holder.get("standings"))
        for position, entry in enumerate(items(standings.get("entries")), start=1):
            team = mapping(entry.get("team"))
            stats = _stats_by_type(entry)

            def display(key: str, stats: dict[str, Mapping[str, Any]] = stats) -> str | None:
                stat = stats.get(key, {})
                return text(stat.get("summary")) or text(stat.get("displayValue"))

            def value(key: str, stats: dict[str, Mapping[str, Any]] = stats) -> Any:
                return stats.get(key, {}).get("value")

            overall = display("total")
            wins, losses, ties = parse_record(overall)
            conf_record = display("vsconf")
            conf_wins, conf_losses, _ = parse_record(conf_record)
            decided = (wins or 0) + (losses or 0)
            games_back = stats.get("gamesbehind", {})
            rows.append(
                {
                    "season": to_int(standings.get("season")) or season,
                    "division": division,
                    "conference_id": to_int(conference.get("id")),
                    "conference_name": text(conference.get("name")),
                    "conference_abbreviation": text(conference.get("abbreviation")),
                    "subdivision_id": to_int(sub.get("id")) if sub is not None else None,
                    "subdivision_name": text(sub.get("name")) if sub is not None else None,
                    "team_id": to_int(team.get("id")),
                    "team_name": text(team.get("displayName")),
                    "team_abbreviation": text(team.get("abbreviation")),
                    "conference_rank": to_int(value("playoffseed")) or position,
                    "wins": wins,
                    "losses": losses,
                    "ties": ties,
                    "win_percentage": (
                        to_decimal(Decimal(wins or 0) / Decimal(decided), "0.001") if decided else None
                    ),
                    "overall_record": overall,
                    "conference_wins": conf_wins,
                    "conference_losses": conf_losses,
                    "conference_win_percentage": to_decimal(value("leaguewinpercent"), "0.001"),
                    "conference_record": conf_record,
                    "conference_games_back": (
                        Decimal("0.0")
                        if games_back.get("displayValue") == "-"
                        else to_decimal(games_back.get("value"), "0.1")
                    ),
                    "home_record": display("homerecord"),
                    "away_record": display("awayrecord"),
                    "vs_ap_top25_record": display("vsaprankedteams"),
                    "points_for": to_int(value("pointsfor")),
                    "points_against": to_int(value("pointsagainst")),
                    "point_differential": to_int(value("pointdifferential")),
                    "streak": text(stats.get("streak", {}).get("displayValue")),
                }
            )
    return unique_by(rows, lambda row: (row["team_id"],))


def flatten_teams(payload: Mapping[str, Any]) -> list[Row]:
    """One row per member team from a division standings tree."""
    division = _division_name(payload)
    rows: list[Row] = []
    for conference, sub in _conference_groups(payload):
        holder = sub if sub is not None else conference
        for entry in items(mapping(holder.get("standings")).get("entries")):
            team = mapping(entry.get("team"))
            rows.append(
                {
                    "team_id": to_int(team.get("id")),
                    "display_name": text(team.get("displayName")),
                    "short_display_name": text(team.get("shortDisplayName")),
                    "location": text(team.get("location")),
                    "nickname": text(team.get("name")),
                    "abbreviation": text(team.get("abbreviation")),
                    "division": division,
                    "conference_id": to_int(conference.get("id")),
                    "conference_name": text(conference.get("name")),
                    "conference_abbreviation": text(conference.get("abbreviation")),
                    "subdivision_id": to_int(sub.get("id")) if sub is not None else None,
                    "subdivision_name": text(sub.get("name")) if sub is not None else None,
                    "is_active": to_bool(team.get("isActive")),
                }
            )
    return unique_by(rows, lambda row: (row["team_id"],))


def flatten_conferences(payload: Mapping[str, Any]) -> list[Row]:
    """One row per conference from a division standings tree."""
    division = _division_name(payload)
    rows: list[Row] = []
    for conference in items(payload.get("children")):
        subs = items(conference.get("children"))
        holders = subs or [conference]
        team_ids = {
            to_int(mapping(entry.get("team")).get("id"))
            for holder in holders
            for entry in items(mapping(holder.get("standings")).get("entries"))
        }
        team_ids.discard(None)
        rows.append(
            {
                "conference_id": to_int(conference.get("id")),
                "name": text(conference.get("name")),
                "short_name": text(conference.get("shortName")),
                "abbreviation": text(conference.get("abbreviation")),
                "division": division,
                "team_count": len(team_ids),
                "subdivision_count": len(subs),
            }
        )
    return unique_by(rows, lambda row: (row["conference_id"],))


# --------------------------------------------------------------------------- rankings


def team_directory(payload: Mapping[str, Any]) -> dict[int, Row]:
    """Map team id to name and abbreviation from ESPN's team directory."""
    result: dict[int, Row] = {}
    for sport in items(payload.get("sports")):
        for league in items(sport.get("leagues")):
            for wrapper in items(league.get("teams")):
                team = mapping(wrapper.get("team"))
                team_id = to_int(team.get("id"))
                if team_id is not None:
                    result[team_id] = {
                        "team_name": text(team.get("displayName")),
                        "team_abbreviation": text(team.get("abbreviation")),
                    }
    return result


def _team_name(team: Mapping[str, Any]) -> str | None:
    name = text(team.get("displayName"))
    if name:
        return name
    parts = [text(team.get("location")), text(team.get("name"))]
    return " ".join(part for part in parts if part) or None


def flatten_poll(poll: Mapping[str, Any], teams: Mapping[int, Row] | None = None) -> list[Row]:
    """One row per ranked or vote-receiving team in a site or core poll document."""
    occurrence = mapping(poll.get("occurrence"))
    season = mapping(poll.get("season"))
    season_type = season_type_name(season.get("type")) if season.get("type") is not None else None
    if season_type is None:
        season_type = season_type_name(ref_id_type(poll.get("$ref")))
    base = {
        "season": to_int(season.get("year")) or ref_season(poll.get("$ref")),
        "season_type": season_type,
        "week": to_int(occurrence.get("number")),
        "poll_id": to_int(poll.get("id")),
        "poll_name": text(poll.get("name")),
        "poll_short_name": text(poll.get("shortName")),
        "poll_type": text(poll.get("type")),
        "poll_date": to_timestamp(poll.get("date")),
    }
    rows: list[Row] = []
    for receiving, entries in ((False, poll.get("ranks")), (True, poll.get("others"))):
        for entry in items(entries):
            team = mapping(entry.get("team"))
            team_id = to_int(team.get("id")) or ref_id(team.get("$ref"), "teams")
            known = (teams or {}).get(team_id or -1, {})
            rank = to_int(entry.get("current"))
            previous = to_int(entry.get("previous"))
            record = text(entry.get("recordSummary")) or text(mapping(entry.get("record")).get("summary"))
            rows.append(
                {
                    **base,
                    "rank": None if receiving or not rank else rank,
                    "previous_rank": previous if previous else None,
                    "points": to_int(entry.get("points")),
                    "first_place_votes": to_int(entry.get("firstPlaceVotes")),
                    "trend": text(entry.get("trend")),
                    "record": record,
                    "receiving_votes": receiving,
                    "team_id": team_id,
                    "team_name": _team_name(team) or known.get("team_name"),
                    "team_abbreviation": text(team.get("abbreviation")) or known.get("team_abbreviation"),
                }
            )
    return unique_by(rows, lambda row: (row["poll_id"], row["team_id"]))


_TYPE_IN_REF = re.compile(r"/types/(\d+)/")
_SEASON_IN_REF = re.compile(r"/seasons/(\d+)/")


def ref_id_type(ref: Any) -> int | None:
    """Season type embedded in a core ``$ref`` URL."""
    match = _TYPE_IN_REF.search(ref) if isinstance(ref, str) else None
    return int(match.group(1)) if match else None


def ref_season(ref: Any) -> int | None:
    """Season year embedded in a core ``$ref`` URL."""
    match = _SEASON_IN_REF.search(ref) if isinstance(ref, str) else None
    return int(match.group(1)) if match else None


# --------------------------------------------------------------------------- rosters / team stats


def flatten_roster(payload: Mapping[str, Any], team_id: int) -> list[Row]:
    """One row per rostered player."""
    rows: list[Row] = []
    for group in items(payload.get("athletes")):
        for athlete in items(group.get("items")):
            position = mapping(athlete.get("position"))
            birth = mapping(athlete.get("birthPlace"))
            rows.append(
                {
                    "team_id": team_id,
                    "player_id": to_int(athlete.get("id")),
                    "full_name": text(athlete.get("fullName")) or text(athlete.get("displayName")),
                    "first_name": text(athlete.get("firstName")),
                    "last_name": text(athlete.get("lastName")),
                    "jersey": text(athlete.get("jersey")),
                    "position": text(position.get("abbreviation")),
                    "position_name": text(position.get("displayName")) or text(position.get("name")),
                    "unit": text(group.get("position")),
                    "height_inches": to_int(athlete.get("height")),
                    "weight_lbs": to_int(athlete.get("weight")),
                    "class_year": text(mapping(athlete.get("experience")).get("displayValue")),
                    "birth_city": text(birth.get("city")),
                    "birth_state": text(birth.get("state")),
                    "status": text(mapping(athlete.get("status")).get("name")),
                }
            )
    return unique_by(rows, lambda row: (row["player_id"],))


def _stat_categories(block: Any) -> list[Mapping[str, Any]]:
    if isinstance(block, Mapping):
        return items(block.get("categories"))
    return items(block)


def flatten_team_stats(payload: Mapping[str, Any], team_id: int) -> list[Row]:
    """One row per team and opponent season statistic."""
    results = mapping(payload.get("results"))
    season = to_int(mapping(payload.get("requestedSeason")).get("year")) or to_int(
        mapping(payload.get("season")).get("year")
    )
    rows: list[Row] = []
    for side, key in (("team", "stats"), ("opponent", "opponent")):
        for category in _stat_categories(results.get(key)):
            for stat in items(category.get("stats")):
                display = text(stat.get("displayValue"))
                rows.append(
                    {
                        "team_id": team_id,
                        "season": season,
                        "side": side,
                        "category": text(category.get("name")),
                        "stat_name": text(stat.get("name")),
                        "stat_label": text(stat.get("abbreviation")) or text(stat.get("shortDisplayName")),
                        "display_value": display,
                        "value": to_float(stat.get("value"))
                        if stat.get("value") is not None
                        else to_float(display),
                        "per_game_value": to_float(stat.get("perGameValue")),
                        "rank": to_int(stat.get("rank")),
                    }
                )
    return unique_by(rows, lambda row: (row["side"], row["category"], row["stat_name"]))


# --------------------------------------------------------------------------- players


def flatten_leaders(payload: Mapping[str, Any]) -> list[Row]:
    """One row per player on each national leaderboard."""
    season = to_int(mapping(payload.get("requestedSeason")).get("year"))
    rows: list[Row] = []
    for category in items(mapping(payload.get("leaders")).get("categories")):
        for position, leader in enumerate(items(category.get("leaders")), start=1):
            athlete = mapping(leader.get("athlete"))
            team = mapping(leader.get("team")) or next(iter(items(leader.get("teams"))), {})
            rows.append(
                {
                    "season": season,
                    "category": text(category.get("name")),
                    "category_name": text(category.get("displayName")),
                    "rank": position,
                    "player_id": to_int(athlete.get("id")),
                    "player_name": text(athlete.get("displayName")),
                    "position": text(mapping(athlete.get("position")).get("abbreviation")),
                    "team_id": to_int(team.get("id")),
                    "team_name": text(team.get("displayName")),
                    "team_abbreviation": text(team.get("abbreviation")),
                    "value": to_float(leader.get("value")),
                    "display_value": text(leader.get("displayValue")),
                }
            )
    return unique_by(rows, lambda row: (row["category"], row["player_id"]))


def _category_for_index(payload: Mapping[str, Any], count: int) -> list[str | None]:
    """Expand the gamelog's category runs ({name, count}) into one entry per stat."""
    names: list[str | None] = []
    for category in items(payload.get("categories")):
        size = to_int(category.get("count")) or 0
        names.extend([text(category.get("name"))] * size)
    names.extend([None] * (count - len(names)))
    return names[:count]


def flatten_game_log(payload: Mapping[str, Any], player_id: int, season: int | None) -> list[Row]:
    """One row per player statistic per game from an athlete game log."""
    names = payload.get("names") if isinstance(payload.get("names"), list) else []
    labels = payload.get("labels") if isinstance(payload.get("labels"), list) else []
    categories = _category_for_index(payload, len(names))
    events = mapping(payload.get("events"))
    rows: list[Row] = []
    for season_block in items(payload.get("seasonTypes")):
        label = (text(season_block.get("displayName")) or "").lower()
        season_type = "postseason" if "postseason" in label else "regular"
        block_season = to_int(label.split(" ", 1)[0]) if label else None
        for category in items(season_block.get("categories")):
            for game in items(category.get("events")):
                event_id = text(game.get("eventId"))
                event = mapping(events.get(event_id)) if event_id else {}
                opponent = mapping(event.get("opponent"))
                stats = game.get("stats") if isinstance(game.get("stats"), list) else []
                at_vs = text(event.get("atVs"))
                for index, name in enumerate(names):
                    display = text(stats[index]) if index < len(stats) else None
                    rows.append(
                        {
                            "player_id": player_id,
                            "season": block_season or season,
                            "season_type": season_type,
                            "game_id": to_int(event_id),
                            "game_date": to_timestamp(event.get("gameDate")),
                            "week": to_int(event.get("week")),
                            "team_id": to_int(mapping(event.get("team")).get("id")),
                            "opponent_id": to_int(opponent.get("id")),
                            "opponent_name": text(opponent.get("displayName")),
                            "opponent_abbreviation": text(opponent.get("abbreviation")),
                            "home_away": "away" if at_vs == "@" else "home" if at_vs == "vs" else None,
                            "result": text(event.get("gameResult")),
                            "score": text(event.get("score")),
                            "event_note": text(event.get("eventNote")),
                            "category": categories[index],
                            "stat_name": text(name),
                            "stat_label": text(labels[index]) if index < len(labels) else None,
                            "display_value": display,
                            "value": to_float(display),
                        }
                    )
    return unique_by(rows, lambda row: (row["game_id"], row["category"], row["stat_name"]))


def flatten_player_seasons(payload: Mapping[str, Any], player_id: int) -> list[Row]:
    """One row per statistic per season (and team) from an athlete stats document."""
    teams = {
        to_int(team.get("id")): team
        for team in mapping(payload.get("teams")).values()
        if isinstance(team, Mapping)
    }
    rows: list[Row] = []
    for category in items(payload.get("categories")):
        names = category.get("names") if isinstance(category.get("names"), list) else []
        labels = category.get("labels") if isinstance(category.get("labels"), list) else []
        for line in items(category.get("statistics")):
            stats = line.get("stats") if isinstance(line.get("stats"), list) else []
            team_id = to_int(line.get("teamId"))
            team = mapping(teams.get(team_id))
            for index, name in enumerate(names):
                display = text(stats[index]) if index < len(stats) else None
                rows.append(
                    {
                        "player_id": player_id,
                        "season": to_int(mapping(line.get("season")).get("year")),
                        "team_id": team_id,
                        "team_name": text(team.get("displayName")),
                        "team_abbreviation": text(team.get("abbreviation")),
                        "position": text(line.get("position")),
                        "category": text(category.get("name")),
                        "stat_name": text(name),
                        "stat_label": text(labels[index]) if index < len(labels) else None,
                        "display_value": display,
                        "value": to_float(display),
                    }
                )
    return unique_by(rows, lambda row: (row["season"], row["team_id"], row["category"], row["stat_name"]))
