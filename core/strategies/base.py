"""Strategy protocol and primitives for the guru panel.

Each guru (CAN SLIM, Schloss, Greenblatt, ...) implements the `Strategy`
protocol and emits a `StrategyVerdict` for one stock under one market
regime. Verdicts are deterministic, comparable across gurus, and
serialise cleanly into the daily Telegram digest.

The `rating_0_100` field is intentionally *not* a percentile across the
universe — it is the strategy's own opinion of how strongly the stock
fits its rules, derived from `FilterCheck` weights. Cross-strategy
comparison is done by counting endorsements first, ratings second.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from core.canslim import MarketRegime, StockFundamentals


@dataclass(frozen=True)
class FilterCheck:
    """One named rule inside a strategy: passed or failed, with a note."""

    name: str
    passes: bool
    note: str
    weight: float = 1.0


@dataclass(frozen=True)
class StrategyVerdict:
    """Per-stock outcome of one strategy."""

    code: str  # "canslim", "schloss", ...
    name: str  # "O'Neil CAN SLIM"
    school: str  # "growth" | "value" | "garp" | "deep_value" | "quality"
    passes: bool  # overall strategy verdict
    rating_0_100: float
    checks: list[FilterCheck]
    notes: dict[str, str]

    @property
    def passing_checks(self) -> list[str]:
        return [c.name for c in self.checks if c.passes]

    @property
    def failing_checks(self) -> list[str]:
        return [c.name for c in self.checks if not c.passes]


@runtime_checkable
class Strategy(Protocol):
    """Anything implementing this protocol can plug into the guru panel."""

    code: str
    name: str
    school: str

    def evaluate(
        self, fundamentals: StockFundamentals, regime: MarketRegime
    ) -> StrategyVerdict: ...


def rating_from_checks(
    checks: list[FilterCheck],
    *,
    require: int | None = None,
) -> tuple[bool, float]:
    """Aggregate a list of `FilterCheck` into ``(passes_overall, rating_0_100)``.

    `rating` = weighted fraction of checks that passed × 100, rounded to 1dp.

    `passes` = ``True`` iff:
    - every check passed (`require=None` default), OR
    - at least ``require`` checks passed when ``require`` is provided.

    Empty list or zero total weight returns ``(False, 0.0)`` — no signal.
    """
    if not checks:
        return False, 0.0
    total_weight = sum(c.weight for c in checks)
    if total_weight == 0:
        return False, 0.0
    weighted_pass = sum(c.weight for c in checks if c.passes)
    rating = round(weighted_pass / total_weight * 100.0, 1)
    pass_count = sum(1 for c in checks if c.passes)
    passes = pass_count >= require if require is not None else all(c.passes for c in checks)
    return passes, rating
