"""Relative-strength rating — percentile rank of 12-month returns across a universe.

IBD's proprietary RS Rating combines 3, 6, 9, 12-month windows with weights.
We use the simpler 12-month-return percentile which captures the same
'leader vs laggard' signal without access to their formula.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ReturnPoint:
    symbol: str
    total_return: float  # e.g. 0.35 == +35% over the lookback window


def compute_12m_return(closes: Iterable[float]) -> float | None:
    """Total return from first to last close in the provided series.

    Returns None if the series has fewer than two finite values.
    """
    arr = np.array([c for c in closes if c is not None and np.isfinite(c)], dtype=float)
    if arr.size < 2 or arr[0] == 0:
        return None
    return float(arr[-1] / arr[0] - 1.0)


def rank_by_return(points: list[ReturnPoint]) -> dict[str, float]:
    """Return {symbol: rs_rating_0_to_100} via percentile rank.

    RS Rating = percentile of the symbol's return across the universe * 100.
    A stock outperforming 92% of the universe scores 92.
    """
    valid = [p for p in points if p.total_return is not None and np.isfinite(p.total_return)]
    n = len(valid)
    if n == 0:
        return {}
    # Sort ascending by return — index divided by (n-1) gives percentile [0,1].
    sorted_pts = sorted(valid, key=lambda p: p.total_return)
    out: dict[str, float] = {}
    for i, p in enumerate(sorted_pts):
        pct = (i / (n - 1)) * 100.0 if n > 1 else 100.0
        out[p.symbol] = round(pct, 1)
    return out


def classify_rs(rs_rating: float) -> str:
    """Human-readable RS tier."""
    if rs_rating >= 90:
        return "Elite leader"
    if rs_rating >= 80:
        return "Leader"
    if rs_rating >= 60:
        return "Neutral"
    if rs_rating >= 40:
        return "Laggard"
    return "Weak"
