"""Sell-rule engine tests with synthetic OHLCV."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from core.portfolio import Holding
from core.sell_signals import SellSeverity, evaluate_holding


def _ohlcv(closes: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
    """Build a daily OHLCV frame ending today, one row per close."""
    n = len(closes)
    end = date.today()
    idx = pd.to_datetime([end - timedelta(days=n - 1 - i) for i in range(n)])
    closes_arr = np.array(closes, dtype=float)
    if volumes is None:
        volumes = [1_000_000] * n
    return pd.DataFrame(
        {
            "Open": closes_arr,
            "High": closes_arr * 1.01,
            "Low": closes_arr * 0.99,
            "Close": closes_arr,
            "Volume": np.array(volumes, dtype=float),
        },
        index=idx,
    )


def _holding(buy_price: float = 100.0, days_ago: int = 30) -> Holding:
    return Holding(
        symbol="X.NS",
        qty=10,
        buy_price=buy_price,
        buy_date=date.today() - timedelta(days=days_ago),
    )


# -----------------------------------------------------------------------------
# 7% stop
# -----------------------------------------------------------------------------


def test_seven_percent_stop_triggers_sell():
    h = _holding(buy_price=100.0)
    history = _ohlcv([100.0] * 60 + [92.5])  # last close 7.5% below buy
    signal = evaluate_holding(h, current_close=92.5, history=history)
    assert signal.severity == SellSeverity.SELL
    assert signal.rule == "stop_loss_7pct"


def test_seven_percent_stop_not_triggered_just_above():
    h = _holding(buy_price=100.0)
    history = _ohlcv([100.0] * 60 + [94.0])
    signal = evaluate_holding(h, current_close=94.0, history=history)
    assert signal.severity != SellSeverity.SELL or signal.rule != "stop_loss_7pct"


# -----------------------------------------------------------------------------
# Broke 50-DMA on volume
# -----------------------------------------------------------------------------


def test_broke_50dma_on_high_volume_triggers_sell():
    # Trade above 50-DMA (~100), then close under it on heavy volume.
    closes = [100.0] * 50 + [102.0, 95.0]
    volumes = [1_000_000] * 51 + [1_800_000]  # last day = 1.8x avg
    history = _ohlcv(closes, volumes=volumes)
    h = _holding(buy_price=100.0)
    signal = evaluate_holding(h, current_close=95.0, history=history)
    # Either 7% stop or 50-DMA breach should fire — the higher-priority one wins.
    # 95 is exactly 5% below 100, so 7% stop does NOT fire; 50-DMA breach does.
    assert signal.severity == SellSeverity.SELL
    assert signal.rule == "broke_50dma_on_volume"


def test_50dma_breach_without_volume_only_warns():
    # Close below 50-DMA but on light volume → not a sell.
    closes = [100.0] * 50 + [102.0, 99.0]
    volumes = [1_000_000] * 51 + [800_000]
    history = _ohlcv(closes, volumes=volumes)
    h = _holding(buy_price=100.0)
    signal = evaluate_holding(h, current_close=99.0, history=history)
    assert signal.severity != SellSeverity.SELL


# -----------------------------------------------------------------------------
# Climax top
# -----------------------------------------------------------------------------


def test_climax_top_triggers_sell():
    # Stock ran +30% in 21 sessions then a single +7% spike on highest volume.
    closes = list(np.linspace(100.0, 130.0, 21).tolist())
    closes.append(closes[-1] * 1.07)
    volumes = [1_000_000] * 21 + [3_000_000]  # spike volume
    history = _ohlcv(closes, volumes=volumes)
    h = _holding(buy_price=100.0, days_ago=21)
    signal = evaluate_holding(h, current_close=closes[-1], history=history)
    assert signal.rule == "climax_top"
    assert signal.severity == SellSeverity.SELL


# -----------------------------------------------------------------------------
# RS deterioration
# -----------------------------------------------------------------------------


def test_rs_deterioration_triggers_trim():
    h = _holding(buy_price=100.0)
    history = _ohlcv([100.0] * 60)
    signal = evaluate_holding(
        h, current_close=100.0, history=history, entry_rs=88.0, current_rs=65.0
    )
    assert signal.severity == SellSeverity.TRIM
    assert signal.rule == "rs_deterioration"


def test_rs_steady_does_not_trigger_anything():
    h = _holding(buy_price=100.0)
    history = _ohlcv([100.0] * 60)
    signal = evaluate_holding(
        h, current_close=100.0, history=history, entry_rs=88.0, current_rs=85.0
    )
    assert signal.severity == SellSeverity.HOLD


# -----------------------------------------------------------------------------
# 8-week rule
# -----------------------------------------------------------------------------


def test_eight_week_rule_trims_non_leader():
    """Bought 4 weeks ago, +22%, RS dropped to 75 — trim, not a leader anymore."""
    h = _holding(buy_price=100.0, days_ago=28)
    history = _ohlcv([100.0] * 60 + [122.0])
    signal = evaluate_holding(
        h, current_close=122.0, history=history, entry_rs=82.0, current_rs=75.0
    )
    assert signal.severity == SellSeverity.TRIM
    assert signal.rule == "eight_week_rule_non_leader"


def test_eight_week_rule_holds_for_leader():
    """+22% in 4 weeks but RS still ≥85 — hold the leader."""
    h = _holding(buy_price=100.0, days_ago=28)
    history = _ohlcv([100.0] * 60 + [122.0])
    signal = evaluate_holding(
        h, current_close=122.0, history=history, entry_rs=85.0, current_rs=88.0
    )
    assert signal.severity == SellSeverity.HOLD


# -----------------------------------------------------------------------------
# Hold path
# -----------------------------------------------------------------------------


def test_no_rules_fire_returns_hold():
    h = _holding(buy_price=100.0)
    history = _ohlcv([100.0, 101.0, 102.0, 101.5, 102.5])
    signal = evaluate_holding(h, current_close=102.5, history=history)
    assert signal.severity == SellSeverity.HOLD
    assert signal.rule == "hold"


# -----------------------------------------------------------------------------
# Severity ordering
# -----------------------------------------------------------------------------


def test_seven_percent_stop_beats_50dma_when_both_apply():
    """Defensive 7% stop has highest priority."""
    h = _holding(buy_price=100.0)
    closes = [100.0] * 50 + [102.0, 90.0]  # -10% breach AND below 50-DMA
    volumes = [1_000_000] * 51 + [2_000_000]
    history = _ohlcv(closes, volumes=volumes)
    signal = evaluate_holding(h, current_close=90.0, history=history)
    assert signal.rule == "stop_loss_7pct"


# -----------------------------------------------------------------------------
# Robustness
# -----------------------------------------------------------------------------


def test_short_history_does_not_crash_or_false_positive():
    h = _holding(buy_price=100.0)
    history = _ohlcv([100.0, 101.0])  # only 2 sessions
    signal = evaluate_holding(h, current_close=101.0, history=history)
    assert signal.severity == SellSeverity.HOLD
