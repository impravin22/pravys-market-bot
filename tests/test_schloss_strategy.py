"""Walter Schloss deep-value strategy tests."""

from __future__ import annotations

from core.canslim import MarketRegime, StockFundamentals
from core.strategies.schloss import SchlossStrategy


def _any_regime() -> MarketRegime:
    """Schloss is regime-agnostic: he buys when nobody else will."""
    return MarketRegime(
        nifty_above_50dma=False,
        nifty_above_200dma=False,
        nifty_5d_trend_up=False,
        phase="downtrend",
    )


def _classic_schloss_pick() -> StockFundamentals:
    """Cheap, low debt, pays dividend, positive earnings, near 52w low."""
    return StockFundamentals(
        symbol="VALUE.NS",
        last_close=110.0,
        high_52w=200.0,
        low_52w=100.0,
        pb_ratio=0.8,
        debt_to_equity=0.2,
        pays_dividend=True,
        earnings_positive_recent=True,
    )


def test_schloss_metadata():
    s = SchlossStrategy()
    assert s.code == "schloss"
    assert s.school == "deep_value"
    assert "Schloss" in s.name


def test_classic_schloss_pick_passes_with_high_rating():
    v = SchlossStrategy().evaluate(_classic_schloss_pick(), _any_regime())
    assert v.passes is True
    assert v.rating_0_100 >= 80


def test_far_from_52w_low_fails():
    f = StockFundamentals(
        symbol="EXPENSIVE.NS",
        last_close=190.0,
        high_52w=200.0,
        low_52w=100.0,
        pb_ratio=0.8,
        debt_to_equity=0.2,
        pays_dividend=True,
        earnings_positive_recent=True,
    )
    v = SchlossStrategy().evaluate(f, _any_regime())
    assert v.passes is False
    assert "near_52w_low" in v.failing_checks


def test_high_pb_fails():
    f = _classic_schloss_pick()
    f = StockFundamentals(
        symbol=f.symbol,
        last_close=f.last_close,
        high_52w=f.high_52w,
        low_52w=f.low_52w,
        pb_ratio=2.5,  # well above 1.0 floor
        debt_to_equity=f.debt_to_equity,
        pays_dividend=f.pays_dividend,
        earnings_positive_recent=f.earnings_positive_recent,
    )
    v = SchlossStrategy().evaluate(f, _any_regime())
    assert v.passes is False
    assert "low_pb" in v.failing_checks


def test_high_debt_fails():
    f = _classic_schloss_pick()
    f = StockFundamentals(
        symbol=f.symbol,
        last_close=f.last_close,
        high_52w=f.high_52w,
        low_52w=f.low_52w,
        pb_ratio=f.pb_ratio,
        debt_to_equity=1.5,
        pays_dividend=f.pays_dividend,
        earnings_positive_recent=f.earnings_positive_recent,
    )
    v = SchlossStrategy().evaluate(f, _any_regime())
    assert v.passes is False
    assert "low_debt" in v.failing_checks


def test_loss_making_fails():
    f = _classic_schloss_pick()
    f = StockFundamentals(
        symbol=f.symbol,
        last_close=f.last_close,
        high_52w=f.high_52w,
        low_52w=f.low_52w,
        pb_ratio=f.pb_ratio,
        debt_to_equity=f.debt_to_equity,
        pays_dividend=f.pays_dividend,
        earnings_positive_recent=False,  # losing money
    )
    v = SchlossStrategy().evaluate(f, _any_regime())
    assert v.passes is False
    assert "positive_earnings" in v.failing_checks


def test_no_data_fails_gracefully():
    v = SchlossStrategy().evaluate(StockFundamentals(symbol="NODATA.NS"), _any_regime())
    assert v.passes is False
    # All checks fail because data missing — strategy can't endorse blind.
    assert v.rating_0_100 == 0.0


def test_borderline_pb_at_one_passes():
    f = _classic_schloss_pick()
    f = StockFundamentals(
        symbol=f.symbol,
        last_close=f.last_close,
        high_52w=f.high_52w,
        low_52w=f.low_52w,
        pb_ratio=1.0,  # exactly at threshold
        debt_to_equity=f.debt_to_equity,
        pays_dividend=f.pays_dividend,
        earnings_positive_recent=f.earnings_positive_recent,
    )
    v = SchlossStrategy().evaluate(f, _any_regime())
    assert v.checks[1].passes is True  # low_pb
