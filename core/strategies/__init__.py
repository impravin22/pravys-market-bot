"""Multi-guru strategy panel — each strategy emits a `StrategyVerdict` on a stock."""

from core.strategies.base import (
    FilterCheck,
    Strategy,
    StrategyVerdict,
    rating_from_checks,
)

__all__ = [
    "FilterCheck",
    "Strategy",
    "StrategyVerdict",
    "rating_from_checks",
]
