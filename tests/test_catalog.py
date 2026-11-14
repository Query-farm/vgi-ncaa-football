"""Tests for the SQL-facing catalog shape and discovery metadata."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vgi_ncaa_football.composed import COMPOSED_EXAMPLES
from vgi_ncaa_football.games import CurrentScoreboardFunction, ScoreboardFunction
from vgi_ncaa_football.league import CurrentRankingsFunction, CurrentStandingsFunction
from vgi_ncaa_football.players import CurrentStatLeadersFunction
from vgi_ncaa_football.worker import AGENT_TASKS, NCAA_FOOTBALL_CATALOG

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = NCAA_FOOTBALL_CATALOG.schemas[0]


def test_catalog_surface() -> None:
    assert NCAA_FOOTBALL_CATALOG.name == "ncaa_football"
    assert SCHEMA.path == ["main"]
    assert {function.Meta.name for function in SCHEMA.functions} == {
        "scoreboard",
        "all_current_scoreboard",
        "team_schedule",
        "game_summary",
        "box_score_team",
        "box_score_player",
        "drives",
        "play_by_play",
        "standings",
        "all_current_standings",
        "teams",
        "conferences",
        "rankings",
        "all_current_rankings",
        "roster",
        "team_season_stats",
        "stat_leaders",
        "all_current_stat_leaders",
        "player_game_log",
        "player_season_stats",
    }
    assert {table.name: table.function for table in SCHEMA.tables} == {
        "current_scoreboard": CurrentScoreboardFunction,
        "current_standings": CurrentStandingsFunction,
        "current_rankings": CurrentRankingsFunction,
        "current_stat_leaders": CurrentStatLeadersFunction,
    }


def test_scoreboard_arguments_are_named_and_optional() -> None:
    parameters = {parameter.name: parameter for parameter in ScoreboardFunction.get_metadata().parameters}
    assert set(parameters) == {"season", "week", "season_type", "division", "conference_id"}
    assert all(not parameter.required for parameter in parameters.values())


def test_argument_names_avoid_duckdb_keywords() -> None:
    reserved = {"end", "from", "interval", "order", "group", "limit", "type", "select"}
    for function in SCHEMA.functions:
        for parameter in function.get_metadata().parameters:
            assert parameter.name not in reserved, (function.Meta.name, parameter.name)


def _key_of(table, kind: str) -> list[tuple[str, ...]]:
    return [
        tuple(name for path in record.definition.column_paths for name in path)
        for record in table.constraints
        if record.definition.kind == kind
    ]


@pytest.mark.parametrize(
    ("table", "key"),
    [
        ("current_scoreboard", ("game_id",)),
        ("current_standings", ("team_id",)),
        ("current_rankings", ("poll_id", "team_id")),
        ("current_stat_leaders", ("category", "player_id")),
    ],
)
def test_table_keys(table: str, key: tuple[str, ...]) -> None:
    found = next(value for value in SCHEMA.tables if value.name == table)
    assert _key_of(found, "PRIMARY_KEY") == [key]
    assert _key_of(found, "NOT_NULL") == [(column,) for column in key]


def test_every_function_carries_discovery_tags() -> None:
    for function in SCHEMA.functions:
        tags = function.Meta.tags
        for name in ("vgi.title", "vgi.doc_llm", "vgi.doc_md", "vgi.result_columns_schema"):
            assert tags.get(name), (function.Meta.name, name)
        examples = json.loads(tags["vgi.example_queries"])
        assert examples and all(f"ncaa_football.main.{function.Meta.name}" in ex["sql"] for ex in examples)
    for table in SCHEMA.tables:
        for name in ("vgi.title", "vgi.subtitle", "vgi.doc_llm", "vgi.doc_md", "vgi.keywords"):
            if name == "vgi.subtitle":
                continue
            assert table.tags.get(name), (table.name, name)
    for name in ("vgi.title", "vgi.subtitle", "vgi.doc_llm", "vgi.doc_md", "vgi.keywords", "vgi.copyright"):
        assert NCAA_FOOTBALL_CATALOG.tags.get(name), name


def test_discovery_contract_is_machine_readable() -> None:
    examples = json.loads(NCAA_FOOTBALL_CATALOG.tags["vgi.executable_examples"])
    tasks = json.loads(NCAA_FOOTBALL_CATALOG.tags["vgi.agent_test_tasks"])
    assert all({"name", "description", "sql", "expected_result"} <= example.keys() for example in examples)
    assert tasks == AGENT_TASKS
    assert 6 <= len(tasks) <= 12
    assert all(set(task) == {"name", "prompt"} for task in tasks)
    assert len({task["name"] for task in tasks}) == len(tasks)


def test_agent_sidecar_grades_every_task_with_one_sql_oracle() -> None:
    text = (ROOT / "vgi-agent-tests.yaml").read_text()
    names = [line.split(":", 1)[1].strip() for line in text.splitlines() if line.startswith("  - name:")]
    assert names == [task["name"] for task in AGENT_TASKS]
    blocks = text.split("\n  - name: ")[1:]
    prompts = {task["name"]: task["prompt"] for task in AGENT_TASKS}
    for block in blocks:
        name = block.split("\n", 1)[0].strip()
        oracles = [key for key in ("check_sql:", "reference_sql:") if key in block]
        assert len(oracles) == 1, (name, oracles)
        if oracles == ["reference_sql:"]:
            # Exact-compare grading is only fair when the prompt pins the output columns.
            assert "column named" in prompts[name], name
    referenced = {function.Meta.name for function in SCHEMA.functions if f".{function.Meta.name}(" in text}
    referenced |= {table.name for table in SCHEMA.tables if f".{table.name}" in text}
    public = {f.Meta.name for f in SCHEMA.functions if not f.Meta.name.startswith("all_")}
    assert public | {table.name for table in SCHEMA.tables} <= referenced


def _example_sql(function) -> list[str]:
    return [ex["sql"] for ex in json.loads(function.Meta.tags["vgi.example_queries"])]


def test_composed_examples_reach_schema_and_driving_function() -> None:
    schema_sql = {ex["sql"] for ex in json.loads(SCHEMA.tags["vgi.example_queries"])}
    for description, sql in COMPOSED_EXAMPLES:
        assert sql in schema_sql, description
        functions = [f for f in SCHEMA.functions if f"ncaa_football.main.{f.Meta.name}(" in sql]
        assert len(functions) >= 2, description
        # The agent simulator shows only an object's first five examples.
        driving = [f for f in functions if sql in _example_sql(f)[:5]]
        assert len(driving) == 1, description
        assert sql in {ex.sql for ex in driving[0].Meta.examples}, description
