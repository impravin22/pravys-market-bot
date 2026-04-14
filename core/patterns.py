"""Chart-pattern detection (rule-based, no ML).

We detect three of the canonical IBD/CAN-SLIM bases on daily OHLC:

- **Flat base** — consolidation where the stock has traded in a tight range
  (< 15%) for at least ~5 weeks and the current close is near the range high.
- **Cup with handle** — classic O'Neil pattern. A rounding 'cup' lasting
  7–65 weeks with depth ≤ 33%, followed by a short (1–4 week) 'handle' that
  pulls back < 12% from the cup's right-side high.
- **Breakout** — the most recent bar closed above the pattern's 'pivot'
  (the right-side high for flat base, the handle's high for cup-with-handle)
  on above-average volume.

The detectors intentionally use broad thresholds — they are meant to flag
*candidates* for human review in the digest, not to emit trade signals.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

TRADING_DAYS_PER_WEEK = 5
DEFAULT_FLAT_BASE_MIN_WEEKS = 5
DEFAULT_FLAT_BASE_MAX_DEPTH_PCT = 15.0
DEFAULT_CUP_MIN_WEEKS = 7
DEFAULT_CUP_MAX_WEEKS = 65
DEFAULT_CUP_MAX_DEPTH_PCT = 33.0
DEFAULT_HANDLE_MIN_WEEKS = 1
DEFAULT_HANDLE_MAX_WEEKS = 4
DEFAULT_HANDLE_MAX_DEPTH_PCT = 12.0
DEFAULT_BREAKOUT_VOL_MULTIPLE = 1.4


@dataclass(frozen=True)
class PatternResult:
    kind: str  # 'flat-base', 'cup-with-handle', None
    pivot: float
    depth_pct: float
    weeks: int
    notes: str


def _ensure_close(df: pd.DataFrame) -> pd.Series:
    for col in ("Close", "close", "Adj Close"):
        if col in df.columns:
            return df[col].dropna()
    raise ValueError("DataFrame missing Close column")


def detect_flat_base(
    df: pd.DataFrame,
    *,
    min_weeks: int = DEFAULT_FLAT_BASE_MIN_WEEKS,
    max_depth_pct: float = DEFAULT_FLAT_BASE_MAX_DEPTH_PCT,
) -> PatternResult | None:
    closes = _ensure_close(df)
    min_bars = min_weeks * TRADING_DAYS_PER_WEEK
    if len(closes) < min_bars:
        return None
    window = closes.tail(min_bars)
    hi = float(window.max())
    lo = float(window.min())
    if hi == 0:
        return None
    depth_pct = (1.0 - lo / hi) * 100.0
    if depth_pct > max_depth_pct:
        return None
    return PatternResult(
        kind="flat-base",
        pivot=hi,
        depth_pct=round(depth_pct, 2),
        weeks=min_weeks,
        notes=f"flat base, pivot ₹{hi:,.2f}, depth {depth_pct:.1f}%",
    )


def detect_cup_with_handle(
    df: pd.DataFrame,
    *,
    min_cup_weeks: int = DEFAULT_CUP_MIN_WEEKS,
    max_cup_weeks: int = DEFAULT_CUP_MAX_WEEKS,
    max_cup_depth_pct: float = DEFAULT_CUP_MAX_DEPTH_PCT,
    min_handle_weeks: int = DEFAULT_HANDLE_MIN_WEEKS,
    max_handle_weeks: int = DEFAULT_HANDLE_MAX_WEEKS,
    max_handle_depth_pct: float = DEFAULT_HANDLE_MAX_DEPTH_PCT,
) -> PatternResult | None:
    closes = _ensure_close(df)
    if len(closes) < (min_cup_weeks + min_handle_weeks) * TRADING_DAYS_PER_WEEK:
        return None

    # Iterate over plausible cup lengths from long to short; return the first valid.
    for cup_weeks in range(max_cup_weeks, min_cup_weeks - 1, -1):
        cup_bars = cup_weeks * TRADING_DAYS_PER_WEEK
        if len(closes) < cup_bars + min_handle_weeks * TRADING_DAYS_PER_WEEK:
            continue
        for handle_weeks in range(max_handle_weeks, min_handle_weeks - 1, -1):
            handle_bars = handle_weeks * TRADING_DAYS_PER_WEEK
            total = cup_bars + handle_bars
            if len(closes) < total:
                continue

            cup = closes.iloc[-total:-handle_bars]
            handle = closes.iloc[-handle_bars:]

            cup_left = float(cup.iloc[0])
            cup_right = float(cup.iloc[-1])
            cup_low = float(cup.min())
            cup_high = max(cup_left, cup_right)
            if cup_high == 0:
                continue

            # Cup depth
            cup_depth = (1.0 - cup_low / cup_high) * 100.0
            if cup_depth > max_cup_depth_pct:
                continue
            # Left and right of cup roughly at similar level (within 10%)
            sides_diff = abs(cup_left - cup_right) / cup_high * 100.0
            if sides_diff > 10.0:
                continue

            handle_high = float(handle.max())
            handle_low = float(handle.min())
            # Handle must not exceed cup highs.
            if handle_high > cup_high * 1.02:
                continue
            handle_depth = (1.0 - handle_low / handle_high) * 100.0 if handle_high else 0.0
            if handle_depth > max_handle_depth_pct:
                continue

            pivot = handle_high
            return PatternResult(
                kind="cup-with-handle",
                pivot=round(pivot, 2),
                depth_pct=round(cup_depth, 2),
                weeks=cup_weeks + handle_weeks,
                notes=(
                    f"cup {cup_weeks}w (depth {cup_depth:.1f}%) + handle {handle_weeks}w "
                    f"(depth {handle_depth:.1f}%), pivot ₹{pivot:,.2f}"
                ),
            )
    return None


def detect_breakout(
    df: pd.DataFrame,
    pivot: float,
    *,
    vol_multiple: float = DEFAULT_BREAKOUT_VOL_MULTIPLE,
) -> bool:
    """True when the most recent close breached pivot on elevated volume."""
    if "Close" not in df.columns or "Volume" not in df.columns:
        return False
    recent = df.tail(50)
    if recent.empty:
        return False
    last_close = float(recent["Close"].iloc[-1])
    last_vol = float(recent["Volume"].iloc[-1])
    avg_vol = float(recent["Volume"].mean())
    if avg_vol == 0:
        return False
    return last_close > pivot and last_vol >= avg_vol * vol_multiple
