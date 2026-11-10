"""Shared fixtures: an isolated cache and a mock ESPN backed by recorded responses."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import httpx
import pyarrow as pa
import pytest

from tests import espn_fixtures
from vgi_ncaa_football import api

ROOT = "https://espn.test"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in api.BASE_URL_ENV.values():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(api.ROOT_ENV, ROOT)
    monkeypatch.setenv("NCAAF_MIN_REQUEST_INTERVAL", "0")
    api.clear_cache()
    yield
    api.clear_cache()


@dataclass
class MockEspn:
    """Records requests served from the fixture router."""

    requests: list[httpx.Request] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        content = espn_fixtures.body(path, dict(request.url.params))
        if content is None:
            return httpx.Response(404, json={"error": {"message": "no instance found", "code": 404}})
        return httpx.Response(
            200, content=content, headers={"Content-Type": "application/json", "Cache-Control": "max-age=5"}
        )

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]


@pytest.fixture
def espn(monkeypatch: pytest.MonkeyPatch) -> MockEspn:
    """Patch the API module so every request is answered from fixtures."""
    mock = MockEspn()

    @contextmanager
    def client(given: httpx.Client | None) -> Iterator[httpx.Client]:
        if given is not None:
            yield given
            return
        with httpx.Client(transport=httpx.MockTransport(mock.handler)) as http:
            yield http

    monkeypatch.setattr(api, "_client", client)
    return mock


class Collector:
    """Minimal stand-in for VGI's OutputCollector."""

    def __init__(self) -> None:
        self.batches: list[pa.RecordBatch] = []
        self.cache_controls: list[Any] = []
        self.finished = False

    def emit(self, batch: pa.RecordBatch, cache_control: Any = None) -> None:
        self.batches.append(batch)
        self.cache_controls.append(cache_control)

    def finish(self) -> None:
        self.finished = True

    def rows(self) -> list[dict[str, Any]]:
        return [row for batch in self.batches for row in batch.to_pylist()]


def run(function: Any, args: Any = None, schema: pa.Schema | None = None) -> Collector:
    """Invoke a table function's ``process`` in-process and collect its output."""
    out = Collector()
    params = SimpleNamespace(args=args, output_schema=schema or function.FIXED_SCHEMA)
    function.process(params, None, out)
    assert out.finished
    return out
