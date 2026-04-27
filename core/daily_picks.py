"""Daily picks engine — runs every `Strategy` over a universe and returns
the top-N stocks ranked first by guru endorsement count, then by
composite rating.

Honest empty: when the market is in a confirmed downtrend, returns ``[]``.
That is the correct answer — O'Neil's "M" rule says do not buy in a
downtrend, regardless of how good a stock looks.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from core.canslim import MarketRegime, StockFundamentals
from core.strategies.base import Strategy, StrategyVerdict

DEFAULT_TOP_N = 5
DEFAULT_MIN_COMPOSITE = 60.0
DEFAULT_WEIGHTS: Mapping[str, float] = {
    "canslim": 0.30,
    "trending_value": 0.20,
    "magic_formula": 0.15,
    "graham": 0.10,
    "buffett": 0.10,
    "lynch": 0.10,
    "schloss": 0.05,
}


@dataclass(frozen=True)
class Pick:
    symbol: str
    composite_rating: float
    endorsement_count: int
    endorsing_codes: list[str]
    verdicts: list[StrategyVerdict]
    fundamentals: StockFundamentals


def composite_rating(
    verdicts: list[StrategyVerdict],
    *,
    weights: Mapping[str, float] | None = None,
) -> float:
    """Weighted blend of strategy ratings; falls back to equal-weight if no weight matches."""
    if not verdicts:
        return 0.0
    weights = weights if weights is not None else DEFAULT_WEIGHTS
    total_weight = sum(weights.get(v.code, 0.0) for v in verdicts)
    if total_weight == 0.0:
        return round(sum(v.rating_0_100 for v in verdicts) / len(verdicts), 1)
    weighted_sum = sum(v.rating_0_100 * weights.get(v.code, 0.0) for v in verdicts)
    return round(weighted_sum / total_weight, 1)


def daily_picks(
    fundamentals: list[StockFundamentals],
    regime: MarketRegime,
    strategies: list[Strategy],
    *,
    top_n: int = DEFAULT_TOP_N,
    min_composite: float = DEFAULT_MIN_COMPOSITE,
    block_in_downtrend: bool = True,
    weights: Mapping[str, float] | None = None,
) -> list[Pick]:
    if block_in_downtrend and regime.phase == "downtrend":
        return []
    if not fundamentals or not strategies:
        return []

    picks: list[Pick] = []
    for f in fundamentals:
        verdicts = [s.evaluate(f, regime) for s in strategies]
        endorsing = [v for v in verdicts if v.passes]
        composite = composite_rating(verdicts, weights=weights)
        if composite < min_composite:
            continue
        picks.append(
            Pick(
                symbol=f.symbol,
                composite_rating=composite,
                endorsement_count=len(endorsing),
                endorsing_codes=[v.code for v in endorsing],
                verdicts=verdicts,
                fundamentals=f,
            )
        )

    picks.sort(
        key=lambda p: (p.endorsement_count, p.composite_rating),
        reverse=True,
    )
    return picks[:top_n]
