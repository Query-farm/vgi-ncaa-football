# NCAA football drive notebook

[01 · A drive filter changes the denominator](01_drive_filters_change_the_denominator.ipynb) compares yards per recorded drive with yards per reported offensive play, and tests how requiring at least four plays changes the selected population. It uses ESPN game 401769076, Miami at Indiana, final score 21–27, kickoff **2026-01-20 00:30 UTC**, in the 2025 season. It makes no predictive or overall team-strength claim.

## Install and run from this directory

Requires Python 3.13–3.14, [uv](https://docs.astral.sh/uv/), Git, and internet for initial packages, Git worker and extension downloads. Replay serves a local recorded fixture through a loopback HTTP server.

```sh
cd notebooks
uv sync --locked
uv run --locked python execute.py
uv run --locked python -m ipykernel install --user --name vgi-ncaa-football-notebooks --display-name 'Python (vgi-ncaa-football notebooks)'
```

Select that kernel and Restart Kernel → Run All. The runner supplies its own fresh temporary kernel and uses this directory as cwd. The entire notebooks directory can be copied independently; no worker checkout or local binary is discovered.

The visible literal launch pins Git tag v0.2.0, revision `b8c975d14023556697d6f27ffedac8d6909bd61d`, with SDK 0.51.0, RPC 0.53.1, httpx 0.28.1, httpx2 2.16.0 and Arrow 25.0.1. Notebook Haybarn is locked to 2.0.0rc11. The worker is not on PyPI. Revalidate all modes and the experiment before upgrading pins.

## Experiment and live check

```sh
uv run --locked python execute.py --parameters '{"MIN_OFFENSIVE_PLAYS":4}' --output-dir validation/minimum-4
uv run --locked python execute.py --parameters '{"DATA_MODE":"live"}' --output-dir validation/live
```

Defaults are `DATA_MODE="replay"` and `MIN_OFFENSIVE_PLAYS=1`; the integer minimum accepts 1–10 inclusive. Live requests the same historical game, not current games, and has no fixture fallback. All NCAAF base-URL overrides are cleared in live mode and replaced with the loopback root in replay; request pacing is explicitly 0.25 seconds. Cleanup restores previous environment values. Each fresh execution starts a new worker process, so an old in-process stale cache is not reused between runs.

Overrides require a separate output destination; unknown names fail. The runner replaces a saved default only after a successful run, atomically. Experiment directories include fixture, helper, documentation, branding, runner and environment. Runtime audits, original response capture, extracted charts and validation copies are ignored; successful main outputs are embedded.

## Actual response and transparent projection

The source is ESPN's public summary endpoint:
`https://site.api.espn.com/apis/site/v2/sports/football/college-football/summary?event=401769076`.
`fixtures/manifest.json` records the exact capture interval on 2026-11-14 UTC, URL, status, original body hash/size and replay body hash/size.

The original 556,993-byte response contains game data plus unrelated editorial, video and betting sections. The published 91,632-byte fixture is explicitly a **field projection**, not an unmodified full response: complete `header` and `gameInfo` are retained, as are **all 22 `drives.previous` rows with every field except nested `plays`**. No drive rows, identifiers, values or timing fields are changed. Other top-level sections are removed. This is recorded factual data, not a synthetic substitute. No play-by-play, player/box-score or editorial query is claimed to work against this projected fixture.

`replay_source.py` verifies its SHA-256 and serves only `/site/summary?event=401769076`, with exact query matching. Unknown requests fail without forwarding. The real worker parses the header and drives. The notebook verifies every drive's ID, team, yards and play count against the fixture. The summary is materialized once per function; the worker cache lets the two functions share one exact replay request. No schedule fanout or SQL retrieval LIMIT is used; all selection and plots are local. Replay source time is distinct from execution time and historical kickoff. Live attempts are not instrumented and may retry.

ESPN's endpoints are undocumented and can change. The root README describes data terms and affiliation; the worker's MIT licence does not extend to upstream data. This example is not affiliated with ESPN, the NCAA or either school.

## Interpretation

The worker exposes completed `drives.previous`, so this notebook requires a final game. Negative yardage is kept. Missing yardage or a nonpositive play count cannot produce a rate; availability is shown separately. A short drive can reflect success, failure or clock management, so the illustrative minimum is not a quality rule.

These summaries use ESPN's reported drive yards and `offensivePlays`, not reconstructed play totals or official box-score yards per play. Drive result/scoring labels alone do not identify the team awarded points; the score comes from the header. Ratio-of-sums yards per play weights each drive by its reported plays; an arithmetic mean of drive rates gives each drive equal weight. Selected totals plus omitted totals reconcile exactly to the full measured pool. An empty selection has a known zero count and unavailable rates, not zero efficiency.

[Notebook standard](STANDARD.md) · [Query.Farm](https://query.farm/) · [VGI](https://query.farm/vgi/)
