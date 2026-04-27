"""Morning market pulse — runs 03:00 UTC Mon–Fri (11:00 TPE / 08:30 IST)."""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from bot.redis_store import RedisConfig, RedisStore
from core.canslim import CanslimScore
from core.config import load_config
from core.digest_builder import IndexSnapshot, build_morning_pulse
from core.digest_extras import format_picks_section
from core.gemini_client import GeminiClient
from core.nse_data import (
    BANK_NIFTY_TICKER,
    INDIA_VIX_TICKER,
    NIFTY_TICKER,
    SENSEX_TICKER,
    fetch_commodity_quotes,
    fetch_history,
    is_trading_day,
    nse_holidays,
    today_in_market,
)
from core.picks_orchestrator import compute_picks
from core.screener import run_screener
from core.telegram_client import TelegramClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("morning_pulse")


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


def _gather_indices() -> list[IndexSnapshot]:
    pairs = (
        ("Nifty 50", NIFTY_TICKER),
        ("Sensex", SENSEX_TICKER),
        ("Bank Nifty", BANK_NIFTY_TICKER),
        ("India VIX", INDIA_VIX_TICKER),
    )
    out = []
    for label, sym in pairs:
        snap = _index_snapshot(label, sym)
        if snap is not None:
            out.append(snap)
    return out


def _top_n(scored: list[CanslimScore], n: int = 10) -> list[CanslimScore]:
    return scored[:n]


def main() -> int:
    config = load_config()
    today = today_in_market()
    force_run = os.getenv("FORCE_RUN", "false").lower() == "true"
    if not force_run and not is_trading_day(today, holidays=nse_holidays()):
        logger.info("%s is not an NSE trading day — skipping morning pulse", today)
        return 0
    if force_run:
        logger.info(
            "FORCE_RUN is set — running pulse even though %s may not be a trading day", today
        )

    indices = _gather_indices()
    commodities = fetch_commodity_quotes()
    result = run_screener(min_binary=5)
    if result is None:
        logger.error("Screener returned no result — aborting")
        return 1

    gemini = GeminiClient(
        api_key=config.google.api_key,
        model=config.google.model,
        search_api_key=config.google.search_api_key,
        cse_id=config.google.cse_id,
    )
    global_cues_prompt = (
        "In 2 short sentences, summarise overnight global market cues likely to drive the "
        "Indian market open today (Nifty and Sensex). Focus on US close, Asian open, "
        "commodities, and USD/INR. Be factual and neutral."
    )
    global_cues = gemini.generate_commentary(global_cues_prompt)

    now = datetime.now(tz=ZoneInfo("UTC"))
    text = build_morning_pulse(
        now=now,
        market_tz=config.market_tz,
        regime=result.regime,
        indices=indices,
        commodities=commodities,
        top_scores=_top_n(result.scored, 10),
        global_cues_commentary=global_cues,
    )

    tg = TelegramClient(config.telegram.bot_token, config.telegram.chat_id)
    tg.send_message(text)
    logger.info(
        "morning-pulse-sent | universe=%d | qualified=%d | elapsed=%.1fs",
        result.universe_size,
        len(result.scored),
        result.elapsed_seconds,
    )

    # Daily picks panel — runs the full 7-guru screen, writes to picks cache,
    # and sends a separate message so the existing morning pulse stays
    # untouched. Failure here must NOT break the pulse delivery above.
    redis_config = RedisConfig.from_env()
    if redis_config is None:
        logger.warning("Redis creds missing — skipping daily picks section")
        return 0
    redis = RedisStore(redis_config)
    try:
        picks = compute_picks(redis=redis)
    except Exception as exc:  # noqa: BLE001 — never let picks crash morning pulse
        logger.warning("daily picks computation failed: %s", exc)
        return 0

    picks_text = format_picks_section(picks)
    try:
        tg.send_message(picks_text)
        logger.info("morning-picks-sent | picks=%d", len(picks))
    except Exception as exc:  # noqa: BLE001
        logger.warning("morning picks send failed: %s", exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
