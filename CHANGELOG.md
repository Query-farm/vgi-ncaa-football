# Changelog

All notable changes to this project are documented here.

## [Unreleased]

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
