"""Benjamin Graham Defensive — free-data lite.

Graham's Defensive Investor checklist demands long financial history we
don't have for free (10-year EPS continuity, 20-year dividend record,
33% per-share earnings growth over a decade). Free-data subset:

| Check | Threshold |
|-------|-----------|
| `low_pe` | P/E ≤ 15 |
| `low_pb` | P/B ≤ 1.5 |
| `strong_current_ratio` | current ratio ≥ 2.0 |
| `pays_dividend` | dividend yield > 0 AND `pays_dividend=True` |
| `earnings_positive` | `earnings_positive_recent=True` |

Pass-rule: all five (Graham was strict). Rating reflects fraction.
"""

from __future__ import annotations

from core.canslim import MarketRegime, StockFundamentals
from core.strategies.base import FilterCheck, StrategyVerdict, rating_from_checks

LOW_PE_MAX = 15.0
LOW_PB_MAX = 1.5
CURRENT_RATIO_MIN = 2.0


class GrahamStrategy:
    code: str = "graham"
    name: str = "Benjamin Graham (Defensive)"
    school: str = "value"

    def evaluate(self, f: StockFundamentals, regime: MarketRegime) -> StrategyVerdict:
        checks = [
            _check_low_pe(f),
            _check_low_pb(f),
            _check_current_ratio(f),
            _check_pays_dividend(f),
            _check_earnings_positive(f),
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


def _check_low_pe(f: StockFundamentals) -> FilterCheck:
    if f.pe_ratio is None:
        return FilterCheck("low_pe", False, "P/E unavailable")
    return FilterCheck("low_pe", f.pe_ratio <= LOW_PE_MAX, f"P/E {f.pe_ratio:.1f}")


def _check_low_pb(f: StockFundamentals) -> FilterCheck:
    if f.pb_ratio is None:
        return FilterCheck("low_pb", False, "P/B unavailable")
    return FilterCheck("low_pb", f.pb_ratio <= LOW_PB_MAX, f"P/B {f.pb_ratio:.2f}")


def _check_current_ratio(f: StockFundamentals) -> FilterCheck:
    if f.current_ratio is None:
        return FilterCheck("strong_current_ratio", False, "current ratio unavailable")
    return FilterCheck(
        "strong_current_ratio",
        f.current_ratio >= CURRENT_RATIO_MIN,
        f"current ratio {f.current_ratio:.2f}",
    )


def _check_pays_dividend(f: StockFundamentals) -> FilterCheck:
    paying = bool(f.pays_dividend) and (f.dividend_yield_pct or 0.0) > 0.0
    if f.pays_dividend is None and f.dividend_yield_pct is None:
        return FilterCheck("pays_dividend", False, "dividend record unavailable")
    return FilterCheck(
        "pays_dividend",
        paying,
        "pays dividend" if paying else "no dividend",
    )


def _check_earnings_positive(f: StockFundamentals) -> FilterCheck:
    if f.earnings_positive_recent is None:
        return FilterCheck("earnings_positive", False, "earnings status unavailable")
    return FilterCheck(
        "earnings_positive",
        bool(f.earnings_positive_recent),
        "recent earnings positive" if f.earnings_positive_recent else "loss-making",
    )
