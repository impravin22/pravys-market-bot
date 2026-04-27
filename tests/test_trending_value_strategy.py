"""O'Shaughnessy Trending Value (free-data lite) tests."""

from __future__ import annotations

from core.canslim import MarketRegime, StockFundamentals
from core.strategies.trending_value import TrendingValueStrategy


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
        pe_ratio=12.0,  # cheap on earnings
        pb_ratio=1.2,  # cheap on book
        dividend_yield_pct=2.0,  # paying decent yield
        momentum_6m_pct=15.0,  # positive 6m momentum
    )


def test_metadata():
    s = TrendingValueStrategy()
    assert s.code == "trending_value"
    assert s.school == "value"
    assert "Shaughnessy" in s.name


def test_classic_pick_passes_with_high_rating():
    v = TrendingValueStrategy().evaluate(_passing(), _regime())
    assert v.passes is True
    assert v.rating_0_100 == 100.0


def test_high_pe_alone_does_not_fail_when_others_pass():
    f = _passing()
    f = StockFundamentals(
        symbol=f.symbol,
        last_close=f.last_close,
        pe_ratio=40.0,
        pb_ratio=f.pb_ratio,
        dividend_yield_pct=f.dividend_yield_pct,
        momentum_6m_pct=f.momentum_6m_pct,
    )
    v = TrendingValueStrategy().evaluate(f, _regime())
    # 3 of 4 still pass — strategy passes by default (require=3).
    assert v.passes is True
    assert "low_pe" in v.failing_checks


def test_negative_momentum_fails_momentum_check():
    f = StockFundamentals(
        symbol="X.NS",
        last_close=100.0,
        pe_ratio=12.0,
        pb_ratio=1.2,
        dividend_yield_pct=2.0,
        momentum_6m_pct=-10.0,
    )
    v = TrendingValueStrategy().evaluate(f, _regime())
    assert "positive_momentum" in v.failing_checks


def test_no_data_fails_safely():
    v = TrendingValueStrategy().evaluate(StockFundamentals(symbol="X.NS"), _regime())
    assert v.passes is False
    assert v.rating_0_100 == 0.0


def test_zero_dividend_yield_does_not_pass_yield_check():
    f = StockFundamentals(
        symbol="X.NS",
        last_close=100.0,
        pe_ratio=12.0,
        pb_ratio=1.2,
        dividend_yield_pct=0.0,
        momentum_6m_pct=10.0,
    )
    v = TrendingValueStrategy().evaluate(f, _regime())
    assert "shareholder_yield" in v.failing_checks


def test_borderline_pe_at_25_passes():
    f = StockFundamentals(
        symbol="X.NS",
        last_close=100.0,
        pe_ratio=25.0,
        pb_ratio=1.2,
        dividend_yield_pct=2.0,
        momentum_6m_pct=10.0,
    )
    v = TrendingValueStrategy().evaluate(f, _regime())
    pe_check = next(c for c in v.checks if c.name == "low_pe")
    assert pe_check.passes is True
