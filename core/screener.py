"""Full-universe CAN SLIM screening pipeline.

Glues data fetchers, RS rating, fundamentals enrichment, and the CAN SLIM
scorer together. Designed to run inside GitHub Actions in under 15 minutes
on a 2000-ticker universe.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import pandas as pd

from core.canslim import (
    CanslimScore,
    MarketRegime,
    StockFundamentals,
    classify_phase,
    rank_universe,
)
from core.fundamentals import enrich_with_earnings, fundamentals_from_history
from core.nse_data import (
    COMMODITY_ETFS,
    StockHistory,
    fetch_fii_dii_activity,
    fetch_history,
    fetch_nifty,
    fetch_nifty_500_symbols,
)
from core.rs_rating import ReturnPoint, compute_12m_return, rank_by_return

logger = logging.getLogger(__name__)

DEFAULT_PARALLELISM = 12


@dataclass(frozen=True)
class ScreenerResult:
    regime: MarketRegime
    scored: list[CanslimScore]
    nifty_last_close: float
    universe_size: int
    elapsed_seconds: float


def detect_market_regime(nifty_history: StockHistory) -> MarketRegime:
    closes = nifty_history.history["Close"].dropna()
    if len(closes) < 200:
        return MarketRegime(
            nifty_above_50dma=False,
            nifty_above_200dma=False,
            nifty_5d_trend_up=False,
        )
    last = float(closes.iloc[-1])
    ma50 = float(closes.tail(50).mean())
    ma200 = float(closes.tail(200).mean())
    five_day_change = (closes.iloc[-1] / closes.iloc[-5] - 1) if len(closes) >= 5 else 0
    above_50 = last > ma50
    above_200 = last > ma200
    five_up = bool(five_day_change > 0)
    return MarketRegime(
        nifty_above_50dma=above_50,
        nifty_above_200dma=above_200,
        nifty_5d_trend_up=five_up,
        phase=classify_phase(above_50dma=above_50, above_200dma=above_200, five_day_up=five_up),
    )


def _fii_dii_net_positive_5d(df: pd.DataFrame | None) -> bool | None:
    """True if combined FII + DII net flow over recent days is positive.

    nselib returns a two-row snapshot (one FII/FPI row, one DII row) with
    columns ``category``, ``date``, ``buyValue``, ``sellValue``, ``netValue``
    — the current-day endpoint only, so the "5d" window is approximate.
    """
    if df is None or df.empty:
        return None
    col = next((c for c in ("netValue", "Net_Value", "NetValue") if c in df.columns), None)
    if col is None:
        return None
    try:
        total_net = pd.to_numeric(df[col], errors="coerce").sum()
        return bool(total_net > 0)
    except (ValueError, TypeError):
        return None


def _fetch_one(symbol: str) -> tuple[str, StockHistory | None]:
    return symbol, fetch_history(symbol, period="1y")


def run_screener(
    *,
    universe: list[str] | None = None,
    min_binary: int = 5,
    parallelism: int = DEFAULT_PARALLELISM,
) -> ScreenerResult | None:
    """Execute the full screener. Returns None if market data is unavailable.

    Tests should prefer passing a small `universe` and running synchronously.
    """
    started = time.time()

    nifty = fetch_nifty()
    if nifty is None:
        logger.error("Nifty history unavailable — aborting screener run")
        return None
    regime = detect_market_regime(nifty)
    nifty_close = float(nifty.history["Close"].iloc[-1])

    universe = universe or (fetch_nifty_500_symbols() + list(COMMODITY_ETFS))
    if not universe:
        logger.error("Empty screening universe — aborting")
        return None

    # Fetch histories in parallel.
    histories: dict[str, StockHistory] = {}
    with ThreadPoolExecutor(max_workers=parallelism) as pool:
        for future in as_completed(pool.submit(_fetch_one, s) for s in universe):
            sym, hist = future.result()
            if hist is not None:
                histories[sym] = hist

    # Compute RS across those with data.
    returns = []
    for sym, hist in histories.items():
        r = compute_12m_return(hist.history["Close"].dropna().tolist())
        if r is not None:
            returns.append(ReturnPoint(symbol=sym, total_return=r))
    rs_ratings = rank_by_return(returns)

    # FII/DII context once.
    fii_positive = _fii_dii_net_positive_5d(fetch_fii_dii_activity())

    # Build per-stock fundamentals.
    records: list[StockFundamentals] = []
    for sym, hist in histories.items():
        base = fundamentals_from_history(sym, hist)
        base_with_earnings = enrich_with_earnings(base)
        enriched = StockFundamentals(
            symbol=base_with_earnings.symbol,
            last_close=base_with_earnings.last_close,
            high_52w=base_with_earnings.high_52w,
            low_52w=base_with_earnings.low_52w,
            avg_vol_50d=base_with_earnings.avg_vol_50d,
            last_volume=base_with_earnings.last_volume,
            quarterly_eps_yoy_pct=base_with_earnings.quarterly_eps_yoy_pct,
            annual_eps_3y_cagr_pct=base_with_earnings.annual_eps_3y_cagr_pct,
            rs_rating=rs_ratings.get(sym),
            fii_dii_5d_net_positive=fii_positive,
        )
        records.append(enriched)

    scored = rank_universe(records, regime, min_binary=min_binary)
    elapsed = time.time() - started
    logger.info(
        "Screener finished: %d/%d qualified in %.1fs",
        len(scored),
        len(records),
        elapsed,
    )
    return ScreenerResult(
        regime=regime,
        scored=scored,
        nifty_last_close=nifty_close,
        universe_size=len(records),
        elapsed_seconds=elapsed,
    )
