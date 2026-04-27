"""Joel Greenblatt Magic Formula — free-data approximation.

Greenblatt ranks the universe on two factors and buys the intersection
of the top-ranked names:

| Factor | Original | Free-data proxy |
|--------|----------|-----------------|
| Return on Capital | EBIT ÷ (NWC + NFA) | screener.in **ROCE** |
| Earnings Yield | EBIT ÷ EV | 1 / P/E (so 100 / P/E in %) |

Both proxies are imperfect — ROCE uses post-tax operating profit and
total capital employed, not Greenblatt's pre-tax EBIT and tangible
capital. EV-based earnings yield accounts for debt; 1/PE doesn't.
Acceptable for a free-data screen; bias toward false positives on
heavily-leveraged names.

Pass-rule: both checks pass.
"""

from __future__ import annotations

from core.canslim import MarketRegime, StockFundamentals
from core.strategies.base import FilterCheck, StrategyVerdict, rating_from_checks

ROCE_THRESHOLD_PCT = 15.0
EARNINGS_YIELD_THRESHOLD_PCT = 8.0


class MagicFormulaStrategy:
    code: str = "magic_formula"
    name: str = "Greenblatt Magic Formula"
    school: str = "value"

    def evaluate(self, f: StockFundamentals, regime: MarketRegime) -> StrategyVerdict:
        checks = [_check_return_on_capital(f), _check_earnings_yield(f)]
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


def _check_return_on_capital(f: StockFundamentals) -> FilterCheck:
    if f.roce_pct is None:
        return FilterCheck("high_return_on_capital", False, "ROCE unavailable")
    return FilterCheck(
        "high_return_on_capital",
        f.roce_pct >= ROCE_THRESHOLD_PCT,
        f"ROCE {f.roce_pct:.1f}%",
    )


def _check_earnings_yield(f: StockFundamentals) -> FilterCheck:
    if f.pe_ratio is None or f.pe_ratio <= 0:
        return FilterCheck("high_earnings_yield", False, "P/E unavailable or non-positive")
    earnings_yield = 100.0 / f.pe_ratio
    return FilterCheck(
        "high_earnings_yield",
        earnings_yield >= EARNINGS_YIELD_THRESHOLD_PCT,
        f"earnings yield {earnings_yield:.1f}%",
    )
