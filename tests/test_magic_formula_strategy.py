"""Joel Greenblatt Magic Formula (free-data lite) tests."""

from __future__ import annotations

from core.canslim import MarketRegime, StockFundamentals
from core.strategies.magic_formula import MagicFormulaStrategy


def _regime() -> MarketRegime:
    return MarketRegime(
        nifty_above_50dma=True,
        nifty_above_200dma=True,
        nifty_5d_trend_up=True,
        phase="confirmed-uptrend",
    )


def test_metadata():
    s = MagicFormulaStrategy()
    assert s.code == "magic_formula"
    assert s.school == "value"
    assert "Greenblatt" in s.name


def test_high_roce_and_high_earnings_yield_passes():
    f = StockFundamentals(
        symbol="X.NS",
        pe_ratio=10.0,  # earnings yield = 10%
        roce_pct=22.0,
    )
    v = MagicFormulaStrategy().evaluate(f, _regime())
    assert v.passes is True
    assert v.rating_0_100 == 100.0


def test_low_roce_fails():
    f = StockFundamentals(
        symbol="X.NS",
        pe_ratio=10.0,
        roce_pct=5.0,
    )
    v = MagicFormulaStrategy().evaluate(f, _regime())
    assert v.passes is False
    assert "high_return_on_capital" in v.failing_checks


def test_high_pe_low_yield_fails():
    f = StockFundamentals(
        symbol="X.NS",
        pe_ratio=50.0,  # earnings yield = 2%
        roce_pct=22.0,
    )
    v = MagicFormulaStrategy().evaluate(f, _regime())
    assert v.passes is False
    assert "high_earnings_yield" in v.failing_checks


def test_negative_pe_fails_yield_check():
    f = StockFundamentals(
        symbol="X.NS",
        pe_ratio=-15.0,  # loss-making
        roce_pct=22.0,
    )
    v = MagicFormulaStrategy().evaluate(f, _regime())
    assert "high_earnings_yield" in v.failing_checks


def test_no_data_fails_safely():
    v = MagicFormulaStrategy().evaluate(StockFundamentals(symbol="X.NS"), _regime())
    assert v.passes is False
    assert v.rating_0_100 == 0.0


def test_borderline_roce_at_15_passes():
    f = StockFundamentals(symbol="X.NS", pe_ratio=10.0, roce_pct=15.0)
    v = MagicFormulaStrategy().evaluate(f, _regime())
    roce_check = next(c for c in v.checks if c.name == "high_return_on_capital")
    assert roce_check.passes is True
