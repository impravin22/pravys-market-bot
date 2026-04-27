"""Peter Lynch GARP — Growth At Reasonable Price (free-data lite).

Lynch's signature ratio is **PEG = P/E ÷ EPS-growth**. ≤1.0 is the
classic green light, ≤0.5 is exceptional. He prefers steady fast-growers
in the 15–30% range — he was wary of anything above 30% as
unsustainable.

Free-data subset (3 checks):

| Check | Threshold |
|-------|-----------|
| `low_peg` | PEG ≤ 1.0 (computed from P/E and 3y EPS CAGR) |
| `fast_grower` | 3y EPS CAGR within 15–30% |
| `low_debt` | D/E ≤ 0.5 |

Pass-rule: at least 2 of 3 — PEG already fuses two factors so the
three checks are not fully independent.
"""

from __future__ import annotations

from core.canslim import MarketRegime, StockFundamentals
from core.strategies.base import FilterCheck, StrategyVerdict, rating_from_checks

PEG_MAX = 1.0
GROWTH_MIN_PCT = 15.0
GROWTH_MAX_PCT = 30.0
DEBT_TO_EQUITY_MAX = 0.5
MIN_PASS = 2


class LynchStrategy:
    code: str = "lynch"
    name: str = "Peter Lynch (GARP)"
    school: str = "garp"

    def evaluate(self, f: StockFundamentals, regime: MarketRegime) -> StrategyVerdict:
        checks = [
            _check_low_peg(f),
            _check_fast_grower(f),
            _check_low_debt(f),
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


def _check_low_peg(f: StockFundamentals) -> FilterCheck:
    if (
        f.pe_ratio is None
        or f.pe_ratio <= 0
        or f.annual_eps_3y_cagr_pct is None
        or f.annual_eps_3y_cagr_pct <= 0
    ):
        return FilterCheck("low_peg", False, "PEG inputs unavailable or non-positive")
    peg = f.pe_ratio / f.annual_eps_3y_cagr_pct
    return FilterCheck("low_peg", peg <= PEG_MAX, f"PEG {peg:.2f}")


def _check_fast_grower(f: StockFundamentals) -> FilterCheck:
    if f.annual_eps_3y_cagr_pct is None:
        return FilterCheck("fast_grower", False, "EPS growth unavailable")
    growth = f.annual_eps_3y_cagr_pct
    in_band = GROWTH_MIN_PCT <= growth <= GROWTH_MAX_PCT
    return FilterCheck(
        "fast_grower",
        in_band,
        f"3y EPS CAGR {growth:+.1f}% (target 15–30%)",
    )


def _check_low_debt(f: StockFundamentals) -> FilterCheck:
    if f.debt_to_equity is None:
        return FilterCheck("low_debt", False, "D/E unavailable")
    return FilterCheck(
        "low_debt",
        f.debt_to_equity <= DEBT_TO_EQUITY_MAX,
        f"D/E {f.debt_to_equity:.2f}",
    )
