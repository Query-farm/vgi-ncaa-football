<p align="center">
  <a href="https://query.farm/vgi/">
    <img src="https://raw.githubusercontent.com/Query-farm/vgi-kalshi/main/docs/vgi-logo.png" alt="Vector Gateway Interface logo" width="320">
  </a>
</p>

<h1 align="center">vgi-ncaa-football</h1>

<p align="center">
  NCAA FBS and FCS college football scores, standings, polls, box scores and player statistics as ordinary DuckDB tables.<br>
  A <strong>read-only</strong> <a href="https://query.farm/vgi/">VGI</a> worker, built by <a href="https://query.farm">🚜 Query.Farm</a>
</p>

<p align="center">
  <a href="https://github.com/Query-farm/vgi-ncaa-football/actions/workflows/ci.yml"><img src="https://github.com/Query-farm/vgi-ncaa-football/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT license"></a>
  <img src="https://img.shields.io/badge/python-3.13%2B-3776AB.svg" alt="Python 3.13+">
  <a href="https://query.farm/vgi/"><img src="https://img.shields.io/badge/VGI-read--only-6f42c1.svg" alt="Read-only VGI worker"></a>
</p>

---

`vgi-ncaa-football` fetches college-football data on demand from ESPN's public
JSON endpoints and reformats the responses as typed Arrow data for Haybarn SQL.
It requires no credentials and does not rehost or persist a dataset. See
[Data source, terms and affiliation](#data-source-terms-and-affiliation) before
using it.

## Quick start

The worker is not published to PyPI. `uvx` runs the tagged release straight from
GitHub and caches it after the first use:

```bash
uvx --from git+https://github.com/Query-farm/vgi-ncaa-football@v0.2.0 vgi-ncaa-football --help
```

Launch Haybarn with the catalog pre-attached:

```bash
uvx haybarn-cli \
  -cmd "INSTALL vgi FROM community; LOAD vgi; ATTACH 'ncaa_football' (TYPE vgi, LOCATION 'uvx --from git+https://github.com/Query-farm/vgi-ncaa-football@v0.2.0 vgi-ncaa-football');" \
  :memory:
```

Or attach it in an existing Haybarn session:

```sql
INSTALL vgi FROM community;
LOAD vgi;
ATTACH 'ncaa_football' (
  TYPE vgi,
  LOCATION 'uvx --from git+https://github.com/Query-farm/vgi-ncaa-football@v0.2.0 vgi-ncaa-football'
);
```

For source development, run `uv lock --check --no-sources`, then
`uv sync --no-sources` and `uv run --no-sources vgi-ncaa-football`.
The lockfile resolves all dependencies from package registries and does not
require a sibling checkout.

## Catalog surface

The `ncaa_football.main` schema exposes four current-season tables and sixteen
parameterized table functions:

| Kind | Objects |
| --- | --- |
| Current season | `current_scoreboard`, `current_standings`, `current_rankings`, `current_stat_leaders` |
| Scores and schedules | `scoreboard()`, `team_schedule()` |
| Standings and polls | `standings()`, `rankings()` |
| Teams and conferences | `teams()`, `conferences()`, `roster()`, `team_season_stats()` |
| Game detail | `game_summary()`, `box_score_team()`, `box_score_player()`, `drives()`, `play_by_play()` |
| Player statistics | `stat_leaders()`, `player_game_log()`, `player_season_stats()` |

Seasons are labelled by the year they start: `season := 2025` is the 2025-26
season, including the January bowls and the College Football Playoff final.
`division` accepts `fbs` (Bowl Subdivision), `fcs` (Championship Subdivision) or
`all`. Functions take ESPN ids: `teams()` and `conferences()` look up `team_id`
and `conference_id`, and `scoreboard()`/`team_schedule()` return the `game_id`
values the per-game functions need. Each `all_current_*` function is the scan
behind the matching `current_*` table.

## Usage

### Scores and schedules

```sql
-- This week's ranked FBS matchups
SELECT start_time, away_rank, away_team_name, home_rank, home_team_name, status_detail
FROM ncaa_football.main.current_scoreboard
WHERE home_rank IS NOT NULL OR away_rank IS NOT NULL
ORDER BY start_time;

-- Every 2025 bowl and playoff result
SELECT start_time, notes, away_team_name, away_score, home_team_name, home_score
FROM ncaa_football.main.scoreboard(season := 2025, season_type := 'postseason')
ORDER BY start_time;

-- FCS opening week, or one conference's games
SELECT short_name, away_score, home_score
FROM ncaa_football.main.scoreboard(season := 2025, week := 1, division := 'fcs');

SELECT short_name, away_score, home_score
FROM ncaa_football.main.scoreboard(season := 2025, week := 14, conference_id := 5);

-- Indiana's unbeaten 2025 season (team id 84), regular season and playoff
SELECT season_type, week, away_team_abbreviation, away_score,
       home_team_abbreviation, home_score, notes
FROM ncaa_football.main.team_schedule(84, season := 2025)
ORDER BY start_time;
```

Scores are `NULL` before kickoff. Games between FBS and FCS teams appear in both
divisions; `division := 'all'` returns each game once. ESPN's team-schedule feed
omits conference and venue ids, so `team_schedule()` leaves `conference_game`,
`conference_id`, `conference_name` and `venue_id` `NULL`; `scoreboard()` and
`game_summary()` fill them.

### Standings and rankings

```sql
-- Current SEC standings
SELECT conference_rank, team_name, overall_record, conference_record
FROM ncaa_football.main.current_standings
WHERE conference_abbreviation = 'sec'
ORDER BY conference_rank;

-- Final 2025 Big Ten standings
SELECT conference_rank, team_name, overall_record, conference_record
FROM ncaa_football.main.standings(season := 2025)
WHERE conference_abbreviation = 'big10'
ORDER BY conference_rank;

-- Current AP top ten (poll_id 1; 2 = Coaches, 21 = CFP committee, 20 = FCS)
SELECT rank, team_name, record, first_place_votes
FROM ncaa_football.main.current_rankings
WHERE poll_id = 1 AND rank <= 10
ORDER BY rank;

-- The 2025 week-15 College Football Playoff committee ranking
SELECT rank, team_name, record
FROM ncaa_football.main.rankings(season := 2025, week := 15, poll := 'cfp')
WHERE rank IS NOT NULL
ORDER BY rank;

-- The final 2025 AP poll (season without a week = final postseason polls)
SELECT rank, team_name, first_place_votes
FROM ncaa_football.main.rankings(season := 2025, poll := 'ap')
WHERE rank <= 10
ORDER BY rank;
```

`win_percentage` is computed exactly from overall wins and losses;
`conference_win_percentage` is ESPN's conference figure. Teams only receiving
votes appear in poll results with `rank IS NULL` and `receiving_votes = true`.

### Combining functions

Every row carries ESPN ids, so the functions join to each other. Week 16 is the
final, selection-day CFP committee ranking of 2025:

```sql
-- 2025 bowl and playoff games between two final-CFP top-25 teams: spot the upsets
SELECT g.start_time, g.notes,
       g.away_team_abbreviation, away_cfp.rank AS away_rank, g.away_score,
       g.home_team_abbreviation, home_cfp.rank AS home_rank, g.home_score
FROM ncaa_football.main.scoreboard(season := 2025, season_type := 'postseason') g
JOIN ncaa_football.main.rankings(season := 2025, week := 16, poll := 'cfp') away_cfp
  ON away_cfp.team_id = g.away_team_id AND away_cfp.rank IS NOT NULL
JOIN ncaa_football.main.rankings(season := 2025, week := 16, poll := 'cfp') home_cfp
  ON home_cfp.team_id = g.home_team_id AND home_cfp.rank IS NOT NULL
ORDER BY g.start_time;
```

### Game detail

The per-game functions all read one ESPN game summary, which the worker caches,
so querying several of them for the same game costs one upstream request.

```sql
SELECT name, notes, venue_name, attendance, home_score, away_score,
       home_line_scores, away_line_scores
FROM ncaa_football.main.game_summary(401769076);

-- Box scores are long format: one row per team (or player) and statistic
SELECT team_abbreviation, stat_label, display_value
FROM ncaa_football.main.box_score_team(401769076)
WHERE stat_name IN ('totalYards', 'netPassingYards', 'rushingYards', 'turnovers');

SELECT player_name, team_abbreviation, value AS passing_yards
FROM ncaa_football.main.box_score_player(401769076)
WHERE category = 'passing' AND stat_name = 'passingYards'
ORDER BY value DESC;

SELECT drive_number, team_abbreviation, start_text, offensive_plays, yards, display_result
FROM ncaa_football.main.drives(401769076)
ORDER BY drive_number;

SELECT period, clock, play_type, text
FROM ncaa_football.main.play_by_play(401769076)
WHERE scoring_play
ORDER BY sequence_number;
```

`value` is numeric only when ESPN's display value is a single number; compound
values such as `19/32` or `6-14` stay in `display_value`.

### Teams, rosters and statistics

```sql
SELECT team_id, display_name, conference_name
FROM ncaa_football.main.teams(division := 'all')
WHERE display_name ILIKE '%state%';

SELECT jersey, full_name, position, class_year, height_inches, weight_lbs
FROM ncaa_football.main.roster(84)
ORDER BY position, full_name;

-- ESPN repeats some statistics (totalPoints) under several categories,
-- so filter on category as well as stat_name
SELECT side, category, stat_name, display_value, per_game_value, rank
FROM ncaa_football.main.team_season_stats(84, season := 2025)
WHERE (category, stat_name) IN (('passing', 'passingYards'),
                                ('rushing', 'rushingYards'),
                                ('scoring', 'totalPoints'))
ORDER BY side, category;

SELECT category_name, rank, player_name, team_abbreviation, display_value
FROM ncaa_football.main.stat_leaders(season := 2025)
WHERE rank <= 3
ORDER BY category, rank;

-- Fernando Mendoza's 2025 game log and his career by season and school
SELECT game_date, opponent_abbreviation, result, score, value AS passing_yards
FROM ncaa_football.main.player_game_log(4837248, season := 2025)
WHERE stat_name = 'passingYards'
ORDER BY game_date;

SELECT season, team_abbreviation, stat_name, display_value
FROM ncaa_football.main.player_season_stats(4837248)
WHERE category = 'passing' AND stat_name IN ('passingYards', 'passingTouchdowns')
ORDER BY season, stat_name;
```

Known upstream limits: ESPN publishes current rosters only, its national
leaderboards are top-ten per category (full-season totals, postseason included)
and do not accept a division filter, and its per-athlete `byathlete` endpoint
currently returns empty offensive values for college football, so it is not
used.

## Data source, terms and affiliation

All data comes from ESPN's **undocumented** public endpoints
(`site.api.espn.com`, `sports.core.api.espn.com` and `site.web.api.espn.com`).
They are not a published API, carry no licence grant, and can change or
disappear without notice. ESPN's content is subject to the
[Disney Terms of Use](https://disneytermsofuse.com/), which restrict automated
access; **you are responsible for ensuring your use complies with ESPN's terms
and any other upstream terms.**

This project is **not affiliated with, endorsed by, or sponsored by ESPN, The
Walt Disney Company, the NCAA, or any conference or school.** Team, conference
and poll names are used only to identify the data.

To keep its footprint small, the worker:

- fetches only what a query asks for, on demand, and never stores, mirrors or
  redistributes a dataset;
- caches responses briefly in process (at least 60 seconds for live data, six
  hours for completed seasons, one hour for directories), honoring any longer
  `max-age` ESPN sends;
- paces its own requests (about four per second per process at most) and backs
  off on HTTP 429 and 5xx responses;
- identifies itself honestly as
  `vgi-ncaa-football/<version> (+https://github.com/Query-farm/vgi-ncaa-football)`;
- does not expose betting odds, logos, images or news content.

The worker's own code is MIT-licensed; that licence does not extend to the data.

## Production HTTP deployment

Stdio is the recommended transport when Haybarn can launch the worker locally.
Use HTTP when the worker must be shared across hosts. The production HTTP entry
point fails closed unless it has a stable signing key and either authentication
or an explicit anonymous-access decision:

```bash
export VGI_SIGNING_KEY='a-long-random-secret-shared-by-every-replica'
export VGI_BEARER_TOKENS='replace-with-a-random-token=haybarn'

uvx --from git+https://github.com/Query-farm/vgi-ncaa-football@v0.2.0 vgi-ncaa-football-http \
  --host 0.0.0.0 \
  --port 8000 \
  --cors-origins 'https://sql.example.com' \
  --log-format json
```

`vgi-ncaa-football-http` disables the description pages and cross-origin access
unless they are explicitly configured. Terminate TLS at a trusted proxy or load
balancer. All replicas must receive the same `VGI_SIGNING_KEY`; rotate it as a
coordinated deployment because outstanding state tokens are signed with it.

Supported authentication options are:

- `VGI_BEARER_TOKENS`: comma-separated `token=principal` pairs.
- `VGI_JWT_ISSUER` and `VGI_JWT_AUDIENCE`: JWT/JWKS authentication.
- `VGI_PROXY_PROOF_MODE=require` with the corresponding VGI proxy-proof
  settings when a trusted proxy authenticates users.

An intentionally public service can set `VGI_ALLOW_ANONYMOUS=1`, but because
every query reaches ESPN, a public deployment should sit behind gateway rate
limits. The health probe is `GET /health`.

### Container

```bash
docker build -t vgi-ncaa-football .
docker run --rm -p 8000:8000 \
  -e VGI_SIGNING_KEY='replace-with-a-stable-random-secret' \
  -e VGI_BEARER_TOKENS='replace-with-a-random-token=haybarn' \
  vgi-ncaa-football
```

The image runs as an unprivileged user, emits JSON logs, disables interactive
description pages, and includes a health check.

## Reliability and operations

Upstream calls use a 30-second request timeout, a 120-second total query
deadline, at most four attempts, at most four simultaneous ESPN requests per
process, and a minimum spacing between requests (`NCAAF_MIN_REQUEST_INTERVAL`,
default 0.25 seconds). HTTP 429 and transient 5xx responses are retried with
exponential backoff and jitter; `Retry-After` is honored with a 30-second
per-retry cap. Redirects are followed. If ESPN fails after retries and an
expired cached copy (up to one hour old) exists, the worker serves it rather
than failing the query and marks it uncacheable for clients. The freshness the
worker applied is forwarded to VGI clients as `Cache-Control`. Success timings
are logged at debug level; retries, stale responses and transport failures are
warnings.

Base URLs can be redirected for hermetic tests: `NCAAF_API_ROOT` maps every API
to `{root}/site`, `{root}/site_v3`, `{root}/standings`, `{root}/core` and
`{root}/common`, and `NCAAF_SITE_API_BASE_URL`, `NCAAF_SITE_V3_API_BASE_URL`,
`NCAAF_STANDINGS_API_BASE_URL`, `NCAAF_CORE_API_BASE_URL` and
`NCAAF_COMMON_API_BASE_URL` override them one at a time. Do not set these from
untrusted user input.

## Development and release checks

```bash
uv lock --check --no-sources
uv sync --no-sources
uv run --no-sources ruff check .
uv run --no-sources ruff format --check .
uv run --no-sources pytest --cov=vgi_ncaa_football --cov-fail-under=80
uv build
uvx twine check dist/*
uvx --no-sources --from vgi-lint-check==2.2.0 vgi-lint lint \
  'uv run --no-sources ncaa_football_worker.py' \
  --no-execute --no-check-links --audit-waivers --fail-on warning
```

The offline suite replays trimmed real ESPN responses from `tests/fixtures`,
including a hermetic Haybarn end-to-end test that serves them from a local HTTP
server. Live upstream smoke tests are opt-in with `uv run pytest -m live` and
also run in a serialized scheduled workflow alongside executable VGI catalog
examples. Publishing is driven by releases. Create a GitHub release named `vX.Y.Z` that
matches the version in `pyproject.toml`. CI then builds, validates and attests
the wheel and sdist, attaches them to the release, and publishes a
multi-platform container image to `ghcr.io/query-farm/vgi-ncaa-football`. The package is
not published to PyPI; install it from the release tag as shown in the quick
start.

---

Copyright 2026 Query Farm LLC - https://query.farm
