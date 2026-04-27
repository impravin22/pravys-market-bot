"""Benjamin Graham Defensive (free-data lite) tests."""

from __future__ import annotations

from core.canslim import MarketRegime, StockFundamentals
from core.strategies.graham import GrahamStrategy


def _regime() -> MarketRegime:
    return MarketRegime(
        nifty_above_50dma=True,
        nifty_above_200dma=True,
        nifty_5d_trend_up=True,
        phase="confirmed-uptrend",
    )


def _passing() -> StockFundamentals:
    return StockFundamentals(
        symbol="X.NS",
        last_close=100.0,
        pe_ratio=12.0,
        pb_ratio=1.2,
        current_ratio=2.5,
        dividend_yield_pct=2.0,
        pays_dividend=True,
        earnings_positive_recent=True,
    )


def test_metadata():
    s = GrahamStrategy()
    assert s.code == "graham"
    assert s.school == "value"
    assert "Graham" in s.name


def test_classic_defensive_pick_passes():
    v = GrahamStrategy().evaluate(_passing(), _regime())
    assert v.passes is True
    assert v.rating_0_100 >= 80


def test_high_pe_fails():
    f = _passing()
    f = StockFundamentals(
        symbol=f.symbol,
        last_close=f.last_close,
        pe_ratio=25.0,
        pb_ratio=f.pb_ratio,
        current_ratio=f.current_ratio,
        dividend_yield_pct=f.dividend_yield_pct,
        pays_dividend=f.pays_dividend,
        earnings_positive_recent=f.earnings_positive_recent,
    )
    v = GrahamStrategy().evaluate(f, _regime())
    assert "low_pe" in v.failing_checks
    assert v.passes is False


def test_high_pb_fails():
    f = _passing()
    f = StockFundamentals(
        symbol=f.symbol,
        last_close=f.last_close,
        pe_ratio=f.pe_ratio,
        pb_ratio=2.5,
        current_ratio=f.current_ratio,
        dividend_yield_pct=f.dividend_yield_pct,
        pays_dividend=f.pays_dividend,
        earnings_positive_recent=f.earnings_positive_recent,
    )
    v = GrahamStrategy().evaluate(f, _regime())
    assert "low_pb" in v.failing_checks


def test_low_current_ratio_fails():
    f = _passing()
    f = StockFundamentals(
        symbol=f.symbol,
        last_close=f.last_close,
        pe_ratio=f.pe_ratio,
        pb_ratio=f.pb_ratio,
        current_ratio=1.2,
        dividend_yield_pct=f.dividend_yield_pct,
        pays_dividend=f.pays_dividend,
        earnings_positive_recent=f.earnings_positive_recent,
    )
    v = GrahamStrategy().evaluate(f, _regime())
    assert "strong_current_ratio" in v.failing_checks


def test_no_dividend_fails():
    f = _passing()
    f = StockFundamentals(
        symbol=f.symbol,
        last_close=f.last_close,
        pe_ratio=f.pe_ratio,
        pb_ratio=f.pb_ratio,
        current_ratio=f.current_ratio,
        dividend_yield_pct=0.0,
        pays_dividend=False,
        earnings_positive_recent=f.earnings_positive_recent,
    )
    v = GrahamStrategy().evaluate(f, _regime())
    assert "pays_dividend" in v.failing_checks


def test_loss_making_fails():
    f = _passing()
    f = StockFundamentals(
        symbol=f.symbol,
        last_close=f.last_close,
        pe_ratio=f.pe_ratio,
        pb_ratio=f.pb_ratio,
        current_ratio=f.current_ratio,
        dividend_yield_pct=f.dividend_yield_pct,
        pays_dividend=f.pays_dividend,
        earnings_positive_recent=False,
    )
    v = GrahamStrategy().evaluate(f, _regime())
    assert "earnings_positive" in v.failing_checks


def test_no_data_fails():
    v = GrahamStrategy().evaluate(StockFundamentals(symbol="X.NS"), _regime())
    assert v.passes is False
    assert v.rating_0_100 == 0.0
