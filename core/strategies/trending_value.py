"""James O'Shaughnessy Trending Value — free-data lite.

The full What-Works-on-Wall-Street recipe is a Value Composite Two
(VC2) rank across 6 factors (P/E, P/B, P/S, P/CF, EV/EBITDA, shareholder
yield) followed by a 6-month momentum sort on the top decile.

Free-data lite: we drop P/S, P/CF, EV/EBITDA (not surfaced by
screener.in's top-ratios block) and shareholder buyback yield (no public
buyback feed). What remains:

| Check | Threshold |
|-------|-----------|
| `low_pe` | P/E ≤ 25 |
| `low_pb` | P/B ≤ 3.0 |
| `shareholder_yield` | dividend yield > 0 |
| `positive_momentum` | 6-month price return > 0 |

Pass-rule: at least 3 of 4 pass — keeps the spirit of "cheap and
trending" without forcing every factor to clear.
"""

from __future__ import annotations

from core.canslim import MarketRegime, StockFundamentals
from core.strategies.base import FilterCheck, StrategyVerdict, rating_from_checks

LOW_PE_MAX = 25.0
LOW_PB_MAX = 3.0
MIN_PASS = 3


class TrendingValueStrategy:
    code: str = "trending_value"
    name: str = "O'Shaughnessy Trending Value (lite)"
    school: str = "value"

    def evaluate(self, f: StockFundamentals, regime: MarketRegime) -> StrategyVerdict:
        checks = [
            _check_low_pe(f),
            _check_low_pb(f),
            _check_shareholder_yield(f),
            _check_momentum(f),
        ]
        passes, rating = rating_from_checks(checks, require=MIN_PASS)
        return StrategyVerdict(
            code=self.code,
            name=self.name,
            school=self.school,
            passes=passes,
            rating_0_100=rating,
            checks=checks,
            notes={"phase": regime.phase},
        )


def _check_low_pe(f: StockFundamentals) -> FilterCheck:
    if f.pe_ratio is None:
        return FilterCheck("low_pe", False, "P/E unavailable")
    return FilterCheck("low_pe", f.pe_ratio <= LOW_PE_MAX, f"P/E {f.pe_ratio:.1f}")


def _check_low_pb(f: StockFundamentals) -> FilterCheck:
    if f.pb_ratio is None:
        return FilterCheck("low_pb", False, "P/B unavailable")
    return FilterCheck("low_pb", f.pb_ratio <= LOW_PB_MAX, f"P/B {f.pb_ratio:.2f}")


def _check_shareholder_yield(f: StockFundamentals) -> FilterCheck:
    if f.dividend_yield_pct is None:
        return FilterCheck("shareholder_yield", False, "dividend yield unavailable")
    return FilterCheck(
        "shareholder_yield",
        f.dividend_yield_pct > 0.0,
        f"div yield {f.dividend_yield_pct:.2f}%",
    )


def _check_momentum(f: StockFundamentals) -> FilterCheck:
    if f.momentum_6m_pct is None:
        return FilterCheck("positive_momentum", False, "6m momentum unavailable")
    return FilterCheck(
        "positive_momentum",
        f.momentum_6m_pct > 0.0,
        f"6m return {f.momentum_6m_pct:+.1f}%",
    )
