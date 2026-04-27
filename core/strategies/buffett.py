"""Warren Buffett Lite — free-data quality screen.

Buffett's full Berkshire-grade checklist needs 10-year ROE history,
durable moat assessment, owner-earnings DCF, and qualitative judgement
on management. None of that is reliable from screener.in's top-ratios
block alone.

Free-data lite focuses on the four numerical pillars we *can* measure:

| Check | Threshold | Source |
|-------|-----------|--------|
| `high_roe` | 5y average ROE ≥ 15% | screener.in ROE field |
| `high_roic` | ROCE ≥ 12% (proxy for ROIC) | screener.in ROCE |
| `low_debt` | D/E ≤ 0.5 | screener.in |
| `earnings_positive` | recent EPS positive | derived from positive P/E |

Pass-rule: all four (quality is non-negotiable in Buffett's frame).
Three-of-four still produces a 75 rating which is informative even
when the strategy doesn't formally endorse.

Naming: this strategy is **Buffett-Lite** in the digest. We do not
claim it replicates Buffett's actual decision process.
"""

from __future__ import annotations

from core.canslim import MarketRegime, StockFundamentals
from core.strategies.base import FilterCheck, StrategyVerdict, rating_from_checks

ROE_THRESHOLD_PCT = 15.0
ROIC_THRESHOLD_PCT = 12.0
DEBT_TO_EQUITY_MAX = 0.5


class BuffettStrategy:
    code: str = "buffett"
    name: str = "Buffett Lite (Quality)"
    school: str = "quality"

    def evaluate(self, f: StockFundamentals, regime: MarketRegime) -> StrategyVerdict:
        checks = [
            _check_high_roe(f),
            _check_high_roic(f),
            _check_low_debt(f),
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


def _check_high_roe(f: StockFundamentals) -> FilterCheck:
    if f.roe_5y_avg_pct is None:
        return FilterCheck("high_roe", False, "ROE unavailable")
    return FilterCheck(
        "high_roe",
        f.roe_5y_avg_pct >= ROE_THRESHOLD_PCT,
        f"ROE {f.roe_5y_avg_pct:.1f}%",
    )


def _check_high_roic(f: StockFundamentals) -> FilterCheck:
    if f.roce_pct is None:
        return FilterCheck("high_roic", False, "ROCE (ROIC proxy) unavailable")
    return FilterCheck(
        "high_roic",
        f.roce_pct >= ROIC_THRESHOLD_PCT,
        f"ROCE {f.roce_pct:.1f}%",
    )


def _check_low_debt(f: StockFundamentals) -> FilterCheck:
    if f.debt_to_equity is None:
        return FilterCheck("low_debt", False, "D/E unavailable")
    return FilterCheck(
        "low_debt",
        f.debt_to_equity <= DEBT_TO_EQUITY_MAX,
        f"D/E {f.debt_to_equity:.2f}",
    )


def _check_earnings_positive(f: StockFundamentals) -> FilterCheck:
    if f.earnings_positive_recent is None:
        return FilterCheck("earnings_positive", False, "earnings status unavailable")
    return FilterCheck(
        "earnings_positive",
        bool(f.earnings_positive_recent),
        "earnings positive" if f.earnings_positive_recent else "loss-making",
    )
