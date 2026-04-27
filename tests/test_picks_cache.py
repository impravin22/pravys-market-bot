"""Picks cache tests — Upstash mocked end-to-end."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from bot.redis_store import RedisConfig, RedisStore
from core.canslim import StockFundamentals
from core.daily_picks import Pick
from core.picks_cache import PicksCache, picks_to_payload
from core.strategies.base import FilterCheck, StrategyVerdict


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


def _pick(symbol: str = "X.NS", *, composite: float = 85.0) -> Pick:
    return Pick(
        symbol=symbol,
        composite_rating=composite,
        endorsement_count=1,
        endorsing_codes=["canslim"],
        verdicts=[
            StrategyVerdict(
                code="canslim",
                name="O'Neil",
                school="growth",
                passes=True,
                rating_0_100=composite,
                checks=[FilterCheck(name="C", passes=True, note="+34%")],
                notes={},
            )
        ],
        fundamentals=StockFundamentals(symbol=symbol, last_close=100.0),
    )


# -----------------------------------------------------------------------------
# round-trip
# -----------------------------------------------------------------------------


def test_picks_to_payload_round_trip():
    picks = [_pick("RELIANCE.NS"), _pick("TCS.NS", composite=78.0)]
    payload = picks_to_payload(picks)
    decoded = json.loads(payload)
    assert decoded["picks"][0]["symbol"] == "RELIANCE.NS"
    assert decoded["picks"][1]["composite_rating"] == 78.0
    assert "computed_at" in decoded


# -----------------------------------------------------------------------------
# write + read
# -----------------------------------------------------------------------------


def test_write_then_read_returns_picks():
    redis_http = _redis_mock([_redis_response("OK")])
    redis = RedisStore(_config(), http_client=redis_http)
    cache = PicksCache(redis=redis)
    cache.write([_pick("RELIANCE.NS")])
    sent = redis_http.post.call_args.kwargs["json"]
    assert sent[0] == "SET"
    assert sent[1] == "picks:latest"


def test_read_returns_picks_and_computed_at():
    payload = picks_to_payload([_pick("RELIANCE.NS")])
    redis_http = _redis_mock([_redis_response(payload)])
    redis = RedisStore(_config(), http_client=redis_http)
    cache = PicksCache(redis=redis)
    out = cache.read()
    assert out is not None
    assert len(out.picks) == 1
    assert out.picks[0]["symbol"] == "RELIANCE.NS"
    assert isinstance(out.computed_at, datetime)


def test_read_returns_none_when_empty():
    redis_http = _redis_mock([_redis_response(None)])
    redis = RedisStore(_config(), http_client=redis_http)
    cache = PicksCache(redis=redis)
    assert cache.read() is None


def test_read_handles_corrupt_json():
    redis_http = _redis_mock([_redis_response("not json {")])
    redis = RedisStore(_config(), http_client=redis_http)
    cache = PicksCache(redis=redis)
    assert cache.read() is None


def test_is_fresh_within_window():
    payload = picks_to_payload([_pick()])
    redis_http = _redis_mock([_redis_response(payload)])
    redis = RedisStore(_config(), http_client=redis_http)
    cache = PicksCache(redis=redis)
    out = cache.read()
    assert out is not None
    assert cache.is_fresh(out, max_age=timedelta(hours=24)) is True


def test_is_fresh_returns_false_for_old_picks():
    old_payload = json.dumps(
        {
            "picks": [{"symbol": "X.NS"}],
            "computed_at": (datetime.now(tz=UTC) - timedelta(hours=48)).isoformat(),
        }
    )
    redis_http = _redis_mock([_redis_response(old_payload)])
    redis = RedisStore(_config(), http_client=redis_http)
    cache = PicksCache(redis=redis)
    out = cache.read()
    assert out is not None
    assert cache.is_fresh(out, max_age=timedelta(hours=24)) is False


def test_redis_unavailable_returns_none_on_read():
    cache = PicksCache(redis=None)
    assert cache.read() is None


def test_redis_unavailable_no_op_on_write():
    cache = PicksCache(redis=None)
    # Should not raise.
    cache.write([_pick()])
