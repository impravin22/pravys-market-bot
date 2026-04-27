"""Peter Lynch GARP (free-data lite) tests."""

from __future__ import annotations

from core.canslim import MarketRegime, StockFundamentals
from core.strategies.lynch import LynchStrategy


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
        pe_ratio=18.0,
        annual_eps_3y_cagr_pct=22.0,  # PEG = 18/22 = 0.82
        debt_to_equity=0.3,
    )


def test_metadata():
    s = LynchStrategy()
    assert s.code == "lynch"
    assert s.school == "garp"
    assert "Lynch" in s.name


def test_classic_garp_pick_passes():
    v = LynchStrategy().evaluate(_passing(), _regime())
    assert v.passes is True
    assert v.rating_0_100 == 100.0


def test_high_peg_fails():
    """PE 30 / EPS growth 10 = PEG 3.0 — well above 1."""
    f = StockFundamentals(
        symbol="X.NS",
        pe_ratio=30.0,
        annual_eps_3y_cagr_pct=10.0,
        debt_to_equity=0.3,
    )
    v = LynchStrategy().evaluate(f, _regime())
    assert "low_peg" in v.failing_checks


def test_too_slow_growth_fails():
    f = StockFundamentals(
        symbol="X.NS",
        pe_ratio=18.0,
        annual_eps_3y_cagr_pct=8.0,
        debt_to_equity=0.3,
    )
    v = LynchStrategy().evaluate(f, _regime())
    assert "fast_grower" in v.failing_checks


def test_too_hot_growth_fails():
    """Lynch was wary of >30% growers — unsustainable."""
    f = StockFundamentals(
        symbol="X.NS",
        pe_ratio=20.0,
        annual_eps_3y_cagr_pct=80.0,
        debt_to_equity=0.3,
    )
    v = LynchStrategy().evaluate(f, _regime())
    assert "fast_grower" in v.failing_checks


def test_high_debt_fails():
    f = StockFundamentals(
        symbol="X.NS",
        pe_ratio=18.0,
        annual_eps_3y_cagr_pct=22.0,
        debt_to_equity=1.5,
    )
    v = LynchStrategy().evaluate(f, _regime())
    assert "low_debt" in v.failing_checks


def test_negative_pe_blocks_peg():
    """Loss-making companies can't have a sensible PEG."""
    f = StockFundamentals(
        symbol="X.NS",
        pe_ratio=-10.0,
        annual_eps_3y_cagr_pct=22.0,
        debt_to_equity=0.3,
    )
    v = LynchStrategy().evaluate(f, _regime())
    assert "low_peg" in v.failing_checks


def test_no_data_fails_safely():
    v = LynchStrategy().evaluate(StockFundamentals(symbol="X.NS"), _regime())
    assert v.passes is False
    assert v.rating_0_100 == 0.0
