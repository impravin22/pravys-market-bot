"""Daily MarketSmith India replica — structured snapshot for the post-close report.

Runs after NSE close (15:30 IST) and emits a JSON blob covering:

- Market pulse (overall phase + rolling distribution-day count)
- Today's market action for Nifty + Sensex (OHLC, breadth, volume delta, vs 21/50 DMA)
- Top 5 gainers / losers in Nifty 50
- Sector-by-sector performance with directional classification
- Industry-group approximation (best-effort — uses sector indices, not the full
  proprietary 197 groups)
- Buy watchlist (top CAN SLIM scorers from the screener)
- Key news bullets via Gemini grounded search

The output is consumed by the ``marketsmith-daily-replica`` RemoteTrigger,
which spins it into a MarketSmith-voice narrative and ships to Telegram.

Usage::

    uv run python -m jobs.marketsmith_data > /tmp/snapshot.json
"""

from __future__ import annotations

import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from core.canslim import CanslimScore, phase_label
from core.config import load_config
from core.distribution_days import DistributionDayTracker
from core.gemini_client import GeminiClient
from core.nse_data import (
    NIFTY_TICKER,
    SENSEX_TICKER,
    fetch_history,
    is_trading_day,
    nse_holidays,
    today_in_market,
)
from core.screener import run_screener
from core.sector_indices import fetch_sector_snapshots

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("marketsmith_data")

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


@dataclass(frozen=True)
class IndexAction:
    label: str
    last_close: float
    open_: float
    high: float
    low: float
    prev_close: float
    change_pct: float
    volume: float
    prev_volume: float
    volume_change_pct: float
    vs_21dma_pct: float | None
    vs_50dma_pct: float | None


@dataclass(frozen=True)
class StockMover:
    symbol: str
    change_pct: float


def _nifty50_symbols() -> list[str]:
    """Fetch Nifty 50 constituents; fall back to a hardcoded list if nselib fails."""
    try:
        from nselib.capital_market.capital_market_data import (  # noqa: PLC0415
            nifty50_equity_list,
        )

        df = nifty50_equity_list()
        if df is not None and not df.empty and "Symbol" in df.columns:
            return [f"{s.strip()}.NS" for s in df["Symbol"].dropna().astype(str)]
    except Exception as exc:  # noqa: BLE001
        logger.warning("nifty50 list fetch failed, using fallback: %s", exc)
    return [f"{s}.NS" for s in NIFTY_50_FALLBACK]


def _index_action(label: str, symbol: str) -> IndexAction | None:
    hist = fetch_history(symbol, period="1y")
    if hist is None:
        return None
    df = hist.history.dropna(subset=["Close"])
    if len(df) < 2:
        return None
    last_row = df.iloc[-1]
    prev_row = df.iloc[-2]
    last_close = float(last_row["Close"])
    prev_close = float(prev_row["Close"])
    change_pct = (last_close / prev_close - 1.0) * 100.0 if prev_close else 0.0
    volume = float(last_row.get("Volume") or 0)
    prev_volume = float(prev_row.get("Volume") or 0)
    vol_change_pct = (volume / prev_volume - 1.0) * 100.0 if prev_volume else 0.0

    closes = df["Close"]
    ma21 = float(closes.tail(min(21, len(closes))).mean()) if len(closes) >= 21 else None
    ma50 = float(closes.tail(min(50, len(closes))).mean()) if len(closes) >= 50 else None
    vs_21 = (last_close / ma21 - 1.0) * 100.0 if ma21 else None
    vs_50 = (last_close / ma50 - 1.0) * 100.0 if ma50 else None

    return IndexAction(
        label=label,
        last_close=last_close,
        open_=float(last_row.get("Open") or last_close),
        high=float(last_row.get("High") or last_close),
        low=float(last_row.get("Low") or last_close),
        prev_close=prev_close,
        change_pct=change_pct,
        volume=volume,
        prev_volume=prev_volume,
        volume_change_pct=vol_change_pct,
        vs_21dma_pct=vs_21,
        vs_50dma_pct=vs_50,
    )


def _movers(symbols: list[str], parallelism: int = 12) -> tuple[list[StockMover], int, int]:
    """Fetch today's % change for each symbol; return movers + breadth counts."""
    all_movers: list[StockMover] = []
    advances = 0
    declines = 0

    def _one(sym: str) -> StockMover | None:
        hist = fetch_history(sym, period="5d")
        if hist is None:
            return None
        closes = hist.history["Close"].dropna()
        if len(closes) < 2:
            return None
        change_pct = (closes.iloc[-1] / closes.iloc[-2] - 1.0) * 100.0
        clean_symbol = sym.removesuffix(".NS")
        return StockMover(symbol=clean_symbol, change_pct=float(change_pct))

    with ThreadPoolExecutor(max_workers=parallelism) as pool:
        for fut in as_completed(pool.submit(_one, s) for s in symbols):
            mover = fut.result()
            if mover is None:
                continue
            all_movers.append(mover)
            if mover.change_pct > 0:
                advances += 1
            elif mover.change_pct < 0:
                declines += 1
    return all_movers, advances, declines


def _build_buy_watchlist(
    scores: list[CanslimScore], min_binary: int = 6, top_n: int = 8
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s in scores:
        if s.binary_score < min_binary:
            continue
        out.append(
            {
                "symbol": s.symbol.removesuffix(".NS"),
                "binary_score": s.binary_score,
                "passed_letters": s.passed_codes,
                "rs_note": s.letters["L"].note,
                "eps_note": s.letters["C"].note,
            }
        )
        if len(out) >= top_n:
            break
    return out


def _fetch_news(gemini: GeminiClient) -> list[str]:
    """Pull 5 key news bullets via Gemini grounded search."""
    prompt = (
        "List exactly 5 key news items or earnings updates from today (post-NSE-close) "
        "that affected the Indian stock market. One bullet per line, prefixed with '- '. "
        "Focus on large-cap stocks, M&A, quarterly results, RBI/SEBI announcements, or "
        "macro events. No preamble, no closing remarks — just five hyphen bullets."
    )
    try:
        text = gemini.generate_commentary(prompt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gemini news fetch failed: %s", exc)
        return []
    bullets: list[str] = []
    for line in (text or "").splitlines():
        cleaned = line.strip().lstrip("-•* ").strip()
        if cleaned:
            bullets.append(cleaned)
    return bullets[:5]


def _serialise_index(action: IndexAction | None) -> dict[str, Any] | None:
    if action is None:
        return None
    payload = asdict(action)
    payload["open"] = payload.pop("open_")
    return payload


def build_snapshot(*, today: date | None = None, force: bool = False) -> dict[str, Any]:
    today = today or today_in_market()
    holidays = nse_holidays()
    if not force and not is_trading_day(today, holidays=holidays):
        return {"skipped": True, "reason": "non-trading-day", "date": today.isoformat()}

    config = load_config()

    nifty = _index_action("Nifty 50", NIFTY_TICKER)
    sensex = _index_action("Sensex", SENSEX_TICKER)

    nifty_change = nifty.change_pct if nifty else 0.0
    nifty_vol_change = nifty.volume_change_pct if nifty else 0.0

    tracker = DistributionDayTracker(
        redis_url=os.environ["UPSTASH_REDIS_REST_URL"],
        redis_token=os.environ["UPSTASH_REDIS_REST_TOKEN"],
    )
    dd_result = tracker.record_today(
        today=today,
        nifty_change_pct=nifty_change,
        volume_change_pct=nifty_vol_change,
    )

    nifty50 = _nifty50_symbols()
    movers, advances, declines = _movers(nifty50)
    movers.sort(key=lambda m: m.change_pct, reverse=True)
    top_gainers = [asdict(m) for m in movers[:5]]
    top_losers = [asdict(m) for m in sorted(movers, key=lambda m: m.change_pct)[:5]]

    sectors = fetch_sector_snapshots()
    sector_payload = [
        {
            "name": s.name,
            "change_pct": s.change_pct,
            "direction": s.direction,
        }
        for s in sectors
    ]

    screener_result = run_screener(min_binary=5)
    if screener_result is None:
        regime_phase = "unknown"
        buy_watchlist: list[dict[str, Any]] = []
    else:
        regime_phase = screener_result.regime.phase
        buy_watchlist = _build_buy_watchlist(screener_result.scored)

    gemini = GeminiClient(
        api_key=config.google.api_key,
        model=config.google.model,
        search_api_key=config.google.search_api_key,
        cse_id=config.google.cse_id,
    )
    news = _fetch_news(gemini)

    return {
        "skipped": False,
        "date": today.isoformat(),
        "as_of_ist": datetime.now(tz=ZoneInfo("Asia/Kolkata")).strftime("%d/%m/%Y %H:%M"),
        "market_pulse": {
            "phase": regime_phase,
            "phase_label": phase_label(regime_phase),
            "distribution_days_active": dd_result.active_count,
            "today_was_distribution_day": dd_result.is_distribution_day,
        },
        "nifty_action": _serialise_index(nifty),
        "sensex_action": _serialise_index(sensex),
        "nifty50_breadth": {"advances": advances, "declines": declines},
        "top_gainers": top_gainers,
        "top_losers": top_losers,
        "sector_performance": sector_payload,
        "buy_watchlist": buy_watchlist,
        "key_news": news,
    }


def main() -> int:
    force = os.getenv("FORCE_RUN", "false").lower() == "true"
    snapshot = build_snapshot(force=force)
    json.dump(snapshot, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
