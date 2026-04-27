"""Cache layer tests — Upstash mocked, no live screener.in."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from bot.redis_store import RedisConfig, RedisStore
from core.data.screener_cache import ScreenerCache, snapshot_to_dict
from core.data.screener_in import ScreenerSnapshot


def _config() -> RedisConfig:
    return RedisConfig(url="https://mock", token="t", user_id_salt="s")


def _redis_response(result, *, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = {"result": result}
    resp.text = json.dumps(resp.json.return_value)
    return resp


def _redis_mock(responses: list[MagicMock]) -> MagicMock:
    http = MagicMock()

    def post(url, headers=None, json=None, timeout=None):
        return responses.pop(0)

    http.post.side_effect = post
    return http


def _snapshot(symbol: str = "X.NS", *, fetched_at: datetime | None = None) -> ScreenerSnapshot:
    return ScreenerSnapshot(
        symbol=symbol,
        market_cap=1.0e6,
        current_price=100.0,
        pe_ratio=20.0,
        pb_ratio=1.5,
        book_value=66.7,
        dividend_yield_pct=0.5,
        pays_dividend=True,
        roe_pct=14.0,
        roe_5y_avg_pct=14.0,
        roce_pct=15.0,
        debt_to_equity=0.4,
        face_value=10.0,
        fetched_at=fetched_at or datetime.now(tz=UTC),
    )


# -----------------------------------------------------------------------------
# round-trip
# -----------------------------------------------------------------------------


def test_snapshot_serialises_round_trip():
    snap = _snapshot()
    data = snapshot_to_dict(snap)
    again = ScreenerCache._deserialise_snapshot(data)
    assert again is not None
    assert again.symbol == snap.symbol
    assert again.pe_ratio == snap.pe_ratio
    assert again.fetched_at == snap.fetched_at


# -----------------------------------------------------------------------------
# get_or_fetch — cache miss + cache hit
# -----------------------------------------------------------------------------


def test_get_or_fetch_writes_to_cache_on_miss():
    redis_http = _redis_mock([_redis_response(None), _redis_response("OK")])
    redis = RedisStore(_config(), http_client=redis_http)

    fetcher = MagicMock(return_value=_snapshot("RELIANCE.NS"))
    cache = ScreenerCache(redis=redis, fetcher=fetcher)
    snap = cache.get_or_fetch("RELIANCE.NS")
    assert snap is not None
    assert snap.symbol == "RELIANCE.NS"
    fetcher.assert_called_once_with("RELIANCE.NS")
    # Second redis call is the SET that wrote the snapshot.
    assert redis_http.post.call_count == 2
    set_args = redis_http.post.call_args.kwargs["json"]
    assert set_args[0] == "SET"
    assert set_args[1].startswith("screener:")


def test_get_or_fetch_hits_cache_when_value_fresh():
    snap = _snapshot("RELIANCE.NS")
    cached_payload = json.dumps(snapshot_to_dict(snap))
    redis_http = _redis_mock([_redis_response(cached_payload)])
    redis = RedisStore(_config(), http_client=redis_http)

    fetcher = MagicMock()
    cache = ScreenerCache(redis=redis, fetcher=fetcher)
    out = cache.get_or_fetch("RELIANCE.NS")
    assert out is not None
    assert out.symbol == "RELIANCE.NS"
    fetcher.assert_not_called()  # cache hit short-circuited the fetch


def test_get_or_fetch_returns_none_if_fetcher_fails_and_no_cache():
    redis_http = _redis_mock([_redis_response(None)])
    redis = RedisStore(_config(), http_client=redis_http)

    fetcher = MagicMock(return_value=None)
    cache = ScreenerCache(redis=redis, fetcher=fetcher)
    assert cache.get_or_fetch("UNKNOWN") is None


def test_get_or_fetch_uses_stale_cache_when_fetcher_fails():
    """If a fresh fetch fails but stale data exists, surface stale rather than nothing."""
    stale_snap = _snapshot("X.NS", fetched_at=datetime.now(tz=UTC) - timedelta(days=10))
    stale_payload = json.dumps(snapshot_to_dict(stale_snap))
    # First GET returns stale value; freshness check rejects it; fetcher fails;
    # cache falls back to stale.
    redis_http = _redis_mock([_redis_response(stale_payload)])
    redis = RedisStore(_config(), http_client=redis_http)

    fetcher = MagicMock(return_value=None)
    cache = ScreenerCache(redis=redis, fetcher=fetcher, fresh_after=timedelta(hours=24))
    out = cache.get_or_fetch("X.NS")
    assert out is not None
    assert out.symbol == "X.NS"  # stale beats nothing


def test_get_or_fetch_corrupt_cache_logs_and_refetches(caplog):
    redis_http = _redis_mock([_redis_response("not json {"), _redis_response("OK")])
    redis = RedisStore(_config(), http_client=redis_http)

    snap = _snapshot("X.NS")
    fetcher = MagicMock(return_value=snap)
    cache = ScreenerCache(redis=redis, fetcher=fetcher)
    import logging

    with caplog.at_level(logging.WARNING, logger="core.data.screener_cache"):
        result = cache.get_or_fetch("X.NS")
    assert result is not None
    assert any("corrupt" in r.message.lower() for r in caplog.records)
    fetcher.assert_called_once()


def test_freshness_window_respected():
    """Snapshot older than fresh_after triggers refetch."""
    old_snap = _snapshot("X.NS", fetched_at=datetime.now(tz=UTC) - timedelta(hours=48))
    old_payload = json.dumps(snapshot_to_dict(old_snap))
    redis_http = _redis_mock([_redis_response(old_payload), _redis_response("OK")])
    redis = RedisStore(_config(), http_client=redis_http)

    fresh_snap = _snapshot("X.NS")
    fetcher = MagicMock(return_value=fresh_snap)
    cache = ScreenerCache(redis=redis, fetcher=fetcher, fresh_after=timedelta(hours=24))
    cache.get_or_fetch("X.NS")
    fetcher.assert_called_once()


def test_redis_unavailable_falls_back_to_direct_fetch():
    """If redis is None (no creds), still serve fresh fetches."""
    snap = _snapshot("X.NS")
    fetcher = MagicMock(return_value=snap)
    cache = ScreenerCache(redis=None, fetcher=fetcher)
    out = cache.get_or_fetch("X.NS")
    assert out is not None
    fetcher.assert_called_once()
