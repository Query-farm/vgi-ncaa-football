"""Shared argument definitions, metadata tags, and emit helpers for table functions."""

from __future__ import annotations

import json
from collections.abc import Sequence

import pyarrow as pa
from vgi.arguments import Arg
from vgi.cache_control import CacheControl
from vgi_rpc.rpc import OutputCollector

from vgi_ncaa_football.meta import result_columns_schema
from vgi_ncaa_football.parsers import Row, batch_from_rows

PROVIDER = "espn"
DIVISIONS = ("fbs", "fcs", "all")
SEASON_TYPE_CHOICES = ("regular", "postseason")

SEASON_ARG = Arg(
    "season",
    default=None,
    arrow_type=pa.int32(),
    ge=1869,
    le=2100,
    doc="Season year, labelled by the calendar year it starts (2025 = the 2025-26 season). NULL = current.",
)


def division_arg(default: str) -> Arg:
    """Named ``division`` argument: fbs, fcs, or all."""
    return Arg(
        "division",
        default=default,
        arrow_type=pa.string(),
        choices=DIVISIONS,
        doc="NCAA Division I subdivision: fbs (Bowl Subdivision), fcs (Championship Subdivision), or all.",
    )


def id_arg(position: int, what: str) -> Arg:
    """Required positional ESPN identifier argument."""
    return Arg(position, arrow_type=pa.int64(), ge=1, doc=f"Stable ESPN {what} identifier.")


def cache_control(ttls: Sequence[int]) -> CacheControl | None:
    """Advertise the shortest freshness window among the responses used."""
    usable = [ttl for ttl in ttls if ttl > 0]
    if not usable or len(usable) != len(ttls):
        return None
    return CacheControl(ttl=min(usable))


def emit(out: OutputCollector, rows: Sequence[Row], schema: pa.Schema, ttls: Sequence[int]) -> None:
    """Emit one batch in the (possibly projected) output schema and finish."""
    out.emit(batch_from_rows(rows, schema), cache_control=cache_control(ttls))
    out.finish()


def tags(
    *,
    category: str,
    title: str,
    llm: str,
    md: str,
    schema: pa.Schema,
    examples: Sequence[tuple[str, str]],
) -> dict[str, str]:
    """Build the standard discovery tags for a table function."""
    return {
        "provider": PROVIDER,
        "vgi.category": category,
        "vgi.title": title,
        "vgi.result_columns_schema": result_columns_schema(schema),
        "vgi.doc_llm": llm,
        "vgi.doc_md": md,
        "vgi.example_queries": json.dumps([{"description": text, "sql": sql} for text, sql in examples]),
    }
