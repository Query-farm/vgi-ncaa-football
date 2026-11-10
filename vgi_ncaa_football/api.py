"""Small, read-only, caching client for ESPN's public college-football JSON feeds.

Every outbound request in the package goes through :func:`fetch_json`. It adds an
honest User-Agent, follows redirects, retries 429/5xx responses with bounded
backoff (honoring ``Retry-After``), paces requests politely, and keeps an
in-process response cache so repeated SQL scans do not re-hit ESPN.
"""

from __future__ import annotations

import logging
import os
import random
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlencode

import httpx

from vgi_ncaa_football import __version__

USER_AGENT = f"vgi-ncaa-football/{__version__} (+https://github.com/Query-farm/vgi-ncaa-football)"

# ESPN serves college football from four related, undocumented hosts.
DEFAULT_BASE_URLS: dict[str, str] = {
    "site": "https://site.api.espn.com/apis/site/v2/sports/football/college-football",
    "site_v3": "https://site.api.espn.com/apis/site/v3/sports/football/college-football",
    "standings": "https://site.api.espn.com/apis/v2/sports/football/college-football",
    "core": "https://sports.core.api.espn.com/v2/sports/football/leagues/college-football",
    "common": "https://site.web.api.espn.com/apis/common/v3/sports/football/college-football",
}
BASE_URL_ENV: dict[str, str] = {
    "site": "NCAAF_SITE_API_BASE_URL",
    "site_v3": "NCAAF_SITE_V3_API_BASE_URL",
    "standings": "NCAAF_STANDINGS_API_BASE_URL",
    "core": "NCAAF_CORE_API_BASE_URL",
    "common": "NCAAF_COMMON_API_BASE_URL",
}
# One root that maps every API to ``{root}/{api}``; used by hermetic tests.
ROOT_ENV = "NCAAF_API_ROOT"

TIMEOUT = httpx.Timeout(30.0, connect=10.0)
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 4
MAX_RETRY_DELAY = 30.0
QUERY_TIMEOUT = 120.0
MAX_CONCURRENT_REQUESTS = 4
DEFAULT_MIN_REQUEST_INTERVAL = 0.25

LIVE_TTL = 60
REFERENCE_TTL = 3_600
HISTORICAL_TTL = 6 * 3_600
STALE_IF_ERROR = 3_600
CACHE_MAX_ENTRIES = 256

SEASON_TYPES = {"regular": 2, "postseason": 3}
SEASON_TYPE_NAMES = {value: key for key, value in SEASON_TYPES.items()}
DIVISION_GROUPS = {"fbs": 80, "fcs": 81}
# ESPN silently falls back to its 25-event default when ``limit`` is too large.
SCOREBOARD_LIMIT = 300

logger = logging.getLogger(__name__)
_request_slots = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)
_pace_lock = threading.Lock()
_last_request_at = 0.0
_cache_lock = threading.Lock()
_cache: OrderedDict[str, _CacheEntry] = OrderedDict()

_MAX_AGE = re.compile(r"(?:^|[,\s])max-age=(\d+)", re.IGNORECASE)
_REF_ID = re.compile(r"/(rankings|teams|athletes|events)/(\d+)")


class EspnApiError(RuntimeError):
    """A failed response from an ESPN college-football endpoint."""

    def __init__(self, status: int, path: str, body: str) -> None:
        super().__init__(f"ESPN college-football API returned HTTP {status} for {path}: {body[:300]}")
        self.status = status


@dataclass(frozen=True, slots=True)
class ApiResponse:
    """A decoded JSON object plus the freshness the worker applied to it."""

    payload: dict[str, Any]
    ttl: int


@dataclass(slots=True)
class _CacheEntry:
    payload: dict[str, Any]
    ttl: int
    stored_at: float


def base_url(api: str) -> str:
    """Return the root URL for one ESPN API, honoring test overrides."""
    specific = os.environ.get(BASE_URL_ENV[api])
    if specific:
        return specific.rstrip("/")
    root = os.environ.get(ROOT_ENV)
    if root:
        return f"{root.rstrip('/')}/{api}"
    return DEFAULT_BASE_URLS[api]


def current_season(today: date | None = None) -> int:
    """Return the season ESPN treats as current; college seasons roll over on 1 February."""
    today = today or datetime.now(UTC).date()
    return today.year if today.month >= 2 else today.year - 1


def ttl_for_season(season: int | None) -> int:
    """Cache completed seasons for hours and the live season for one minute."""
    if season is not None and season < current_season():
        return HISTORICAL_TTL
    return LIVE_TTL


def ref_id(ref: Any, kind: str) -> int | None:
    """Extract the numeric id for ``kind`` from an ESPN ``$ref`` URL."""
    if not isinstance(ref, str):
        return None
    for name, value in _REF_ID.findall(ref):
        if name == kind:
            return int(value)
    return None


def clear_cache() -> None:
    """Drop every cached response (used by tests and long-lived operators)."""
    with _cache_lock:
        _cache.clear()


def _min_request_interval() -> float:
    raw = os.environ.get("NCAAF_MIN_REQUEST_INTERVAL")
    if raw is None:
        return DEFAULT_MIN_REQUEST_INTERVAL
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_MIN_REQUEST_INTERVAL


def _pace() -> None:
    """Space outbound requests so one process stays a polite client."""
    global _last_request_at
    interval = _min_request_interval()
    with _pace_lock:
        wait = _last_request_at + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


@contextmanager
def _client(client: httpx.Client | None) -> Iterator[httpx.Client]:
    """Reuse one connection pool for every request in a logical API fetch."""
    if client is not None:
        yield client
        return
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as owned:
        yield owned


def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
    """Return a bounded Retry-After delay or exponential backoff with jitter."""
    if response is not None:
        raw = response.headers.get("retry-after")
        if raw:
            try:
                delay = float(raw)
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(raw)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=UTC)
                    delay = (retry_at - datetime.now(UTC)).total_seconds()
                except (TypeError, ValueError, OverflowError):
                    delay = -1.0
            if delay >= 0:
                return min(delay, MAX_RETRY_DELAY)
    base = min(0.25 * 2**attempt, MAX_RETRY_DELAY)
    return min(base + random.uniform(0, base * 0.25), MAX_RETRY_DELAY)


def _request(
    url: str, params: Mapping[str, str | int], *, client: httpx.Client, deadline: float
) -> httpx.Response:
    """Issue one GET, retrying transient failures until the query deadline."""
    for attempt in range(MAX_ATTEMPTS):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"ESPN college-football query exceeded {QUERY_TIMEOUT:g} seconds")
        started = time.monotonic()
        try:
            if not _request_slots.acquire(timeout=remaining):
                raise TimeoutError(f"ESPN college-football query exceeded {QUERY_TIMEOUT:g} seconds")
            try:
                _pace()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"ESPN college-football query exceeded {QUERY_TIMEOUT:g} seconds")
                response = client.get(
                    url,
                    params=params,
                    headers={"Accept": "application/json", "User-Agent": USER_AGENT},
                    timeout=httpx.Timeout(min(30.0, remaining), connect=min(10.0, remaining)),
                    follow_redirects=True,
                )
            finally:
                _request_slots.release()
        except httpx.TransportError:
            logger.warning(
                "espn_transport_error url=%s attempt=%d duration_ms=%d",
                url,
                attempt + 1,
                round((time.monotonic() - started) * 1_000),
            )
            if attempt + 1 == MAX_ATTEMPTS:
                raise
            time.sleep(min(_retry_delay(None, attempt), max(0.0, deadline - time.monotonic())))
            continue
        logger.debug(
            "espn_response url=%s status=%d attempt=%d duration_ms=%d",
            url,
            response.status_code,
            attempt + 1,
            round((time.monotonic() - started) * 1_000),
        )
        if response.status_code not in RETRYABLE_STATUSES or attempt + 1 == MAX_ATTEMPTS:
            return response
        delay = min(_retry_delay(response, attempt), max(0.0, deadline - time.monotonic()))
        logger.warning(
            "espn_retry url=%s status=%d attempt=%d delay_seconds=%.3f",
            url,
            response.status_code,
            attempt + 1,
            delay,
        )
        time.sleep(delay)
    raise AssertionError("retry loop exited without a response")


def _cache_get(key: str, *, allow_stale: bool = False) -> _CacheEntry | None:
    now = time.monotonic()
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        age = now - entry.stored_at
        limit = entry.ttl + (STALE_IF_ERROR if allow_stale else 0)
        if age > limit:
            if age > entry.ttl + STALE_IF_ERROR:
                del _cache[key]
            return None
        _cache.move_to_end(key)
        return entry


def _cache_put(key: str, payload: dict[str, Any], ttl: int) -> None:
    with _cache_lock:
        _cache[key] = _CacheEntry(payload=payload, ttl=ttl, stored_at=time.monotonic())
        _cache.move_to_end(key)
        while len(_cache) > CACHE_MAX_ENTRIES:
            _cache.popitem(last=False)


def fetch_json(
    api: str,
    path: str,
    params: Mapping[str, str | int] | None = None,
    *,
    ttl: int = LIVE_TTL,
    client: httpx.Client | None = None,
    missing_ok: bool = False,
) -> ApiResponse | None:
    """Fetch one ESPN JSON object, serving it from the in-process cache when fresh.

    ``ttl`` is the minimum freshness window; a longer origin ``max-age`` wins.
    With ``missing_ok`` a 404 returns ``None`` instead of raising.
    """
    query = {key: params[key] for key in sorted(params or {})}
    url = f"{base_url(api)}/{path.lstrip('/')}"
    key = f"{url}?{urlencode(query)}"
    cached = _cache_get(key)
    if cached is not None:
        remaining = cached.ttl - (time.monotonic() - cached.stored_at)
        return ApiResponse(payload=cached.payload, ttl=max(1, int(remaining)))

    deadline = time.monotonic() + QUERY_TIMEOUT
    try:
        with _client(client) as http:
            response = _request(url, query, client=http, deadline=deadline)
    except (httpx.TransportError, TimeoutError):
        stale = _cache_get(key, allow_stale=True)
        if stale is None:
            raise
        logger.warning("espn_serving_stale url=%s reason=transport", url)
        return ApiResponse(payload=stale.payload, ttl=0)

    if response.status_code == 404 and missing_ok:
        return None
    if not response.is_success:
        stale = _cache_get(key, allow_stale=True) if response.status_code in RETRYABLE_STATUSES else None
        if stale is not None:
            logger.warning("espn_serving_stale url=%s status=%d", url, response.status_code)
            return ApiResponse(payload=stale.payload, ttl=0)
        raise EspnApiError(response.status_code, path, response.text)
    try:
        payload = response.json()
    except ValueError as error:
        raise ValueError(f"ESPN college-football API returned non-JSON content for {path}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"ESPN college-football API returned a non-object JSON document for {path}")

    match = _MAX_AGE.search(response.headers.get("cache-control", ""))
    effective_ttl = max(ttl, int(match.group(1)) if match else 0)
    _cache_put(key, payload, effective_ttl)
    return ApiResponse(payload=payload, ttl=effective_ttl)


def _require(response: ApiResponse | None) -> ApiResponse:
    if response is None:  # pragma: no cover - only reachable with missing_ok
        raise AssertionError("missing response")
    return response


def _check_season_type(season_type: str) -> int:
    if season_type not in SEASON_TYPES:
        raise ValueError("season_type must be 'regular' or 'postseason'")
    return SEASON_TYPES[season_type]


def _check_positive(name: str, value: int) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be positive")


# --------------------------------------------------------------------------- endpoints


def fetch_scoreboard(
    *,
    group: int,
    season: int | None = None,
    week: int | None = None,
    season_type: str | None = None,
    client: httpx.Client | None = None,
) -> ApiResponse:
    """Fetch one week's scoreboard for a division (80/81) or conference group id."""
    params: dict[str, str | int] = {"groups": group, "limit": SCOREBOARD_LIMIT}
    if season is not None:
        params["dates"] = season
    if season_type is not None:
        params["seasontype"] = _check_season_type(season_type)
    if week is not None:
        params["week"] = week
    response = _require(fetch_json("site", "scoreboard", params, ttl=ttl_for_season(season), client=client))
    if not isinstance(response.payload.get("events"), list):
        raise ValueError("ESPN scoreboard response has no events array")
    return response


def fetch_standings(
    *, group: int, season: int | None = None, client: httpx.Client | None = None
) -> ApiResponse:
    """Fetch the conference standings tree for FBS (80) or FCS (81)."""
    params: dict[str, str | int] = {"group": group}
    if season is not None:
        params["season"] = season
    response = _require(
        fetch_json("standings", "standings", params, ttl=ttl_for_season(season), client=client)
    )
    if not isinstance(response.payload.get("children"), list):
        raise ValueError("ESPN standings response has no conference children")
    return response


def fetch_site_rankings(*, client: httpx.Client | None = None) -> ApiResponse:
    """Fetch the latest published polls with hydrated teams."""
    response = _require(fetch_json("site", "rankings", ttl=LIVE_TTL, client=client))
    if not isinstance(response.payload.get("rankings"), list):
        raise ValueError("ESPN rankings response has no rankings array")
    return response


def fetch_core_poll_ids(
    *, season: int, season_type: str, week: int, client: httpx.Client | None = None
) -> list[int]:
    """List the poll ids ESPN published for one season week."""
    _check_positive("week", week)
    path = f"seasons/{season}/types/{_check_season_type(season_type)}/weeks/{week}/rankings"
    response = fetch_json("core", path, ttl=ttl_for_season(season), client=client, missing_ok=True)
    if response is None:
        return []
    items = response.payload.get("items")
    if not isinstance(items, list):
        raise ValueError("ESPN core rankings index has no items array")
    ids = [ref_id(item.get("$ref"), "rankings") for item in items if isinstance(item, Mapping)]
    return [value for value in ids if value is not None]


def fetch_core_poll(
    *, season: int, season_type: str | int, week: int, poll_id: int, client: httpx.Client | None = None
) -> ApiResponse | None:
    """Fetch one poll for one week; ``None`` when ESPN has no such poll."""
    type_id = season_type if isinstance(season_type, int) else _check_season_type(season_type)
    path = f"seasons/{season}/types/{type_id}/weeks/{week}/rankings/{poll_id}"
    response = fetch_json("core", path, ttl=ttl_for_season(season), client=client, missing_ok=True)
    if response is not None and not isinstance(response.payload.get("ranks"), list):
        raise ValueError("ESPN core poll response has no ranks array")
    return response


def fetch_team_directory(*, client: httpx.Client | None = None) -> ApiResponse:
    """Fetch ESPN's full college-football team directory (all divisions)."""
    return _require(fetch_json("site", "teams", {"limit": 1000}, ttl=REFERENCE_TTL, client=client))


def fetch_team_schedule(
    team_id: int, *, season: int | None, season_type: str, client: httpx.Client | None = None
) -> ApiResponse:
    """Fetch one team's regular-season or postseason schedule."""
    _check_positive("team_id", team_id)
    params: dict[str, str | int] = {"seasontype": _check_season_type(season_type)}
    if season is not None:
        params["season"] = season
    response = _require(
        fetch_json("site", f"teams/{team_id}/schedule", params, ttl=ttl_for_season(season), client=client)
    )
    if not isinstance(response.payload.get("events"), list):
        raise ValueError("ESPN team schedule response has no events array")
    return response


def fetch_roster(team_id: int, *, client: httpx.Client | None = None) -> ApiResponse:
    """Fetch one team's current roster (ESPN returns empty historical rosters)."""
    _check_positive("team_id", team_id)
    response = _require(fetch_json("site", f"teams/{team_id}/roster", ttl=REFERENCE_TTL, client=client))
    if not isinstance(response.payload.get("athletes"), list):
        raise ValueError("ESPN roster response has no athletes array")
    return response


def fetch_team_statistics(
    team_id: int, *, season: int | None, client: httpx.Client | None = None
) -> ApiResponse:
    """Fetch one team's season statistics and its opponents' totals."""
    _check_positive("team_id", team_id)
    params: dict[str, str | int] = {}
    if season is not None:
        params["season"] = season
    response = _require(
        fetch_json("site", f"teams/{team_id}/statistics", params, ttl=ttl_for_season(season), client=client)
    )
    if not isinstance(response.payload.get("results"), Mapping):
        raise ValueError("ESPN team statistics response has no results object")
    return response


def fetch_summary(game_id: int, *, client: httpx.Client | None = None) -> ApiResponse:
    """Fetch one game's summary: header, box score, drives and plays."""
    _check_positive("game_id", game_id)
    response = _require(fetch_json("site", "summary", {"event": game_id}, ttl=LIVE_TTL, client=client))
    if not isinstance(response.payload.get("header"), Mapping):
        raise ValueError("ESPN summary response has no header")
    return response


def fetch_leaders(*, season: int | None = None, client: httpx.Client | None = None) -> ApiResponse:
    """Fetch ESPN's national top-ten leaderboards for one season."""
    params: dict[str, str | int] = {}
    if season is not None:
        params["season"] = season
    response = _require(fetch_json("site_v3", "leaders", params, ttl=ttl_for_season(season), client=client))
    leaders = response.payload.get("leaders")
    if not isinstance(leaders, Mapping) or not isinstance(leaders.get("categories"), list):
        raise ValueError("ESPN leaders response has no categories array")
    return response


def fetch_player_game_log(
    player_id: int, *, season: int | None = None, client: httpx.Client | None = None
) -> ApiResponse:
    """Fetch one player's per-game statistics for a season."""
    _check_positive("player_id", player_id)
    params: dict[str, str | int] = {}
    if season is not None:
        params["season"] = season
    return _require(
        fetch_json(
            "common", f"athletes/{player_id}/gamelog", params, ttl=ttl_for_season(season), client=client
        )
    )


def fetch_player_season_stats(player_id: int, *, client: httpx.Client | None = None) -> ApiResponse:
    """Fetch one player's season-by-season statistical lines."""
    _check_positive("player_id", player_id)
    return _require(fetch_json("common", f"athletes/{player_id}/stats", ttl=REFERENCE_TTL, client=client))
