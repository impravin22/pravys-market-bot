"""Multi-guru strategy panel — each strategy emits a `StrategyVerdict` on a stock."""

from core.strategies.base import (
    FilterCheck,
    Strategy,
    StrategyVerdict,
    rating_from_checks,
)
from core.strategies.buffett import BuffettStrategy
from core.strategies.canslim_strategy import CanslimStrategy
from core.strategies.graham import GrahamStrategy
from core.strategies.lynch import LynchStrategy
from core.strategies.magic_formula import MagicFormulaStrategy
from core.strategies.schloss import SchlossStrategy
from core.strategies.trending_value import TrendingValueStrategy


def all_strategies() -> list[Strategy]:
    """Default seven-guru panel used by the daily picks job + /why command.

    Order is intentional: O'Neil leads (the project's house style),
    followed by O'Shaughnessy / Greenblatt / Graham / Buffett-Lite /
    Lynch / Schloss. Mix of growth, value, GARP, deep-value voices.
    """
    return [
        CanslimStrategy(),
        TrendingValueStrategy(),
        MagicFormulaStrategy(),
        GrahamStrategy(),
        BuffettStrategy(),
        LynchStrategy(),
        SchlossStrategy(),
    ]


__all__ = [
    "BuffettStrategy",
    "CanslimStrategy",
    "FilterCheck",
    "GrahamStrategy",
    "LynchStrategy",
    "MagicFormulaStrategy",
    "SchlossStrategy",
    "Strategy",
    "StrategyVerdict",
    "TrendingValueStrategy",
    "all_strategies",
    "rating_from_checks",
]
