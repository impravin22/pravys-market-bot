"""CAN SLIM (William O'Neil) as a `Strategy` — wraps the existing scorer.

The underlying `core.canslim.score` keeps its three-state semantics
(pass / fail / unknown via `None`). The strategy adapter folds unknown
into "fail" because the panel needs a binary view per check.
"""

from __future__ import annotations

from core.canslim import MarketRegime, StockFundamentals, score
from core.strategies.base import FilterCheck, StrategyVerdict


class CanslimStrategy:
    """O'Neil's growth-and-momentum framework. Default pass = 6/7 letters."""

    code: str = "canslim"
    name: str = "O'Neil CAN SLIM"
    school: str = "growth"

    def __init__(self, *, min_letters_to_pass: int = 6) -> None:
        self._min_letters = min_letters_to_pass

    def evaluate(self, fundamentals: StockFundamentals, regime: MarketRegime) -> StrategyVerdict:
        result = score(fundamentals, regime)

        checks: list[FilterCheck] = []
        for code in ("C", "A", "N", "S", "L", "I", "M"):
            letter = result.letters[code]
            checks.append(
                FilterCheck(
                    name=code,
                    passes=bool(letter.passes),  # None → False for binary view
                    note=letter.note,
                )
            )

        passing = sum(1 for c in checks if c.passes)
        rating = round(passing / len(checks) * 100.0, 1)
        return StrategyVerdict(
            code=self.code,
            name=self.name,
            school=self.school,
            passes=passing >= self._min_letters,
            rating_0_100=rating,
            checks=checks,
            notes={
                "binary_score": str(result.binary_score),
                "continuous_score": f"{result.continuous_score:.4f}",
                "phase": regime.phase,
            },
        )
