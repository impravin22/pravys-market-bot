"""Weekly market recap — runs Sat 14:00 UTC (19:30 IST / 22:00 TPE).

Aggregates the trading week (~5 sessions):
  • Index snapshots with week-over-week change
  • Commodity & FX with 5-session change
  • Top gainers/losers of the week across Nifty 500 (first 200 constituents)
  • Gemini narrative for the week

Runs once per week on Saturday. Dispatched by the Cloudflare Worker's
scheduled() handler to avoid GitHub cron drift.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from core.config import load_config
from core.digest_builder import DailyMover, IndexSnapshot, build_weekly_recap
from core.gemini_client import GeminiClient
from core.nse_data import (
    BANK_NIFTY_TICKER,
    COMMODITY_TRACKERS,
    NIFTY_TICKER,
    SENSEX_TICKER,
    Quote,
    fetch_history,
    fetch_nifty_500_symbols,
)
from core.telegram_client import TelegramClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("weekly_recap")

TOP_MOVER_LIMIT = 5
# Trading week window — 5 sessions back. Data pulled with period="1mo" so
# weekends/holidays don't shrink the window below 5 bars.
WEEK_SESSIONS = 5


def _weekly_index_snapshot(label: str, symbol: str) -> IndexSnapshot | None:
    hist = fetch_history(symbol, period="1mo")
    if hist is None or len(hist.history) < WEEK_SESSIONS + 1:
        return None
    closes = hist.history["Close"].dropna()
    if len(closes) < WEEK_SESSIONS + 1:
        return None
    last = float(closes.iloc[-1])
    week_ago = float(closes.iloc[-(WEEK_SESSIONS + 1)])
    change_pct = (last / week_ago - 1.0) * 100.0 if week_ago else 0.0
    return IndexSnapshot(label=label, last=last, change_pct=change_pct)


def _weekly_mover(symbol: str) -> DailyMover | None:
    hist = fetch_history(symbol, period="2mo")
    if hist is None or hist.history.empty:
        return None
    df = hist.history
    closes = df["Close"].dropna()
    vols = df["Volume"].dropna()
    if len(closes) < WEEK_SESSIONS + 1 or len(vols) < 50:
        return None
    last = float(closes.iloc[-1])
    week_ago = float(closes.iloc[-(WEEK_SESSIONS + 1)])
    if week_ago == 0:
        return None
    change = (last / week_ago - 1.0) * 100.0
    avg_vol = float(vols.tail(50).mean())
    # Week's average daily volume relative to 50-day average.
    week_avg_vol = float(vols.tail(WEEK_SESSIONS).mean())
    vol_mult = float(week_avg_vol / avg_vol) if avg_vol else 0.0
    return DailyMover(symbol=symbol, change_pct=change, volume_multiple=vol_mult)


def _top_weekly_gainers_losers() -> tuple[list[DailyMover], list[DailyMover]]:
    universe = fetch_nifty_500_symbols()
    if not universe:
        # Silent empty-list returns from upstream cause the digest to render
        # "(data unavailable)" with no operator-visible signal. Surface it.
        logger.warning(
            "weekly_recap: fetch_nifty_500_symbols returned empty — movers section will be blank"
        )
        return [], []
    movers: list[DailyMover] = []
    for sym in universe[:200]:
        m = _weekly_mover(sym)
        if m is not None:
            movers.append(m)
    gainers = sorted(movers, key=lambda m: m.change_pct, reverse=True)[:TOP_MOVER_LIMIT]
    losers = sorted(movers, key=lambda m: m.change_pct)[:TOP_MOVER_LIMIT]
    return gainers, losers


def _weekly_commodity_quotes() -> list[Quote]:
    """Commodity/FX quotes with 5-session change.

    `core.nse_data.fetch_commodity_quotes` only gives 1-day change; here we
    need week-over-week, so build Quote directly with `prev_close` set to the
    close 5 sessions back.
    """
    try:
        import yfinance as yf  # noqa: PLC0415
    except ImportError:
        return []

    out: list[Quote] = []
    for label, symbol in COMMODITY_TRACKERS.items():
        try:
            hist = yf.Ticker(symbol).history(period="1mo", interval="1d", auto_adjust=False)
            if hist is None or hist.empty or len(hist) < WEEK_SESSIONS + 1:
                continue
            last = float(hist["Close"].iloc[-1])
            week_ago = float(hist["Close"].iloc[-(WEEK_SESSIONS + 1)])
            out.append(Quote(symbol=symbol, label=label, last=last, prev_close=week_ago))
        except Exception as exc:  # noqa: BLE001
            logger.warning("weekly commodity %s failed: %s", symbol, exc)
    return out


def main() -> int:
    config = load_config()

    indices = [
        ix
        for ix in (
            _weekly_index_snapshot("Nifty 50", NIFTY_TICKER),
            _weekly_index_snapshot("Sensex", SENSEX_TICKER),
            _weekly_index_snapshot("Bank Nifty", BANK_NIFTY_TICKER),
        )
        if ix is not None
    ]
    commodities = _weekly_commodity_quotes()
    gainers, losers = _top_weekly_gainers_losers()

    gemini = GeminiClient(
        api_key=config.google.api_key,
        model=config.google.model,
        search_api_key=config.google.search_api_key,
        cse_id=config.google.cse_id,
    )
    narrative_prompt = (
        "In 3–4 short sentences, summarise the Indian equity market's trading week. "
        "Cover index direction, sector leadership/laggards, breadth, and any macro "
        "catalysts (rates, FII flows, earnings). Factual and neutral."
    )
    narrative = gemini.generate_commentary(narrative_prompt)

    now = datetime.now(tz=ZoneInfo("UTC"))
    text = build_weekly_recap(
        now=now,
        market_tz=config.market_tz,
        indices=indices,
        commodities=commodities,
        top_gainers=gainers,
        top_losers=losers,
        narrative=narrative,
    )

    tg = TelegramClient(config.telegram.bot_token, config.telegram.chat_id)
    result = tg.send_message(text)
    if not result.ok:
        # Telegram returned 200 with `{"ok": false, ...}` — usually means the
        # message was rejected (too long, bad parse_mode, invalid chat). Fail
        # the workflow so it surfaces in GitHub Actions instead of looking
        # green while no digest landed.
        logger.error("weekly-recap: Telegram rejected message (ok=False)")
        return 1
    logger.info(
        "weekly-recap-sent | gainers=%d | losers=%d | commodities=%d",
        len(gainers),
        len(losers),
        len(commodities),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
