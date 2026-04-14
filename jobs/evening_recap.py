"""Evening market recap — runs 10:15 UTC Mon–Fri (18:15 TPE / 15:45 IST, 15 min after NSE close)."""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from core.config import load_config
from core.digest_builder import DailyMover, IndexSnapshot, build_evening_recap
from core.gemini_client import GeminiClient
from core.nse_data import (
    BANK_NIFTY_TICKER,
    NIFTY_TICKER,
    SENSEX_TICKER,
    fetch_commodity_quotes,
    fetch_history,
    fetch_nifty_500_symbols,
    is_trading_day,
    nse_holidays,
    today_in_market,
)
from core.telegram_client import TelegramClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("evening_recap")

TOP_MOVER_LIMIT = 5


def _index_snapshot(label: str, symbol: str) -> IndexSnapshot | None:
    hist = fetch_history(symbol, period="5d")
    if hist is None or len(hist.history) < 2:
        return None
    closes = hist.history["Close"].dropna()
    if len(closes) < 2:
        return None
    last = float(closes.iloc[-1])
    prev = float(closes.iloc[-2])
    change_pct = (last / prev - 1.0) * 100.0 if prev else 0.0
    return IndexSnapshot(label=label, last=last, change_pct=change_pct)


def _daily_mover(symbol: str) -> DailyMover | None:
    hist = fetch_history(symbol, period="3mo")
    if hist is None or hist.history.empty:
        return None
    df = hist.history
    closes = df["Close"].dropna()
    vols = df["Volume"].dropna()
    if len(closes) < 2 or len(vols) < 50:
        return None
    last = float(closes.iloc[-1])
    prev = float(closes.iloc[-2])
    if prev == 0:
        return None
    change = (last / prev - 1.0) * 100.0
    avg_vol = float(vols.tail(50).mean())
    vol_mult = float(vols.iloc[-1] / avg_vol) if avg_vol else 0.0
    return DailyMover(symbol=symbol, change_pct=change, volume_multiple=vol_mult)


def _top_gainers_losers() -> tuple[list[DailyMover], list[DailyMover]]:
    universe = fetch_nifty_500_symbols()
    if not universe:
        return [], []
    movers: list[DailyMover] = []
    # Cap to first 200 to keep runtime tight — Nifty 500's gainers leaderboard
    # is dominated by the larger constituents anyway.
    for sym in universe[:200]:
        m = _daily_mover(sym)
        if m is not None:
            movers.append(m)
    gainers = sorted(movers, key=lambda m: m.change_pct, reverse=True)[:TOP_MOVER_LIMIT]
    losers = sorted(movers, key=lambda m: m.change_pct)[:TOP_MOVER_LIMIT]
    return gainers, losers


def main() -> int:
    config = load_config()
    today = today_in_market()
    if not is_trading_day(today, holidays=nse_holidays()):
        logger.info("%s is not an NSE trading day — skipping evening recap", today)
        return 0

    indices = [
        ix
        for ix in (
            _index_snapshot("Nifty 50", NIFTY_TICKER),
            _index_snapshot("Sensex", SENSEX_TICKER),
            _index_snapshot("Bank Nifty", BANK_NIFTY_TICKER),
        )
        if ix is not None
    ]
    commodities = fetch_commodity_quotes()
    gainers, losers = _top_gainers_losers()

    gemini = GeminiClient(
        api_key=config.google.api_key,
        model=config.google.model,
        search_api_key=config.google.search_api_key,
        cse_id=config.google.cse_id,
    )
    narrative_prompt = (
        "In 2–3 short sentences, summarise today's Indian market session. Note sector "
        "leadership, breadth, and any macro triggers. Be factual and neutral."
    )
    narrative = gemini.generate_commentary(narrative_prompt)

    now = datetime.now(tz=ZoneInfo("UTC"))
    text = build_evening_recap(
        now=now,
        market_tz=config.market_tz,
        indices=indices,
        commodities=commodities,
        top_gainers=gainers,
        top_losers=losers,
        watchlist_actions=[],
        narrative=narrative,
    )

    tg = TelegramClient(config.telegram.bot_token, config.telegram.chat_id)
    tg.send_message(text)
    logger.info(
        "evening-recap-sent | gainers=%d | losers=%d | commodities=%d",
        len(gainers),
        len(losers),
        len(commodities),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
