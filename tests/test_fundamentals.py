from unittest.mock import MagicMock

import pandas as pd
import pytest

from core.canslim import StockFundamentals
from core.fundamentals import (
    _annual_eps_3y_cagr_pct,
    _quarterly_eps_yoy_pct,
    enrich_with_earnings,
    fundamentals_from_history,
)
from core.nse_data import StockHistory


def _make_history(symbol: str, closes: list[float], volumes: list[float]) -> StockHistory:
    idx = pd.date_range("2025-01-01", periods=len(closes), freq="D", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": closes,
            "High": closes,
            "Low": closes,
            "Close": closes,
            "Volume": volumes,
        },
        index=idx,
    )
    return StockHistory(symbol=symbol, history=df)


def test_fundamentals_from_history_extracts_price_features():
    hist = _make_history("FOO.NS", list(range(1, 253)), [1000.0] * 252)
    f = fundamentals_from_history("FOO.NS", hist)
    assert f.symbol == "FOO.NS"
    assert f.last_close == 252.0
    assert f.high_52w == 252.0
    assert f.low_52w == 1.0
    assert f.avg_vol_50d == 1000.0
    assert f.last_volume == 1000.0


def test_fundamentals_from_history_handles_empty():
    idx = pd.DatetimeIndex([])
    hist = StockHistory(
        symbol="FOO.NS",
        history=pd.DataFrame(
            {"Open": [], "High": [], "Low": [], "Close": [], "Volume": []}, index=idx
        ),
    )
    f = fundamentals_from_history("FOO.NS", hist)
    assert f == StockFundamentals(symbol="FOO.NS")


def _mock_ticker_with_statements(q_eps: list | None, a_eps: list | None):
    ticker = MagicMock()
    if q_eps is not None:
        q_df = pd.DataFrame([q_eps], index=["Basic EPS"])
        ticker.quarterly_income_stmt = q_df
    else:
        ticker.quarterly_income_stmt = pd.DataFrame()
    if a_eps is not None:
        a_df = pd.DataFrame([a_eps], index=["Basic EPS"])
        ticker.income_stmt = a_df
    else:
        ticker.income_stmt = pd.DataFrame()
    return ticker


def test_quarterly_eps_yoy_pct_basic():
    ticker = _mock_ticker_with_statements(q_eps=[30.0, 25.0, 20.0, 22.0, 15.0], a_eps=None)
    pct = _quarterly_eps_yoy_pct(ticker)
    assert pct == pytest.approx(100.0)  # 30/15 - 1 = 100%


def test_quarterly_eps_yoy_pct_returns_none_on_missing_history():
    ticker = _mock_ticker_with_statements(q_eps=[30.0, 25.0], a_eps=None)
    assert _quarterly_eps_yoy_pct(ticker) is None


def test_annual_eps_3y_cagr_pct_basic():
    ticker = _mock_ticker_with_statements(q_eps=None, a_eps=[80.0, 60.0, 50.0, 40.0])
    pct = _annual_eps_3y_cagr_pct(ticker)
    # (80/40)^(1/3) - 1 = 0.2599, so ~26%
    assert pct == pytest.approx(25.99, abs=0.05)


def test_annual_eps_3y_cagr_pct_returns_none_for_negative_endpoints():
    """If either endpoint is negative, CAGR is undefined and must return None.

    Before the fix, a negative ratio with fractional exponent produced a
    Python complex number, crashing `round()` with TypeError. Guard both
    endpoints to avoid that path entirely.
    """
    # latest negative
    ticker = _mock_ticker_with_statements(q_eps=None, a_eps=[-10.0, 60.0, 50.0, 40.0])
    assert _annual_eps_3y_cagr_pct(ticker) is None
    # oldest negative
    ticker = _mock_ticker_with_statements(q_eps=None, a_eps=[80.0, 60.0, 50.0, -5.0])
    assert _annual_eps_3y_cagr_pct(ticker) is None
    # both negative
    ticker = _mock_ticker_with_statements(q_eps=None, a_eps=[-80.0, -60.0, -50.0, -40.0])
    assert _annual_eps_3y_cagr_pct(ticker) is None


def test_annual_eps_3y_cagr_pct_returns_none_for_zero_endpoint():
    ticker = _mock_ticker_with_statements(q_eps=None, a_eps=[80.0, 60.0, 50.0, 0.0])
    assert _annual_eps_3y_cagr_pct(ticker) is None


def test_enrich_with_earnings_populates_fields(monkeypatch):
    def fake_ticker_factory(symbol):
        return _mock_ticker_with_statements(
            q_eps=[30.0, 25.0, 20.0, 22.0, 15.0],
            a_eps=[80.0, 60.0, 50.0, 40.0],
        )

    import core.fundamentals as module

    fake_yf = MagicMock()
    fake_yf.Ticker = fake_ticker_factory
    monkeypatch.setitem(module.__dict__, "yf", fake_yf)  # noqa: SLF001
    # The function does `import yfinance as yf` locally, so we patch sys.modules.
    import sys

    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)

    base = StockFundamentals(symbol="FOO.NS", last_close=100.0)
    enriched = enrich_with_earnings(base)
    assert enriched.quarterly_eps_yoy_pct == pytest.approx(100.0)
    assert enriched.annual_eps_3y_cagr_pct == pytest.approx(25.99, abs=0.05)
    assert enriched.last_close == 100.0  # preserved from base
