# Changelog

All notable changes to this project are documented here.

## 0.2.2 (2026-11-16)

- Require vgi-python `>=0.52.2,<0.53`; uv.lock re-locked.
- Move to Python 3.14: `requires-python >=3.14`, CI, the Docker image and lint tooling target 3.14.
- CI runs the SQL tests through the starter's `ci/run-integration.sh` over stdio, launch and HTTP.

## [Unreleased]

## [0.2.1]

- Docs link to ESPN College Football and ESPN's terms: the catalog's
  documentation gains a Sources and references section (vgi-lint VGI183).
- The catalog documentation gives an overview of the worker instead of listing its
  functions (vgi-lint VGI173); per-function guidance lives on each function's own docs.

## [0.2.0]

- Port to VGI protocol 3.3 (vgi-python 0.51). Requires the current VGI DuckDB
  extension; 3.2 extensions are incompatible. No SQL surface changes.

## [0.1.0]

- Initial release on VGI protocol 3.2 (vgi-python 0.50) with vgi-lint 2.2.0.
- Current tables: `current_scoreboard`, `current_standings`, `current_rankings`
  (including the CFP committee ranking) and `current_stat_leaders`.
- Functions for weekly FBS/FCS/conference scoreboards, team schedules, standings,
  team and conference directories, historical AP/Coaches/CFP/FCS polls, rosters,
  team season statistics, game summaries, team and player box scores, drives,
  play-by-play, national leaders, player game logs and career season lines.
- Polite ESPN client: honest User-Agent, retries with `Retry-After`, request
  pacing, bounded concurrency, in-process caching and stale-on-error fallback.
- Hermetic Haybarn end-to-end tests over trimmed real ESPN fixtures, opt-in live
  tests, and an agent-test sidecar graded by `check_sql`.
