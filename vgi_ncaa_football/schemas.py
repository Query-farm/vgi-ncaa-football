"""Typed Arrow result schemas for every college-football table and function."""

from __future__ import annotations

import pyarrow as pa

from vgi_ncaa_football.meta import field

TIMESTAMP = pa.timestamp("us", tz="UTC")
PERCENTAGE = pa.decimal128(4, 3)
GAMES_BACK = pa.decimal128(5, 1)


def _side(prefix: str, label: str) -> list[pa.Field]:
    return [
        field(f"{prefix}_team_id", pa.int32(), f"ESPN team id (integer key) of the {label} team."),
        field(f"{prefix}_team_name", pa.string(), f"Full display name of the {label} team."),
        field(f"{prefix}_team_abbreviation", pa.string(), f"ESPN abbreviation of the {label} team."),
        field(
            f"{prefix}_rank",
            pa.int32(),
            f"{label.capitalize()} team's top-25 ranking position (1-25) at kickoff; NULL when unranked.",
        ),
        field(
            f"{prefix}_score",
            pa.int32(),
            f"Points scored by the {label} team (count of points); NULL before kickoff.",
        ),
        field(f"{prefix}_winner", pa.bool_(), f"Whether the {label} team won; NULL until decided."),
        field(
            f"{prefix}_record",
            pa.string(),
            f"{label.capitalize()} team's overall W-L record, as ESPN shows it.",
        ),
    ]


GAME_SCHEMA = pa.schema(
    [
        field("game_id", pa.int64(), "ESPN game id (integer key) for game_summary()."),
        field("season", pa.int32(), "Season year (the calendar year it starts)."),
        field("season_type", pa.string(), "regular or postseason."),
        field("week", pa.int32(), "Week number (1-based count) within the season type."),
        field("start_time", TIMESTAMP, "Scheduled kickoff time in UTC."),
        field("name", pa.string(), "Matchup name, e.g. 'Miami Hurricanes at Indiana Hoosiers'."),
        field("short_name", pa.string(), "Short matchup name, e.g. 'MIA @ IU'."),
        field("status", pa.string(), "ESPN status code, e.g. STATUS_FINAL."),
        field("status_state", pa.string(), "Coarse game state: pre, in or post."),
        field("status_detail", pa.string(), "Readable status, e.g. 'Final' or a kickoff time."),
        field("completed", pa.bool_(), "Whether the game is final."),
        field(
            "period",
            pa.int32(),
            "Current or final period number (1-4 quarters; 5 and above are overtime periods).",
        ),
        field("clock", pa.string(), "Game clock display value."),
        field("neutral_site", pa.bool_(), "Whether the game is played at a neutral site."),
        field("conference_game", pa.bool_(), "Whether it is a conference game."),
        field("conference_id", pa.int32(), "ESPN conference id (integer key) of a conference game."),
        field("conference_name", pa.string(), "Conference name of a conference game."),
        field("notes", pa.string(), "Bowl or playoff round name."),
        field("venue_id", pa.int32(), "ESPN venue id (integer key)."),
        field("venue_name", pa.string(), "Stadium name."),
        field("venue_city", pa.string(), "Stadium city."),
        field("venue_state", pa.string(), "Stadium state or region."),
        field(
            "attendance", pa.int32(), "Reported attendance as a count of spectators; NULL before the game."
        ),
        field("broadcast", pa.string(), "National TV networks, comma-separated."),
        *_side("home", "home"),
        *_side("away", "away"),
    ]
)

GAME_SUMMARY_SCHEMA = pa.schema(
    [
        *GAME_SCHEMA,
        field("home_line_scores", pa.list_(pa.int32()), "Home points by period, overtime included."),
        field("away_line_scores", pa.list_(pa.int32()), "Away points by period, overtime included."),
    ]
)

STANDINGS_SCHEMA = pa.schema(
    [
        field("season", pa.int32(), "Season year."),
        field("division", pa.string(), "NCAA Division I subdivision: FBS or FCS."),
        field(
            "conference_id",
            pa.int32(),
            "ESPN conference (group) id (integer key); joins to conferences().conference_id.",
        ),
        field("conference_name", pa.string(), "Full conference name, for example 'Southeastern Conference'."),
        field("conference_abbreviation", pa.string(), "ESPN conference abbreviation."),
        field(
            "subdivision_id",
            pa.int32(),
            "ESPN id (integer key) of the conference division, for conferences split into divisions.",
        ),
        field("subdivision_name", pa.string(), "Conference division name, for example 'Sun Belt - East'."),
        field("team_id", pa.int32(), "Stable ESPN team id (integer key); joins to teams().team_id."),
        field("team_name", pa.string(), "Full team display name."),
        field("team_abbreviation", pa.string(), "ESPN team abbreviation."),
        field(
            "conference_rank",
            pa.int32(),
            "1-based position in ESPN's conference standings order (1 = first).",
        ),
        field("wins", pa.int32(), "Count of games won, all opponents."),
        field("losses", pa.int32(), "Count of games lost, all opponents."),
        field("ties", pa.int32(), "Count of tied games (historical seasons only)."),
        field(
            "win_percentage",
            PERCENTAGE,
            "Fraction of decided games won (0.000-1.000), computed exactly from wins and losses.",
        ),
        field("overall_record", pa.string(), "Overall record as ESPN displays it, for example 8-1."),
        field("conference_wins", pa.int32(), "Count of conference games won."),
        field("conference_losses", pa.int32(), "Count of conference games lost."),
        field(
            "conference_win_percentage",
            PERCENTAGE,
            "Fraction of conference games won (0.000-1.000) as reported by ESPN.",
        ),
        field("conference_record", pa.string(), "Conference record as ESPN displays it."),
        field(
            "conference_games_back",
            GAMES_BACK,
            "Games behind the conference leader, in games (half-games allowed); the leader is 0.0.",
        ),
        field("home_record", pa.string(), "Wins-losses in home games, for example 6-0."),
        field("away_record", pa.string(), "Wins-losses in road games, for example 3-1."),
        field("vs_ap_top25_record", pa.string(), "Record against AP Top 25 opponents."),
        field("points_for", pa.int32(), "Total points scored in all games (count of points)."),
        field("points_against", pa.int32(), "Total points allowed in all games (count of points)."),
        field("point_differential", pa.int32(), "Points scored minus points allowed (net points)."),
        field("streak", pa.string(), "Current streak, for example W6 or L1."),
    ]
)

TEAM_SCHEMA = pa.schema(
    [
        field("team_id", pa.int32(), "Stable ESPN team id (integer key); joins to teams().team_id."),
        field("display_name", pa.string(), "Full team name, for example 'Indiana Hoosiers'."),
        field("short_display_name", pa.string(), "Short team name, for example 'Indiana'."),
        field("location", pa.string(), "School or location part of the name."),
        field("nickname", pa.string(), "Mascot part of the name, for example 'Hoosiers'."),
        field("abbreviation", pa.string(), "ESPN team abbreviation."),
        field("division", pa.string(), "FBS or FCS."),
        field(
            "conference_id",
            pa.int32(),
            "ESPN conference (group) id (integer key); joins to conferences().conference_id.",
        ),
        field("conference_name", pa.string(), "Full conference name, for example 'Southeastern Conference'."),
        field("conference_abbreviation", pa.string(), "ESPN conference abbreviation."),
        field("subdivision_id", pa.int32(), "Conference division id when the conference is split."),
        field("subdivision_name", pa.string(), "Conference division name when the conference is split."),
        field("is_active", pa.bool_(), "Whether ESPN marks the program active."),
    ]
)

CONFERENCE_SCHEMA = pa.schema(
    [
        field(
            "conference_id",
            pa.int32(),
            "ESPN conference (group) identifier; usable as scoreboard(conference_id).",
        ),
        field("name", pa.string(), "Full conference name, for example 'Southeastern Conference'."),
        field("short_name", pa.string(), "Short conference name."),
        field("abbreviation", pa.string(), "ESPN conference abbreviation."),
        field("division", pa.string(), "FBS or FCS."),
        field("team_count", pa.int32(), "Number of member teams in the requested season's standings."),
        field("subdivision_count", pa.int32(), "Number of conference divisions; 0 when undivided."),
    ]
)

RANKING_SCHEMA = pa.schema(
    [
        field("season", pa.int32(), "Season year of the poll."),
        field("season_type", pa.string(), "regular or postseason (final polls are postseason week 1)."),
        field("week", pa.int32(), "Poll week number (1-based count within the season type)."),
        field(
            "poll_id",
            pa.int32(),
            "ESPN poll id (integer key): 1 AP, 2 Coaches, 21 CFP, 20 FCS Coaches, 11/12 D-II/D-III.",
        ),
        field("poll_name", pa.string(), "Poll name, for example 'AP Top 25'."),
        field("poll_short_name", pa.string(), "Short poll name, for example 'AP Poll'."),
        field("poll_type", pa.string(), "ESPN poll type code: ap, usa, cfp, fcs or afca."),
        field("poll_date", TIMESTAMP, "Date ESPN associates with the poll release."),
        field("rank", pa.int32(), "Ranking position (1 = top); NULL for teams only receiving votes."),
        field(
            "previous_rank",
            pa.int32(),
            "Ranking position (1 = top) in the previous edition; NULL when previously unranked.",
        ),
        field("points", pa.int32(), "Poll points total from voters' ballots (CFP rankings carry 0)."),
        field("first_place_votes", pa.int32(), "Count of voters ranking the team number one."),
        field("trend", pa.string(), "Movement since the previous edition, for example +2 or -."),
        field("record", pa.string(), "Team record at poll time."),
        field("receiving_votes", pa.bool_(), "True for 'others receiving votes' rows outside the ranking."),
        field("team_id", pa.int32(), "Stable ESPN team id (integer key); joins to teams().team_id."),
        field("team_name", pa.string(), "Full team display name."),
        field("team_abbreviation", pa.string(), "ESPN team abbreviation."),
    ]
)

ROSTER_SCHEMA = pa.schema(
    [
        field("team_id", pa.int32(), "ESPN team identifier."),
        field("player_id", pa.int64(), "Stable ESPN athlete id (integer key)."),
        field("full_name", pa.string(), "Player full name."),
        field("first_name", pa.string(), "First name."),
        field("last_name", pa.string(), "Last name."),
        field("jersey", pa.string(), "Jersey number."),
        field("position", pa.string(), "Position abbreviation, for example QB or OL."),
        field("position_name", pa.string(), "Position name, for example Quarterback."),
        field("unit", pa.string(), "Roster group: offense, defense, specialTeam, injuredReserveOrOut, ..."),
        field("height_inches", pa.int32(), "Listed height in inches."),
        field("weight_lbs", pa.int32(), "Listed weight in pounds."),
        field("class_year", pa.string(), "Eligibility class, for example Freshman or Senior."),
        field("birth_city", pa.string(), "Hometown city."),
        field("birth_state", pa.string(), "Hometown state or region."),
        field("status", pa.string(), "ESPN roster status, for example Active."),
    ]
)

_STAT_COMMON = [
    field("category", pa.string(), "Statistical category, for example passing, rushing or defensive."),
    field("stat_name", pa.string(), "ESPN statistic key, for example passingYards."),
    field("stat_label", pa.string(), "Short label, for example YDS."),
    field("display_value", pa.string(), "Value exactly as ESPN displays it, for example 24/43 or 29:05."),
    field(
        "value",
        pa.float64(),
        "Numeric value in the statistic's unit (yards, counts, percent); NULL for compound values.",
    ),
]

TEAM_STATS_SCHEMA = pa.schema(
    [
        field("team_id", pa.int32(), "ESPN team identifier."),
        field("season", pa.int32(), "Season year the totals belong to."),
        field("side", pa.string(), "team for the team's own totals, opponent for its opponents' totals."),
        *_STAT_COMMON,
        field("per_game_value", pa.float64(), "Per-game value when ESPN supplies one."),
        field("rank", pa.int32(), "National rank ESPN reports for the statistic, when supplied."),
    ]
)

BOX_TEAM_SCHEMA = pa.schema(
    [
        field("game_id", pa.int64(), "ESPN game identifier."),
        field("team_id", pa.int32(), "ESPN team identifier."),
        field("team_abbreviation", pa.string(), "ESPN team abbreviation."),
        field("home_away", pa.string(), "home or away."),
        field("stat_name", pa.string(), "ESPN team statistic key, for example totalYards or thirdDownEff."),
        field("stat_label", pa.string(), "Readable label, for example 'Total Yards'."),
        field("display_value", pa.string(), "Value exactly as ESPN displays it, for example 6-14 or 29:05."),
        field(
            "value",
            pa.float64(),
            "Numeric value in the statistic's unit (yards, counts, percent); NULL for compound values.",
        ),
    ]
)

BOX_PLAYER_SCHEMA = pa.schema(
    [
        field("game_id", pa.int64(), "ESPN game identifier."),
        field("team_id", pa.int32(), "ESPN team identifier."),
        field("team_abbreviation", pa.string(), "ESPN team abbreviation."),
        field("player_id", pa.int64(), "Stable ESPN athlete id (integer key)."),
        field("player_name", pa.string(), "Player display name."),
        field("jersey", pa.string(), "Jersey number."),
        *_STAT_COMMON,
    ]
)

DRIVE_SCHEMA = pa.schema(
    [
        field("game_id", pa.int64(), "ESPN game identifier."),
        field("drive_id", pa.int64(), "ESPN drive identifier."),
        field("drive_number", pa.int32(), "1-based drive order within the game."),
        field("team_id", pa.int32(), "ESPN identifier of the team on offense."),
        field("team_abbreviation", pa.string(), "Abbreviation of the team on offense."),
        field("description", pa.string(), "Summary, for example '12 plays, 50 yards, 5:22'."),
        field("start_period", pa.int32(), "Quarter the drive started in."),
        field("start_clock", pa.string(), "Game clock when the drive started."),
        field("start_yard_line", pa.int32(), "ESPN 0-100 yard line where the drive started."),
        field("start_text", pa.string(), "Field position where the drive started, for example 'ND 25'."),
        field("end_period", pa.int32(), "Quarter the drive ended in."),
        field("end_clock", pa.string(), "Game clock when the drive ended."),
        field("end_yard_line", pa.int32(), "ESPN 0-100 yard line where the drive ended."),
        field("end_text", pa.string(), "Field position where the drive ended."),
        field("time_elapsed", pa.string(), "Drive duration as displayed (m:ss)."),
        field("yards", pa.int32(), "Net yards gained."),
        field("offensive_plays", pa.int32(), "Offensive plays run."),
        field("is_score", pa.bool_(), "Whether the drive ended in a score."),
        field("result", pa.string(), "Short result code, for example TD, FG, PUNT or INT."),
        field("display_result", pa.string(), "Readable result, for example 'Field Goal'."),
    ]
)

PLAY_SCHEMA = pa.schema(
    [
        field("game_id", pa.int64(), "ESPN game identifier."),
        field("play_id", pa.int64(), "ESPN play identifier."),
        field("sequence_number", pa.int32(), "ESPN sequence number ordering plays within the game."),
        field("drive_id", pa.int64(), "ESPN drive identifier the play belongs to."),
        field("drive_number", pa.int32(), "1-based drive order within the game."),
        field("period", pa.int32(), "Quarter; 5 and above are overtime periods."),
        field("clock", pa.string(), "Game clock at the snap."),
        field("play_type", pa.string(), "Play type, for example Rush, Pass Reception or Kickoff."),
        field("text", pa.string(), "Play-by-play narrative text."),
        field(
            "start_team_id",
            pa.int32(),
            "Team ESPN lists at the start of the play (the kicking team on kicks).",
        ),
        field("offense_team_id", pa.int32(), "Team ESPN lists as the offense for the play."),
        field("home_score", pa.int32(), "Home score after the play."),
        field("away_score", pa.int32(), "Away score after the play."),
        field("scoring_play", pa.bool_(), "Whether the play scored points."),
        field("is_turnover", pa.bool_(), "Whether the play was a turnover."),
        field("is_penalty", pa.bool_(), "Whether a penalty was called on the play."),
        field("yards", pa.int32(), "Yardage credited to the play."),
        field("start_down", pa.int32(), "Down before the play."),
        field("start_distance", pa.int32(), "Yards to go before the play."),
        field("start_yards_to_endzone", pa.int32(), "Yards to the opponent's end zone before the play."),
        field("end_down", pa.int32(), "Down after the play."),
        field("end_distance", pa.int32(), "Yards to go after the play."),
        field("end_yards_to_endzone", pa.int32(), "Yards to the end zone after the play."),
        field("end_text", pa.string(), "Down and distance after the play, for example '1st & 10 at ND 25'."),
        field("wallclock", TIMESTAMP, "Real-world time of the play, when ESPN records it."),
    ]
)

LEADER_SCHEMA = pa.schema(
    [
        field("season", pa.int32(), "Season year of the leaderboard."),
        field("category", pa.string(), "Leaderboard key, for example passingYards or totalTackles."),
        field("category_name", pa.string(), "Readable leaderboard name, for example 'Passing Yards'."),
        field("rank", pa.int32(), "1-based position on the leaderboard (ties keep ESPN's order)."),
        field("player_id", pa.int64(), "Stable ESPN athlete id (integer key)."),
        field("player_name", pa.string(), "Player display name."),
        field("position", pa.string(), "Position abbreviation."),
        field("team_id", pa.int32(), "ESPN team id (integer key) of the player's team."),
        field("team_name", pa.string(), "Player's team display name."),
        field("team_abbreviation", pa.string(), "Player's team abbreviation."),
        field(
            "value",
            pa.float64(),
            "Season total in the category's unit (yards, touchdowns, tackles), postseason included.",
        ),
        field("display_value", pa.string(), "Value exactly as ESPN displays it."),
    ]
)

GAME_LOG_SCHEMA = pa.schema(
    [
        field("player_id", pa.int64(), "Stable ESPN athlete id (integer key)."),
        field("season", pa.int32(), "Season year of the log."),
        field("season_type", pa.string(), "regular or postseason."),
        field("game_id", pa.int64(), "ESPN game identifier; pass it to game_summary()."),
        field("game_date", TIMESTAMP, "Kickoff time in UTC."),
        field("week", pa.int32(), "Week number (1-based count) within the season type."),
        field("team_id", pa.int32(), "ESPN identifier of the player's team in that game."),
        field("opponent_id", pa.int32(), "ESPN identifier of the opponent."),
        field("opponent_name", pa.string(), "Opponent display name."),
        field("opponent_abbreviation", pa.string(), "Opponent abbreviation."),
        field("home_away", pa.string(), "home or away from the player's perspective."),
        field("result", pa.string(), "Game result for the player's team: W, L or T."),
        field("score", pa.string(), "Final score from the player's team perspective, for example 27-21."),
        field("event_note", pa.string(), "Bowl or playoff name, when present."),
        *_STAT_COMMON,
    ]
)

PLAYER_SEASON_SCHEMA = pa.schema(
    [
        field("player_id", pa.int64(), "Stable ESPN athlete id (integer key)."),
        field("season", pa.int32(), "Season year of the line."),
        field("team_id", pa.int32(), "ESPN identifier of the team the line was compiled for."),
        field("team_name", pa.string(), "Team display name, when ESPN supplies it."),
        field("team_abbreviation", pa.string(), "Team abbreviation, when ESPN supplies it."),
        field("position", pa.string(), "Position abbreviation for that season."),
        *_STAT_COMMON,
    ]
)
