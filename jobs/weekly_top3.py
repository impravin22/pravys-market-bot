"""Weekly top-3 CAN SLIM picks — runs Sun 14:00 UTC (22:00 TPE).

Scores the full Nifty 500 + commodity ETF universe. Picks the top 3 and asks
Gemini for a 3–4 sentence rationale per pick, grounded in the last 7 days of
news via Google Custom Search.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from core.config import load_config
from core.digest_builder import build_weekly_top3
from core.gemini_client import GeminiClient
from core.screener import run_screener
from core.telegram_client import TelegramClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("weekly_top3")


def main() -> int:
    config = load_config()
    tg = TelegramClient(config.telegram.bot_token, config.telegram.chat_id)
    result = run_screener(min_binary=6)  # stricter bar for weekly picks

    # Distinguish "screener crashed" from "screener returned 0 qualified".
    # The latter is a normal weak-market outcome and the user still wants a
    # signal that the digest ran — silent skip looks like a broken bot.
    if result is None:
        logger.error("Screener unavailable — sending failure notice")
        tg.send_message(
            "⚠️ <b>Weekly Top 3</b>\n"
            "Screener was unavailable this week. Will retry next Sunday.",
        )
        return 1

    annotated: list[tuple] = []
    if not result.scored:
        logger.warning(
            "Screener returned 0 qualified at min_binary=6 (universe=%d) — "
            "sending no-picks digest",
            result.universe_size,
        )
    else:
        gemini = GeminiClient(
            api_key=config.google.api_key,
            model=config.google.model,
            search_api_key=config.google.search_api_key,
            cse_id=config.google.cse_id,
        )
        for s in result.scored[:3]:
            context_lines = [f"{code}: {s.letters[code].note}" for code in "CANSLIM"]
            context = f"CAN SLIM {s.binary_score}/7. Score detail:\n" + "\n".join(context_lines)
            rationale = gemini.summarise_with_news(s.symbol, context)
            annotated.append((s, rationale))

    # build_weekly_top3 already renders a "No stocks met the CAN SLIM bar
    # this week." line when picks is empty, so we always send a message.
    now = datetime.now(tz=ZoneInfo("UTC"))
    text = build_weekly_top3(
        now=now,
        market_tz=config.market_tz,
        picks=annotated,
    )
    tg.send_message(text)
    logger.info(
        "weekly-top3-sent | universe=%d | qualified=%d | picks=%d",
        result.universe_size,
        len(result.scored),
        len(annotated),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
