import numpy as np
import pandas as pd

from core.patterns import detect_breakout, detect_cup_with_handle, detect_flat_base


def _df(closes: list[float], volumes: list[int] | None = None) -> pd.DataFrame:
    n = len(closes)
    if volumes is None:
        volumes = [1_000_000] * n
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c * 1.01 for c in closes],
            "Low": [c * 0.99 for c in closes],
            "Close": closes,
            "Volume": volumes,
        }
    )


def test_flat_base_detected_on_tight_range():
    closes = [100.0 + np.sin(i / 3) for i in range(40)]  # oscillating ±1 around 100
    result = detect_flat_base(_df(closes))
    assert result is not None
    assert result.kind == "flat-base"
    assert result.depth_pct < 15.0


def test_flat_base_rejected_when_too_deep():
    closes = [100.0 - i * 2 for i in range(25)] + [50.0] * 5
    assert detect_flat_base(_df(closes)) is None


def test_flat_base_rejected_when_too_short():
    closes = [100.0] * 10
    assert detect_flat_base(_df(closes)) is None


def test_cup_with_handle_detected_on_synthetic_pattern():
    # Build an 8-week cup + 2-week handle
    cup_weeks = 8
    handle_weeks = 2
    cup_bars = cup_weeks * 5
    handle_bars = handle_weeks * 5
    # U-shape: start 100, dip to 75, back to 100
    half = cup_bars // 2
    left = np.linspace(100.0, 75.0, half).tolist()
    right = np.linspace(75.0, 100.0, cup_bars - half).tolist()
    cup = left + right
    # Handle: mild pullback from 100 to 94 then stable
    handle = np.linspace(100.0, 94.0, handle_bars).tolist()
    closes = cup + handle
    result = detect_cup_with_handle(_df(closes))
    assert result is not None
    assert result.kind == "cup-with-handle"


def test_cup_with_handle_rejected_when_cup_too_deep():
    cup_bars = 40  # 8 weeks
    handle_bars = 10  # 2 weeks
    half = cup_bars // 2
    left = np.linspace(100.0, 40.0, half).tolist()  # 60% drop — too deep
    right = np.linspace(40.0, 100.0, cup_bars - half).tolist()
    handle = np.linspace(100.0, 95.0, handle_bars).tolist()
    closes = left + right + handle
    assert detect_cup_with_handle(_df(closes)) is None


def test_breakout_detects_above_pivot_on_high_volume():
    closes = [100.0] * 49 + [108.0]
    vols = [1_000_000] * 49 + [1_800_000]
    df = _df(closes, vols)
    assert detect_breakout(df, pivot=105.0) is True


def test_breakout_rejects_below_pivot():
    closes = [100.0] * 49 + [102.0]
    vols = [1_000_000] * 49 + [1_800_000]
    df = _df(closes, vols)
    assert detect_breakout(df, pivot=105.0) is False


def test_breakout_rejects_thin_volume():
    closes = [100.0] * 49 + [108.0]
    vols = [1_000_000] * 49 + [500_000]  # half the average
    df = _df(closes, vols)
    assert detect_breakout(df, pivot=105.0) is False
