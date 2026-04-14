"""Adapter — yfinance `Ticker` objects → `StockFundamentals` records.

Every field is optional on failure; callers rely on CAN SLIM's graceful
'None means unknown' semantics.
"""

from __future__ import annotations

import logging
from dataclasses import replace

import pandas as pd

from core.canslim import StockFundamentals
from core.nse_data import StockHistory

logger = logging.getLogger(__name__)

QUARTERLY_EPS_KEYS = ("Basic EPS", "Diluted EPS")
ANNUAL_EPS_KEYS = ("Basic EPS", "Diluted EPS")


def fundamentals_from_history(symbol: str, history: StockHistory) -> StockFundamentals:
    """Extract price/volume features from OHLCV history.

    Earnings-related fields are filled in by a separate call (`enrich_with_earnings`)
    because Yahoo Finance returns earnings on a separate Ticker call.
    """
    df = history.history
    if df is None or df.empty:
        return StockFundamentals(symbol=symbol)

    closes = df["Close"].dropna()
    if closes.empty:
        return StockFundamentals(symbol=symbol)

    last_close = float(closes.iloc[-1])
    window_52w = closes.tail(252)
    high_52w = float(window_52w.max())
    low_52w = float(window_52w.min())

    volumes = df["Volume"].dropna()
    avg_vol_50d = float(volumes.tail(50).mean()) if not volumes.empty else None
    last_volume = float(volumes.iloc[-1]) if not volumes.empty else None

    return StockFundamentals(
        symbol=symbol,
        last_close=last_close,
        high_52w=high_52w,
        low_52w=low_52w,
        avg_vol_50d=avg_vol_50d,
        last_volume=last_volume,
    )


def enrich_with_earnings(base: StockFundamentals) -> StockFundamentals:
    """Add quarterly YoY EPS growth and 3-year annual EPS CAGR from yfinance."""
    try:
        import yfinance as yf  # noqa: PLC0415
    except ImportError:
        return base

    ticker = yf.Ticker(base.symbol)

    q_growth = _quarterly_eps_yoy_pct(ticker)
    a_cagr = _annual_eps_3y_cagr_pct(ticker)

    return replace(
        base,
        quarterly_eps_yoy_pct=q_growth,
        annual_eps_3y_cagr_pct=a_cagr,
    )


def _first_available_row(df: pd.DataFrame, keys: tuple[str, ...]) -> pd.Series | None:
    if df is None or df.empty:
        return None
    for key in keys:
        if key in df.index:
            return df.loc[key]
    return None


def _quarterly_eps_yoy_pct(ticker) -> float | None:
    try:
        q = ticker.quarterly_income_stmt
    except Exception as exc:  # noqa: BLE001
        logger.info("quarterly_income_stmt %s failed: %s", getattr(ticker, "ticker", "?"), exc)
        return None
    row = _first_available_row(q, QUARTERLY_EPS_KEYS)
    if row is None or len(row) < 5:
        # Need current + same quarter last year (indices 0 and 4 with latest first)
        return None
    try:
        current = float(row.iloc[0])
        year_ago = float(row.iloc[4])
    except (ValueError, TypeError):
        return None
    if year_ago == 0 or pd.isna(current) or pd.isna(year_ago):
        return None
    return round((current / abs(year_ago) - 1.0) * 100.0, 2)


def _annual_eps_3y_cagr_pct(ticker) -> float | None:
    try:
        a = ticker.income_stmt
    except Exception as exc:  # noqa: BLE001
        logger.info("income_stmt %s failed: %s", getattr(ticker, "ticker", "?"), exc)
        return None
    row = _first_available_row(a, ANNUAL_EPS_KEYS)
    if row is None or len(row) < 4:
        return None
    try:
        latest = float(row.iloc[0])
        three_years_ago = float(row.iloc[3])
    except (ValueError, TypeError):
        return None
    if pd.isna(latest) or pd.isna(three_years_ago):
        return None
    # CAGR is only meaningful when both endpoints are positive. If either is
    # non-positive (company lost money in one of the two periods), skip — the
    # ratio would be negative and the fractional exponent produces a complex
    # number in Python (e.g. `(-1) ** (1/3)` returns 0.5 + 0.87j).
    if latest <= 0 or three_years_ago <= 0:
        return None
    try:
        cagr = (latest / three_years_ago) ** (1 / 3) - 1.0
    except (ValueError, ZeroDivisionError):
        return None
    return round(cagr * 100.0, 2)
