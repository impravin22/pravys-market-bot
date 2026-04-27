"""CAN SLIM as a Strategy — wraps existing scorer, no behavioural change."""

from __future__ import annotations

from core.canslim import MarketRegime, StockFundamentals
from core.strategies.canslim_strategy import CanslimStrategy


def _strong() -> StockFundamentals:
    return StockFundamentals(
        symbol="STRONG.NS",
        last_close=950.0,
        high_52w=1000.0,
        low_52w=600.0,
        avg_vol_50d=1_000_000,
        last_volume=2_000_000,
        quarterly_eps_yoy_pct=45.0,
        annual_eps_3y_cagr_pct=28.0,
        rs_rating=92.0,
        fii_dii_5d_net_positive=True,
    )


def _uptrend() -> MarketRegime:
    return MarketRegime(
        nifty_above_50dma=True,
        nifty_above_200dma=True,
        nifty_5d_trend_up=True,
        phase="confirmed-uptrend",
    )


def _downtrend() -> MarketRegime:
    return MarketRegime(
        nifty_above_50dma=False,
        nifty_above_200dma=False,
        nifty_5d_trend_up=False,
        phase="downtrend",
    )


def test_canslim_strategy_metadata():
    s = CanslimStrategy()
    assert s.code == "canslim"
    assert s.school == "growth"
    assert "CAN SLIM" in s.name


def test_strong_stock_in_uptrend_passes_with_high_rating():
    v = CanslimStrategy().evaluate(_strong(), _uptrend())
    assert v.passes is True
    assert v.rating_0_100 == 100.0
    # All seven letters should be checks.
    assert {c.name for c in v.checks} == {"C", "A", "N", "S", "L", "I", "M"}
    assert v.notes["binary_score"] == "7"


def test_weak_stock_fails_when_few_letters_pass():
    weak = StockFundamentals(symbol="WEAK.NS")  # all None
    v = CanslimStrategy().evaluate(weak, _downtrend())
    assert v.passes is False
    # Nothing passes when fundamentals are all None and regime is downtrend.
    assert v.rating_0_100 == 0.0


def test_passes_threshold_default_is_six_of_seven():
    """Strategy verdict 'passes' = at least 6 of 7 letters passing."""
    s = CanslimStrategy()
    f = StockFundamentals(
        symbol="ALMOST.NS",
        last_close=950.0,
        high_52w=1000.0,
        avg_vol_50d=1_000_000,
        last_volume=2_000_000,
        quarterly_eps_yoy_pct=45.0,
        annual_eps_3y_cagr_pct=28.0,
        rs_rating=92.0,
        fii_dii_5d_net_positive=False,  # I fails
    )
    v = s.evaluate(f, _uptrend())
    # Six pass, one fails.
    assert sum(1 for c in v.checks if c.passes) == 6
    assert v.passes is True


def test_unknown_letters_count_as_fail_for_passing_threshold():
    """If a letter can't be evaluated it is `passes=False` here even though
    the underlying scorer reports None — strategy needs a binary view."""
    sparse = StockFundamentals(
        symbol="SPARSE.NS",
        last_close=950.0,
        high_52w=1000.0,
        avg_vol_50d=1_000_000,
        last_volume=2_000_000,
    )
    v = CanslimStrategy().evaluate(sparse, _uptrend())
    failing = {c.name for c in v.checks if not c.passes}
    # C, A, L, I unknown; only N, S, M can possibly pass.
    assert "C" in failing
    assert "A" in failing
    assert "L" in failing
    assert "I" in failing


def test_custom_pass_threshold():
    f = StockFundamentals(
        symbol="MID.NS",
        last_close=900.0,
        high_52w=1000.0,
        avg_vol_50d=1_000_000,
        last_volume=1_500_000,
        quarterly_eps_yoy_pct=30.0,
        annual_eps_3y_cagr_pct=22.0,
        rs_rating=82.0,
        fii_dii_5d_net_positive=True,
    )
    s = CanslimStrategy(min_letters_to_pass=4)
    v = s.evaluate(f, _uptrend())
    # If 4+ letters pass, the strategy passes.
    if sum(1 for c in v.checks if c.passes) >= 4:
        assert v.passes is True
