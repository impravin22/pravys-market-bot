"""Portfolio + PortfolioStore tests. Redis layer mocked end-to-end."""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock

import pytest

from bot.redis_store import RedisConfig, RedisStore
from core.portfolio import Holding, Portfolio, PortfolioStore


@pytest.fixture
def config() -> RedisConfig:
    return RedisConfig(url="https://mock.upstash", token="secret", user_id_salt="pepper")


def _response(result, *, ok: bool = True, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = {"result": result} if ok else {"error": str(result)}
    resp.text = json.dumps(resp.json.return_value)
    return resp


def _http_mock(responses: list[MagicMock]) -> MagicMock:
    http = MagicMock()

    def post(url, headers=None, json=None, timeout=None):
        return responses.pop(0)

    http.post.side_effect = post
    return http


# -----------------------------------------------------------------------------
# Holding maths
# -----------------------------------------------------------------------------


def test_holding_default_stop_loss_is_seven_pct_below_buy():
    h = Holding(symbol="X", qty=10, buy_price=100.0, buy_date=date(2026, 4, 21))
    assert h.stop_loss == 93.0


def test_holding_explicit_stop_loss_overrides_default():
    h = Holding(symbol="X", qty=10, buy_price=100.0, buy_date=date(2026, 4, 21), stop_loss=88.0)
    assert h.stop_loss == 88.0


def test_holding_pnl_pct():
    h = Holding(symbol="X", qty=10, buy_price=100.0, buy_date=date(2026, 4, 21))
    assert h.pnl_pct(current_price=110.0) == 10.0
    assert h.pnl_pct(current_price=93.0) == -7.0


def test_holding_pnl_value():
    h = Holding(symbol="X", qty=10, buy_price=100.0, buy_date=date(2026, 4, 21))
    assert h.pnl_value(current_price=110.0) == 100.0


# -----------------------------------------------------------------------------
# PortfolioStore — round-trip
# -----------------------------------------------------------------------------


def test_get_portfolio_returns_empty_when_missing(config):
    http = _http_mock([_response(None)])
    store = PortfolioStore(RedisStore(config, http_client=http))
    p = store.get(chat_id=42)
    assert p.chat_id == 42
    assert p.holdings == []


def test_add_holding_writes_payload(config):
    # First a GET (empty), then a SET.
    http = _http_mock([_response(None), _response("OK")])
    store = PortfolioStore(RedisStore(config, http_client=http))
    h = Holding(symbol="RELIANCE.NS", qty=50, buy_price=2400.0, buy_date=date(2026, 4, 21))
    store.add(chat_id=42, holding=h)
    sent = http.post.call_args.kwargs["json"]
    assert sent[0] == "SET"
    assert sent[1].startswith("portfolio:")
    payload = json.loads(sent[2])
    assert payload["chat_id"] == 42
    assert len(payload["holdings"]) == 1
    assert payload["holdings"][0]["symbol"] == "RELIANCE.NS"


def test_get_portfolio_round_trip(config):
    payload = {
        "chat_id": 42,
        "holdings": [
            {
                "symbol": "RELIANCE.NS",
                "qty": 50,
                "buy_price": 2400.0,
                "buy_date": "2026-04-21",
                "source_guru": "canslim",
                "pivot_price": None,
                "stop_loss": 2232.0,
                "target_price": None,
                "notes": "",
            }
        ],
        "cash_remaining": 0.0,
        "last_updated": "2026-04-21T00:00:00+00:00",
    }
    http = _http_mock([_response(json.dumps(payload))])
    store = PortfolioStore(RedisStore(config, http_client=http))
    p = store.get(chat_id=42)
    assert len(p.holdings) == 1
    assert p.holdings[0].symbol == "RELIANCE.NS"
    assert p.holdings[0].buy_date == date(2026, 4, 21)
    assert p.holdings[0].source_guru == "canslim"


def test_remove_returns_holding_and_persists(config):
    payload = {
        "chat_id": 42,
        "holdings": [
            {
                "symbol": "RELIANCE.NS",
                "qty": 50,
                "buy_price": 2400.0,
                "buy_date": "2026-04-21",
                "source_guru": None,
                "pivot_price": None,
                "stop_loss": 2232.0,
                "target_price": None,
                "notes": "",
            },
            {
                "symbol": "TCS.NS",
                "qty": 10,
                "buy_price": 3500.0,
                "buy_date": "2026-04-22",
                "source_guru": None,
                "pivot_price": None,
                "stop_loss": 3255.0,
                "target_price": None,
                "notes": "",
            },
        ],
        "cash_remaining": 0.0,
        "last_updated": "2026-04-21T00:00:00+00:00",
    }
    # GET → SET
    http = _http_mock([_response(json.dumps(payload)), _response("OK")])
    store = PortfolioStore(RedisStore(config, http_client=http))
    removed = store.remove(chat_id=42, symbol="RELIANCE.NS")
    assert removed is not None and removed.symbol == "RELIANCE.NS"
    set_payload = json.loads(http.post.call_args.kwargs["json"][2])
    assert {h["symbol"] for h in set_payload["holdings"]} == {"TCS.NS"}


def test_remove_non_existent_returns_none_and_does_not_write(config):
    payload = {
        "chat_id": 42,
        "holdings": [],
        "cash_remaining": 0.0,
        "last_updated": "2026-04-21T00:00:00+00:00",
    }
    http = _http_mock([_response(json.dumps(payload))])
    store = PortfolioStore(RedisStore(config, http_client=http))
    removed = store.remove(chat_id=42, symbol="GHOST.NS")
    assert removed is None
    # Only one call (GET); no SET.
    assert http.post.call_count == 1


def test_add_two_holdings_keeps_both(config):
    # GET (empty) → SET, then GET → SET.
    http = _http_mock(
        [
            _response(None),
            _response("OK"),
            _response(
                json.dumps(
                    {
                        "chat_id": 42,
                        "holdings": [
                            {
                                "symbol": "RELIANCE.NS",
                                "qty": 50,
                                "buy_price": 2400.0,
                                "buy_date": "2026-04-21",
                                "source_guru": None,
                                "pivot_price": None,
                                "stop_loss": 2232.0,
                                "target_price": None,
                                "notes": "",
                            }
                        ],
                        "cash_remaining": 0.0,
                        "last_updated": "2026-04-21T00:00:00+00:00",
                    }
                )
            ),
            _response("OK"),
        ]
    )
    store = PortfolioStore(RedisStore(config, http_client=http))
    store.add(chat_id=42, holding=Holding("RELIANCE.NS", 50, 2400.0, date(2026, 4, 21)))
    store.add(chat_id=42, holding=Holding("TCS.NS", 10, 3500.0, date(2026, 4, 22)))
    final = json.loads(http.post.call_args.kwargs["json"][2])
    assert {h["symbol"] for h in final["holdings"]} == {"RELIANCE.NS", "TCS.NS"}


def test_chat_id_does_not_appear_in_redis_key(config):
    """Same hashing discipline as chat_history — chat_id stays opaque."""
    http = _http_mock([_response(None)])
    store = PortfolioStore(RedisStore(config, http_client=http))
    store.get(chat_id=8200970431)
    sent_key = http.post.call_args.kwargs["json"][1]
    assert sent_key.startswith("portfolio:")
    assert "8200970431" not in sent_key


def test_get_corrupt_json_logs_warning_and_returns_empty(config, caplog):
    """Corrupt JSON must reset to empty AND log — never silent."""
    import logging

    http = _http_mock([_response("not-json-{")])
    store = PortfolioStore(RedisStore(config, http_client=http))
    with caplog.at_level(logging.WARNING, logger="core.portfolio"):
        portfolio = store.get(chat_id=42)
    assert portfolio.holdings == []
    assert any("corrupt" in rec.message for rec in caplog.records)


def test_portfolio_total_value(config):
    p = Portfolio(
        chat_id=1,
        holdings=[
            Holding("A", 10, 100.0, date(2026, 4, 1)),
            Holding("B", 5, 200.0, date(2026, 4, 1)),
        ],
        cash_remaining=500.0,
    )
    quotes = {"A": 110.0, "B": 220.0}
    assert p.invested_capital == 10 * 100 + 5 * 200
    assert p.market_value(quotes) == 10 * 110 + 5 * 220
    assert p.total_value(quotes) == p.market_value(quotes) + 500.0
