"""Compute today's top picks and store the snapshot in Upstash for /picks.

Manual run::

    set -a; source .env; set +a
    uv run python -m jobs.daily_picks_job

The Telegram ``/picks`` command reads the cached snapshot back. A
follow-up PR will trigger this from the morning_pulse cron; today it's
a stand-alone entrypoint so the user can seed the cache for live tests.

Universe: Nifty 50 (≈50 symbols) keeps the run under 60 seconds on a
laptop. Move to Nifty 500 once a real cron is wired.
"""

from __future__ import annotations

import logging
import os
import sys

import httpx

from bot.redis_store import RedisConfig, RedisStore
from core.canslim import StockFundamentals
from core.daily_picks import daily_picks
from core.data.screener_cache import ScreenerCache
from core.data.screener_in import enrich_fundamentals_with_snapshot, fetch_snapshot
from core.fundamentals import enrich_with_earnings, fundamentals_from_history
from core.nse_data import fetch_history, fetch_nifty
from core.picks_cache import PicksCache
from core.rs_rating import ReturnPoint, compute_12m_return, rank_by_return
from core.screener import detect_market_regime
from core.strategies import all_strategies

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("daily_picks_job")

NIFTY_50_FALLBACK = (
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


def _load_universe() -> list[str]:
    return [f"{s}.NS" for s in NIFTY_50_FALLBACK]


def _compute_6m_momentum(history) -> float | None:
    """Return percent change between the close ~126 sessions ago and the latest close."""
    closes = history.history["Close"].dropna()
    if len(closes) < 130:
        return None
    six_months_ago = float(closes.iloc[-126])
    last_close = float(closes.iloc[-1])
    if six_months_ago <= 0:
        return None
    return round((last_close / six_months_ago - 1.0) * 100.0, 2)


def _build_fundamentals(
    symbols: list[str],
    *,
    screener_cache: ScreenerCache,
    rs_ratings: dict[str, float],
) -> list[StockFundamentals]:
    out: list[StockFundamentals] = []
    for sym in symbols:
        history = fetch_history(sym, period="1y")
        if history is None:
            continue
        base = fundamentals_from_history(sym, history)
        with_earnings = enrich_with_earnings(base)
        with_rs = StockFundamentals(
            symbol=with_earnings.symbol,
            last_close=with_earnings.last_close,
            high_52w=with_earnings.high_52w,
            low_52w=with_earnings.low_52w,
            avg_vol_50d=with_earnings.avg_vol_50d,
            last_volume=with_earnings.last_volume,
            quarterly_eps_yoy_pct=with_earnings.quarterly_eps_yoy_pct,
            annual_eps_3y_cagr_pct=with_earnings.annual_eps_3y_cagr_pct,
            rs_rating=rs_ratings.get(sym),
            fii_dii_5d_net_positive=None,  # phase-1 skip
            momentum_6m_pct=_compute_6m_momentum(history),
        )
        snapshot = screener_cache.get_or_fetch(sym)
        out.append(enrich_fundamentals_with_snapshot(with_rs, snapshot))
    return out


def main() -> int:
    redis_config = RedisConfig.from_env()
    if redis_config is None:
        logger.error("Redis env vars missing — cannot persist picks.")
        return 1
    redis = RedisStore(redis_config)

    nifty_history = fetch_nifty()
    if nifty_history is None:
        logger.error("Nifty history unavailable — aborting")
        return 1
    regime = detect_market_regime(nifty_history)
    logger.info("market regime: %s", regime.phase)

    universe = _load_universe()
    logger.info("scoring universe: %d symbols", len(universe))

    # Compute relative strength across the universe first (price-only path).
    returns: list[ReturnPoint] = []
    histories: dict[str, object] = {}
    for sym in universe:
        h = fetch_history(sym, period="1y")
        if h is None:
            continue
        histories[sym] = h
        r = compute_12m_return(h.history["Close"].dropna().tolist())
        if r is not None:
            returns.append(ReturnPoint(symbol=sym, total_return=r))
    rs_ratings = rank_by_return(returns)

    # screener.in cache uses an httpx Client; share one across the run.
    with httpx.Client(timeout=10.0) as http:

        def _fetcher(sym: str):
            return fetch_snapshot(sym, http_client=http)

        screener_cache = ScreenerCache(redis=redis, fetcher=_fetcher)
        fundamentals = _build_fundamentals(
            universe, screener_cache=screener_cache, rs_ratings=rs_ratings
        )

    picks = daily_picks(
        fundamentals,
        regime,
        all_strategies(),
        top_n=int(os.getenv("DAILY_PICKS_TOP_N", "5")),
        min_composite=float(os.getenv("DAILY_PICKS_MIN_COMPOSITE", "60")),
    )
    logger.info("picks computed: %d", len(picks))
    PicksCache(redis=redis).write(picks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
