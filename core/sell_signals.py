"""O'Neil sell-rule engine — defensive + offensive triggers on a Holding.

Operates on end-of-day OHLCV (no intraday) and emits the highest-severity
signal that fires. If no rule fires, returns HOLD with rule="hold".

Rules (priority: defensive > offensive):

| Rule | Severity | Trigger |
|------|----------|---------|
| stop_loss_7pct          | SELL | last_close ≤ buy_price × 0.93 |
| stop_loss_pivot_8pct    | SELL | pivot known and last_close ≤ pivot × 0.92 |
| broke_50dma_on_volume   | SELL | close < 50-DMA, prior close ≥ 50-DMA, volume ≥ avg_50d × 1.4 |
| climax_top              | SELL | up ≥25% in last 21 sessions AND latest day +6% on highest-volume of run |
| eight_week_rule_non_leader | TRIM | within 8 weeks of buy, ≥20% gain, RS dropped below 80 |
| rs_deterioration        | TRIM | entry RS ≥ 85, current RS < 70 |
| hold                    | HOLD | none of the above |
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING, Final

import pandas as pd

if TYPE_CHECKING:
    from core.portfolio import Holding

DEFAULT_STOP_PCT: Final[float] = 0.07
PIVOT_STOP_PCT: Final[float] = 0.08
DMA_VOLUME_MULTIPLIER: Final[float] = 1.4
CLIMAX_GAIN_PCT: Final[float] = 25.0
CLIMAX_DAILY_SPIKE_PCT: Final[float] = 6.0
CLIMAX_LOOKBACK_SESSIONS: Final[int] = 21
EIGHT_WEEKS_DAYS: Final[int] = 56
EIGHT_WEEK_GAIN_PCT: Final[float] = 20.0
LEADER_RS_THRESHOLD: Final[float] = 80.0
RS_DETERIORATION_FROM: Final[float] = 85.0
RS_DETERIORATION_TO: Final[float] = 70.0


class SellSeverity(StrEnum):
    SELL = "sell"
    TRIM = "trim"
    WATCH = "watch"
    HOLD = "hold"


@dataclass(frozen=True)
class SellSignal:
    severity: SellSeverity
    rule: str
    reason: str
    confidence: float = 1.0


# Rule priority — first match wins. Defensive rules come first.
_RULE_ORDER = (
    "stop_loss_7pct",
    "stop_loss_pivot_8pct",
    "broke_50dma_on_volume",
    "climax_top",
    "eight_week_rule_non_leader",
    "rs_deterioration",
)


def evaluate_holding(
    holding: Holding,
    *,
    current_close: float,
    history: pd.DataFrame,
    today: date | None = None,
    current_rs: float | None = None,
    entry_rs: float | None = None,
) -> SellSignal:
    today = today or date.today()
    closes = history["Close"].dropna() if "Close" in history else pd.Series(dtype=float)
    volumes = history["Volume"].dropna() if "Volume" in history else pd.Series(dtype=float)

    candidates: list[SellSignal] = []

    if (signal := _stop_7pct(holding, current_close)) is not None:
        candidates.append(signal)
    if (signal := _stop_pivot_8pct(holding, current_close)) is not None:
        candidates.append(signal)
    if (signal := _broke_50dma_on_volume(closes, volumes, current_close)) is not None:
        candidates.append(signal)
    if (signal := _climax_top(closes, volumes, current_close)) is not None:
        candidates.append(signal)
    if (signal := _eight_week_rule(holding, current_close, today, current_rs)) is not None:
        candidates.append(signal)
    if (signal := _rs_deterioration(entry_rs, current_rs)) is not None:
        candidates.append(signal)

    if not candidates:
        return SellSignal(SellSeverity.HOLD, "hold", "no sell rule triggered")

    # Pick by rule priority, not severity, so 7%-stop always trumps 50-DMA
    # even when both fire.
    candidates.sort(key=lambda s: _RULE_ORDER.index(s.rule))
    return candidates[0]


# -----------------------------------------------------------------------------
# Individual rules
# -----------------------------------------------------------------------------


def _stop_7pct(holding: Holding, current_close: float) -> SellSignal | None:
    floor = holding.buy_price * (1.0 - DEFAULT_STOP_PCT)
    if current_close <= floor:
        return SellSignal(
            SellSeverity.SELL,
            "stop_loss_7pct",
            (
                f"closed ₹{current_close:.2f} ≤ ₹{floor:.2f} "
                f"(7% defensive stop from ₹{holding.buy_price:.2f})"
            ),
        )
    return None


def _stop_pivot_8pct(holding: Holding, current_close: float) -> SellSignal | None:
    if holding.pivot_price is None:
        return None
    floor = holding.pivot_price * (1.0 - PIVOT_STOP_PCT)
    if current_close <= floor:
        return SellSignal(
            SellSeverity.SELL,
            "stop_loss_pivot_8pct",
            f"closed ₹{current_close:.2f} ≤ ₹{floor:.2f} (8% below pivot ₹{holding.pivot_price:.2f})",
        )
    return None


def _broke_50dma_on_volume(
    closes: pd.Series, volumes: pd.Series, current_close: float
) -> SellSignal | None:
    if len(closes) < 51 or len(volumes) < 50:
        return None
    dma_50 = float(closes.tail(50).mean())
    prev_close = float(closes.iloc[-2])
    avg_vol_50 = float(volumes.tail(50).mean())
    last_vol = float(volumes.iloc[-1])
    if (
        current_close < dma_50
        and prev_close >= dma_50
        and avg_vol_50 > 0
        and last_vol >= avg_vol_50 * DMA_VOLUME_MULTIPLIER
    ):
        return SellSignal(
            SellSeverity.SELL,
            "broke_50dma_on_volume",
            (
                f"closed ₹{current_close:.2f} below 50-DMA ₹{dma_50:.2f} on "
                f"{last_vol / avg_vol_50:.1f}x avg volume"
            ),
        )
    return None


def _climax_top(closes: pd.Series, volumes: pd.Series, current_close: float) -> SellSignal | None:
    if len(closes) < CLIMAX_LOOKBACK_SESSIONS + 1:
        return None
    window = closes.tail(CLIMAX_LOOKBACK_SESSIONS + 1)
    start = float(window.iloc[0])
    if start <= 0:
        return None
    gain_pct = (current_close / start - 1.0) * 100.0
    if gain_pct < CLIMAX_GAIN_PCT:
        return None
    prev_close = float(closes.iloc[-2])
    daily_spike_pct = (current_close / prev_close - 1.0) * 100.0
    if daily_spike_pct < CLIMAX_DAILY_SPIKE_PCT:
        return None
    if len(volumes) >= CLIMAX_LOOKBACK_SESSIONS + 1:
        recent_vol = volumes.tail(CLIMAX_LOOKBACK_SESSIONS + 1)
        if float(volumes.iloc[-1]) < float(recent_vol.max()):
            return None
    return SellSignal(
        SellSeverity.SELL,
        "climax_top",
        (
            f"+{gain_pct:.1f}% in {CLIMAX_LOOKBACK_SESSIONS} sessions, "
            f"+{daily_spike_pct:.1f}% today on highest volume of run — exhaustion"
        ),
    )


def _eight_week_rule(
    holding: Holding, current_close: float, today: date, current_rs: float | None
) -> SellSignal | None:
    days_held = (today - holding.buy_date).days
    if not (0 <= days_held <= EIGHT_WEEKS_DAYS):
        return None
    gain_pct = (current_close / holding.buy_price - 1.0) * 100.0
    if gain_pct < EIGHT_WEEK_GAIN_PCT:
        return None
    # No RS data → can't classify leader vs non-leader. Stay silent rather
    # than firing a TRIM signal on missing information.
    if current_rs is None:
        return None
    if current_rs >= LEADER_RS_THRESHOLD:
        return None  # leaders run — hold them
    return SellSignal(
        SellSeverity.TRIM,
        "eight_week_rule_non_leader",
        (
            f"+{gain_pct:.1f}% in {days_held}d but RS={current_rs:.0f} "
            f"(<{LEADER_RS_THRESHOLD:.0f}) — take profit on the non-leader"
        ),
    )


def _rs_deterioration(entry_rs: float | None, current_rs: float | None) -> SellSignal | None:
    if entry_rs is None or current_rs is None:
        return None
    if entry_rs >= RS_DETERIORATION_FROM and current_rs < RS_DETERIORATION_TO:
        return SellSignal(
            SellSeverity.TRIM,
            "rs_deterioration",
            f"RS dropped {entry_rs:.0f} → {current_rs:.0f} since entry — leadership lost",
        )
    return None
