"""Warren Buffett Lite (free-data subset) tests."""

from __future__ import annotations

from core.canslim import MarketRegime, StockFundamentals
from core.strategies.buffett import BuffettStrategy


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
        roe_5y_avg_pct=18.0,
        roce_pct=20.0,
        debt_to_equity=0.3,
        earnings_positive_recent=True,
    )


def test_metadata():
    s = BuffettStrategy()
    assert s.code == "buffett"
    assert s.school == "quality"
    assert "Buffett" in s.name


def test_quality_pick_passes_with_high_rating():
    v = BuffettStrategy().evaluate(_passing(), _regime())
    assert v.passes is True
    assert v.rating_0_100 == 100.0


def test_low_roe_fails():
    f = _passing()
    f = StockFundamentals(
        symbol=f.symbol,
        roe_5y_avg_pct=8.0,
        roce_pct=f.roce_pct,
        debt_to_equity=f.debt_to_equity,
        earnings_positive_recent=f.earnings_positive_recent,
    )
    v = BuffettStrategy().evaluate(f, _regime())
    assert "high_roe" in v.failing_checks


def test_high_debt_fails():
    f = _passing()
    f = StockFundamentals(
        symbol=f.symbol,
        roe_5y_avg_pct=f.roe_5y_avg_pct,
        roce_pct=f.roce_pct,
        debt_to_equity=1.5,
        earnings_positive_recent=f.earnings_positive_recent,
    )
    v = BuffettStrategy().evaluate(f, _regime())
    assert "low_debt" in v.failing_checks


def test_low_roic_proxy_fails():
    f = _passing()
    f = StockFundamentals(
        symbol=f.symbol,
        roe_5y_avg_pct=f.roe_5y_avg_pct,
        roce_pct=8.0,
        debt_to_equity=f.debt_to_equity,
        earnings_positive_recent=f.earnings_positive_recent,
    )
    v = BuffettStrategy().evaluate(f, _regime())
    assert "high_roic" in v.failing_checks


def test_loss_making_fails():
    f = _passing()
    f = StockFundamentals(
        symbol=f.symbol,
        roe_5y_avg_pct=f.roe_5y_avg_pct,
        roce_pct=f.roce_pct,
        debt_to_equity=f.debt_to_equity,
        earnings_positive_recent=False,
    )
    v = BuffettStrategy().evaluate(f, _regime())
    assert "earnings_positive" in v.failing_checks


def test_three_of_four_passing_does_not_overall_pass_by_default():
    """Buffett is strict — all four checks must pass."""
    f = StockFundamentals(
        symbol="X.NS",
        roe_5y_avg_pct=18.0,
        roce_pct=20.0,
        debt_to_equity=1.5,
        earnings_positive_recent=True,
    )
    v = BuffettStrategy().evaluate(f, _regime())
    assert v.passes is False
    assert v.rating_0_100 == 75.0


def test_no_data_fails_safely():
    v = BuffettStrategy().evaluate(StockFundamentals(symbol="X.NS"), _regime())
    assert v.passes is False
    assert v.rating_0_100 == 0.0


def test_borderline_roe_at_15_passes():
    f = StockFundamentals(
        symbol="X.NS",
        roe_5y_avg_pct=15.0,
        roce_pct=20.0,
        debt_to_equity=0.3,
        earnings_positive_recent=True,
    )
    v = BuffettStrategy().evaluate(f, _regime())
    roe_check = next(c for c in v.checks if c.name == "high_roe")
    assert roe_check.passes is True
