"""Walter Schloss — deep-value, free-data subset of his 12-rule checklist.

Schloss bought cheap stocks near book value and held a hundred at a time.
Five mechanical checks here approximate his core:
- Near 52-week low (don't chase)
- P/B ≤ 1.0 (cheap on book)
- Low debt (D/E ≤ 0.4)
- Pays a dividend (cash returns to shareholders)
- Recent earnings positive (not a falling knife)

Regime-agnostic — Schloss buys when others sell.
"""

from __future__ import annotations

from core.canslim import MarketRegime, StockFundamentals
from core.strategies.base import FilterCheck, StrategyVerdict, rating_from_checks

NEAR_LOW_MAX_PCT = 25.0  # within 25% above the 52w low
LOW_PB_MAX = 1.0
LOW_DE_MAX = 0.4


class SchlossStrategy:
    code: str = "schloss"
    name: str = "Walter Schloss (Deep Value)"
    school: str = "deep_value"

    def evaluate(self, fundamentals: StockFundamentals, regime: MarketRegime) -> StrategyVerdict:
        checks: list[FilterCheck] = [
            _check_near_52w_low(fundamentals),
            _check_low_pb(fundamentals),
            _check_low_debt(fundamentals),
            _check_pays_dividend(fundamentals),
            _check_positive_earnings(fundamentals),
        ]
        passes, rating = rating_from_checks(checks)
        return StrategyVerdict(
            code=self.code,
            name=self.name,
            school=self.school,
            passes=passes,
            rating_0_100=rating,
            checks=checks,
            notes={"phase": regime.phase},
        )


def _check_near_52w_low(f: StockFundamentals) -> FilterCheck:
    if f.last_close is None or f.low_52w is None or f.low_52w == 0:
        return FilterCheck("near_52w_low", False, "price vs 52w low unavailable")
    distance_pct = (f.last_close / f.low_52w - 1.0) * 100.0
    passes = 0.0 <= distance_pct <= NEAR_LOW_MAX_PCT
    return FilterCheck("near_52w_low", passes, f"{distance_pct:.1f}% above 52w low")


def _check_low_pb(f: StockFundamentals) -> FilterCheck:
    if f.pb_ratio is None:
        return FilterCheck("low_pb", False, "P/B unavailable")
    passes = f.pb_ratio <= LOW_PB_MAX
    return FilterCheck("low_pb", passes, f"P/B {f.pb_ratio:.2f}")


def _check_low_debt(f: StockFundamentals) -> FilterCheck:
    if f.debt_to_equity is None:
        return FilterCheck("low_debt", False, "D/E unavailable")
    passes = f.debt_to_equity <= LOW_DE_MAX
    return FilterCheck("low_debt", passes, f"D/E {f.debt_to_equity:.2f}")


def _check_pays_dividend(f: StockFundamentals) -> FilterCheck:
    if f.pays_dividend is None:
        return FilterCheck("pays_dividend", False, "dividend record unavailable")
    return FilterCheck(
        "pays_dividend",
        bool(f.pays_dividend),
        "pays dividend" if f.pays_dividend else "no dividend",
    )


def _check_positive_earnings(f: StockFundamentals) -> FilterCheck:
    if f.earnings_positive_recent is None:
        return FilterCheck("positive_earnings", False, "earnings status unavailable")
    return FilterCheck(
        "positive_earnings",
        bool(f.earnings_positive_recent),
        "recent earnings positive" if f.earnings_positive_recent else "loss-making",
    )
