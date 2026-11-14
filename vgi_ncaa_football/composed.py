"""Example queries that combine two or more of the catalog's functions.

Each SQL string is defined once here and referenced from the example list of the function
that drives the query and from the schema-level example list in ``worker.py``. All of them
pin the completed 2025 season, so their rows do not move week to week.
"""

from __future__ import annotations

# team_schedule() x standings(): every opponent's final record. division := 'all' keeps
# Indiana's FCS opponent (Indiana State), which FBS-only standings would drop.
SCHEDULE_OPPONENT_RECORDS = (
    "Indiana's 2025 strength of schedule: every opponent, regular season and playoff, with that "
    "opponent's final overall record and conference.",
    "SELECT s.season_type, s.week, s.away_team_abbreviation, s.home_team_abbreviation, "
    "opp.team_name AS opponent, opp.overall_record AS opponent_record, opp.conference_name "
    "FROM ncaa_football.main.team_schedule(84, season := 2025) s "
    "JOIN ncaa_football.main.standings(season := 2025, division := 'all') opp "
    "ON opp.team_id = CASE WHEN s.home_team_id = 84 THEN s.away_team_id ELSE s.home_team_id END "
    "ORDER BY s.start_time",
)

# rankings() x standings(): the committee's top 12 next to where each team finished in its
# conference. Week 16 is the final (selection-day) committee ranking of the 2025 season.
CFP_TOP_12_CONFERENCE_FINISH = (
    "The final 2025 CFP committee top 12 (selection day, week 16) with each team's conference, "
    "conference finish and final overall record.",
    "SELECT r.rank, r.team_name, r.record AS record_when_ranked, st.conference_abbreviation, "
    "st.conference_rank, st.conference_record, st.overall_record AS final_record "
    "FROM ncaa_football.main.rankings(season := 2025, week := 16, poll := 'cfp') r "
    "JOIN ncaa_football.main.standings(season := 2025) st USING (team_id) "
    "WHERE r.rank <= 12 ORDER BY r.rank",
)

# scoreboard() x rankings() twice: bowl and playoff games between two ranked teams.
RANKED_BOWL_MATCHUPS = (
    "2025 bowl and playoff games between two teams in the final CFP committee top 25, with both "
    "rankings and the final score, to spot upsets.",
    "SELECT g.start_time, g.notes, g.away_team_abbreviation, away_cfp.rank AS away_rank, g.away_score, "
    "g.home_team_abbreviation, home_cfp.rank AS home_rank, g.home_score "
    "FROM ncaa_football.main.scoreboard(season := 2025, season_type := 'postseason') g "
    "JOIN ncaa_football.main.rankings(season := 2025, week := 16, poll := 'cfp') away_cfp "
    "ON away_cfp.team_id = g.away_team_id AND away_cfp.rank IS NOT NULL "
    "JOIN ncaa_football.main.rankings(season := 2025, week := 16, poll := 'cfp') home_cfp "
    "ON home_cfp.team_id = g.home_team_id AND home_cfp.rank IS NOT NULL "
    "ORDER BY g.start_time",
)

# stat_leaders() x standings(): individual production next to team success.
PASSING_LEADERS_TEAM_RECORDS = (
    "The 2025 national passing-yards top ten with each quarterback's team, its final overall "
    "record and conference.",
    "SELECT l.rank, l.player_name, l.team_abbreviation, l.value AS passing_yards, "
    "st.overall_record AS team_record, st.conference_name "
    "FROM ncaa_football.main.stat_leaders(season := 2025) l "
    "JOIN ncaa_football.main.standings(season := 2025) st USING (team_id) "
    "WHERE l.category = 'passingYards' ORDER BY l.rank",
)

COMPOSED_EXAMPLES: tuple[tuple[str, str], ...] = (
    RANKED_BOWL_MATCHUPS,
    CFP_TOP_12_CONFERENCE_FINISH,
    SCHEDULE_OPPONENT_RECORDS,
    PASSING_LEADERS_TEAM_RECORDS,
)
