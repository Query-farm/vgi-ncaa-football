"""Tests for the ESPN HTTP boundary: URLs, retries, caching and error handling."""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from tests.conftest import MockEspn
from vgi_ncaa_football import __version__, api


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_scoreboard_query_shape_and_honest_user_agent() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"events": []}, headers={"Cache-Control": "max-age=1"})

    with _client(handler) as client:
        result = api.fetch_scoreboard(group=80, season=2025, week=3, season_type="regular", client=client)

    request = seen[0]
    assert request.url.path == "/site/scoreboard"
    assert dict(request.url.params) == {
        "dates": "2025",
        "groups": "80",
        "limit": str(api.SCOREBOARD_LIMIT),
        "seasontype": "2",
        "week": "3",
    }
    assert request.headers["user-agent"] == (
        f"vgi-ncaa-football/{__version__} (+https://github.com/Query-farm/vgi-ncaa-football)"
    )
    # The live-data floor wins over ESPN's one-second max-age; past seasons cache for hours.
    assert result.ttl == api.HISTORICAL_TTL


def test_specific_base_url_overrides_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NCAAF_CORE_API_BASE_URL", "https://core.example/v2/")
    assert api.base_url("core") == "https://core.example/v2"
    assert api.base_url("site") == "https://espn.test/site"
    monkeypatch.delenv(api.ROOT_ENV)
    assert api.base_url("site") == api.DEFAULT_BASE_URLS["site"]


def test_responses_are_cached_in_process() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"rankings": []}, headers={"Cache-Control": "max-age=600"})

    with _client(handler) as client:
        first = api.fetch_site_rankings(client=client)
        second = api.fetch_site_rankings(client=client)

    assert calls == 1
    assert first.ttl == 600
    assert 1 <= second.ttl <= 600
    api.clear_cache()
    with _client(handler) as client:
        api.fetch_site_rankings(client=client)
    assert calls == 2


def test_retries_429_with_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(api.time, "sleep", sleeps.append)
    statuses = iter([429, 503, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        status = next(statuses)
        if status == 429:
            return httpx.Response(429, headers={"Retry-After": "2"})
        if status == 503:
            return httpx.Response(503)
        return httpx.Response(200, json={"events": []})

    with _client(handler) as client:
        api.fetch_scoreboard(group=81, client=client)

    assert sleeps[0] == 2.0
    assert len(sleeps) == 2


def test_retry_after_http_date_and_backoff_bounds() -> None:
    response = httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"})
    assert 0.25 <= api._retry_delay(response, 0) <= 0.3125  # a past date falls back to backoff
    future = httpx.Response(429, headers={"Retry-After": "Fri, 01 Jan 2100 00:00:00 GMT"})
    assert api._retry_delay(future, 0) == api.MAX_RETRY_DELAY
    assert api._retry_delay(httpx.Response(429, headers={"Retry-After": "999"}), 0) == api.MAX_RETRY_DELAY
    assert 0.25 <= api._retry_delay(httpx.Response(503, headers={"Retry-After": "soon"}), 0) <= 0.3125
    assert api._retry_delay(None, 20) == api.MAX_RETRY_DELAY


def test_gives_up_after_max_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api.time, "sleep", lambda _: None)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(502, text="bad gateway")

    with _client(handler) as client, pytest.raises(api.EspnApiError, match="HTTP 502"):
        api.fetch_summary(1, client=client)
    assert calls == api.MAX_ATTEMPTS


def test_transport_errors_retry_then_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api.time, "sleep", lambda _: None)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with _client(handler) as client, pytest.raises(httpx.ConnectError):
        api.fetch_leaders(client=client)


def test_serves_stale_cache_when_upstream_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api.time, "sleep", lambda _: None)
    healthy = True

    def handler(request: httpx.Request) -> httpx.Response:
        if healthy:
            return httpx.Response(200, json={"rankings": [{"id": "1"}]})
        return httpx.Response(503)

    with _client(handler) as client:
        api.fetch_site_rankings(client=client)
    # Expire the entry without waiting.
    for entry in api._cache.values():
        entry.stored_at -= api.LIVE_TTL + 1
    healthy = False
    with _client(handler) as client:
        stale = api.fetch_site_rankings(client=client)
    assert stale.payload["rankings"] == [{"id": "1"}]
    assert stale.ttl == 0


def test_serves_stale_cache_on_transport_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api.time, "sleep", lambda _: None)
    api._cache_put("https://espn.test/site_v3/leaders?", {"leaders": {"categories": []}}, 1)
    for entry in api._cache.values():
        entry.stored_at -= 5

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with _client(handler) as client:
        assert api.fetch_leaders(client=client).ttl == 0


def test_cache_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "CACHE_MAX_ENTRIES", 2)
    for index in range(4):
        api._cache_put(f"key{index}", {}, 60)
    assert list(api._cache) == ["key2", "key3"]


def test_errors_are_descriptive() -> None:
    with (
        _client(lambda request: httpx.Response(400, text="bad season")) as client,
        pytest.raises(api.EspnApiError, match="HTTP 400 for standings.*bad season"),
    ):
        api.fetch_standings(group=80, client=client)


def test_rejects_non_json_and_malformed_documents() -> None:
    with _client(lambda request: httpx.Response(200, text="<html>")) as client, pytest.raises(ValueError):
        api.fetch_summary(5, client=client)
    with _client(lambda request: httpx.Response(200, json=[1])) as client, pytest.raises(ValueError):
        api.fetch_summary(6, client=client)
    malformed = {
        "fetch_scoreboard": {"group": 80},
        "fetch_standings": {"group": 80},
        "fetch_site_rankings": {},
        "fetch_summary": {"game_id": 7},
        "fetch_leaders": {},
        "fetch_roster": {"team_id": 3},
        "fetch_team_statistics": {"team_id": 3, "season": 2025},
        "fetch_team_schedule": {"team_id": 3, "season": 2025, "season_type": "regular"},
    }
    for name, kwargs in malformed.items():
        api.clear_cache()
        with _client(lambda request: httpx.Response(200, json={})) as client, pytest.raises(ValueError):
            getattr(api, name)(**kwargs, client=client)


def test_argument_validation() -> None:
    with pytest.raises(ValueError, match="season_type"):
        api.fetch_scoreboard(group=80, season_type="bowl")
    for call in (
        lambda: api.fetch_summary(0),
        lambda: api.fetch_roster(-1),
        lambda: api.fetch_player_game_log(0),
        lambda: api.fetch_player_season_stats(0),
        lambda: api.fetch_team_statistics(0, season=None),
        lambda: api.fetch_team_schedule(0, season=None, season_type="regular"),
        lambda: api.fetch_core_poll_ids(season=2025, season_type="regular", week=0),
    ):
        with pytest.raises(ValueError, match="must be positive"):
            call()


def test_core_polls_missing_and_malformed(espn: MockEspn) -> None:
    assert api.fetch_core_poll_ids(season=2025, season_type="regular", week=15) == [21, 1, 2]
    assert api.fetch_core_poll_ids(season=1999, season_type="postseason", week=1) == []
    assert api.fetch_core_poll(season=2025, season_type="regular", week=15, poll_id=99) is None
    with (
        _client(lambda request: httpx.Response(200, json={"items": None})) as client,
        pytest.raises(ValueError),
    ):
        api.fetch_core_poll_ids(season=2020, season_type="regular", week=2, client=client)
    with _client(lambda request: httpx.Response(200, json={"id": "1"})) as client, pytest.raises(ValueError):
        api.fetch_core_poll(season=2020, season_type=2, week=2, poll_id=1, client=client)


def test_season_helpers() -> None:
    assert api.current_season(date(2026, 1, 15)) == 2025
    assert api.current_season(date(2026, 2, 1)) == 2026
    assert api.ttl_for_season(None) == api.LIVE_TTL
    assert api.ttl_for_season(2000) == api.HISTORICAL_TTL
    assert api.ttl_for_season(api.current_season()) == api.LIVE_TTL
    ref = "http://sports.core.api.espn.com/v2/sports/football/leagues/college-football/seasons/2025/teams/84?x=1"
    assert api.ref_id(ref, "teams") == 84
    assert api.ref_id(ref, "athletes") is None
    assert api.ref_id(None, "teams") is None


def test_min_request_interval_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NCAAF_MIN_REQUEST_INTERVAL", "not-a-number")
    assert api._min_request_interval() == api.DEFAULT_MIN_REQUEST_INTERVAL
    monkeypatch.setenv("NCAAF_MIN_REQUEST_INTERVAL", "-3")
    assert api._min_request_interval() == 0.0
    monkeypatch.delenv("NCAAF_MIN_REQUEST_INTERVAL")
    assert api._min_request_interval() == api.DEFAULT_MIN_REQUEST_INTERVAL


def test_pacing_spaces_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setenv("NCAAF_MIN_REQUEST_INTERVAL", "10")
    monkeypatch.setattr(api.time, "sleep", sleeps.append)
    api._pace()
    api._pace()
    assert sleeps and 0 < sleeps[-1] <= 10


def test_follows_redirects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/site/teams":
            return httpx.Response(307, headers={"Location": "https://espn.test/site/teams-moved"})
        return httpx.Response(200, json={"sports": []})

    with _client(handler) as client:
        assert api.fetch_team_directory(client=client).payload == {"sports": []}


def test_default_client_is_created_when_none_given(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict] = []

    class FakeClient(httpx.Client):
        def __init__(self, **kwargs) -> None:
            created.append(kwargs)
            super().__init__(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"items": []})))

    monkeypatch.setattr(api.httpx, "Client", FakeClient)
    assert api.fetch_core_poll_ids(season=2024, season_type="regular", week=3) == []
    assert created[0]["follow_redirects"] is True
