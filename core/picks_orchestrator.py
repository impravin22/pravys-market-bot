"""Reusable daily-picks orchestrator — runs the full panel over a universe.

Both `jobs/daily_picks_job.py` (manual) and `jobs/morning_pulse.py`
(cron) call into here. Extracted so the panel logic stays in one
place; jobs just sequence I/O.
"""

from __future__ import annotations

import logging
import os

import httpx

from bot.redis_store import RedisStore
from core.canslim import StockFundamentals
from core.daily_picks import Pick, daily_picks
from core.data.screener_cache import ScreenerCache
from core.data.screener_in import enrich_fundamentals_with_snapshot, fetch_snapshot
from core.fundamentals import enrich_with_earnings, fundamentals_from_history
from core.nse_data import fetch_history, fetch_nifty
from core.picks_cache import PicksCache
from core.rs_rating import ReturnPoint, compute_12m_return, rank_by_return
from core.screener import detect_market_regime
from core.strategies import all_strategies

logger = logging.getLogger(__name__)

NIFTY_50 = (
    "RELIANCE",
    "TCS",
    "HDFCBANK",
    "BHARTIARTL",
    "ICICIBANK",
    "SBIN",
    "INFY",
    "HINDUNILVR",
    "ITC",
    "LT",
    "BAJFINANCE",
    "KOTAKBANK",
    "AXISBANK",
    "MARUTI",
    "M&M",
    "HCLTECH",
    "SUNPHARMA",
    "TITAN",
    "WIPRO",
    "NTPC",
    "TATAMOTORS",
    "ULTRACEMCO",
    "ONGC",
    "POWERGRID",
    "ASIANPAINT",
    "ADANIENT",
    "JSWSTEEL",
    "ADANIPORTS",
    "TATASTEEL",
    "TECHM",
    "BAJAJFINSV",
    "NESTLEIND",
    "CIPLA",
    "DRREDDY",
    "GRASIM",
    "INDUSINDBK",
    "HEROMOTOCO",
    "BAJAJ-AUTO",
    "SHRIRAMFIN",
    "COALINDIA",
    "EICHERMOT",
    "DIVISLAB",
    "BPCL",
    "BRITANNIA",
    "SBILIFE",
    "HDFCLIFE",
    "TATACONSUM",
    "TRENT",
    "APOLLOHOSP",
    "JIOFIN",
)


def default_universe() -> list[str]:
    return [f"{s}.NS" for s in NIFTY_50]


def compute_6m_momentum_pct(history) -> float | None:
    """Return percent change from ~126 sessions ago to the latest close."""
    closes = history.history["Close"].dropna()
    if len(closes) < 130:
        return None
    six_months_ago = float(closes.iloc[-126])
    last = float(closes.iloc[-1])
    if six_months_ago <= 0:
        return None
    return round((last / six_months_ago - 1.0) * 100.0, 2)


def compute_picks(
    *,
    redis: RedisStore | None,
    universe: list[str] | None = None,
    top_n: int | None = None,
    min_composite: float | None = None,
    write_cache: bool = True,
) -> list[Pick]:
    """Run the full panel and return ranked picks. Writes to PicksCache by default."""
    nifty_history = fetch_nifty()
    if nifty_history is None:
        logger.warning("Nifty history unavailable — picks computation aborted")
        return []
    regime = detect_market_regime(nifty_history)
    logger.info("market regime: %s", regime.phase)

    universe = universe or default_universe()
    logger.info("scoring universe: %d symbols", len(universe))

    histories: dict[str, object] = {}
    returns: list[ReturnPoint] = []
    for sym in universe:
        h = fetch_history(sym, period="1y")
        if h is None:
            continue
        histories[sym] = h
        r = compute_12m_return(h.history["Close"].dropna().tolist())
        if r is not None:
            returns.append(ReturnPoint(symbol=sym, total_return=r))
    rs_ratings = rank_by_return(returns)

    with httpx.Client(timeout=10.0) as http:
        screener_cache = ScreenerCache(
            redis=redis, fetcher=lambda s: fetch_snapshot(s, http_client=http)
        )
        fundamentals: list[StockFundamentals] = []
        for sym, history in histories.items():
            base = fundamentals_from_history(sym, history)
            with_earn = enrich_with_earnings(base)
            with_rs = StockFundamentals(
                symbol=with_earn.symbol,
                last_close=with_earn.last_close,
                high_52w=with_earn.high_52w,
                low_52w=with_earn.low_52w,
                avg_vol_50d=with_earn.avg_vol_50d,
                last_volume=with_earn.last_volume,
                quarterly_eps_yoy_pct=with_earn.quarterly_eps_yoy_pct,
                annual_eps_3y_cagr_pct=with_earn.annual_eps_3y_cagr_pct,
                rs_rating=rs_ratings.get(sym),
                fii_dii_5d_net_positive=None,
                momentum_6m_pct=compute_6m_momentum_pct(history),
            )
            snapshot = screener_cache.get_or_fetch(sym)
            fundamentals.append(enrich_fundamentals_with_snapshot(with_rs, snapshot))

    picks = daily_picks(
        fundamentals,
        regime,
        all_strategies(),
        top_n=top_n if top_n is not None else int(os.getenv("DAILY_PICKS_TOP_N", "5")),
        min_composite=(
            min_composite
            if min_composite is not None
            else float(os.getenv("DAILY_PICKS_MIN_COMPOSITE", "60"))
        ),
    )
    logger.info("picks computed: %d", len(picks))

    if write_cache and redis is not None:
        PicksCache(redis=redis).write(picks)
    return picks
