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
    result = run_screener(min_binary=6)  # stricter bar for weekly picks
    if result is None or not result.scored:
        logger.error("Screener empty — skipping weekly top3 send")
        return 0

    gemini = GeminiClient(
        api_key=config.google.api_key,
        model=config.google.model,
        search_api_key=config.google.search_api_key,
        cse_id=config.google.cse_id,
    )
    picks = result.scored[:3]
    annotated: list[tuple] = []
    for s in picks:
        context_lines = [f"{code}: {s.letters[code].note}" for code in "CANSLIM"]
        context = f"CAN SLIM {s.binary_score}/7. Score detail:\n" + "\n".join(context_lines)
        rationale = gemini.summarise_with_news(s.symbol, context)
        annotated.append((s, rationale))

    now = datetime.now(tz=ZoneInfo("UTC"))
    text = build_weekly_top3(
        now=now,
        market_tz=config.market_tz,
        picks=annotated,
    )

    tg = TelegramClient(config.telegram.bot_token, config.telegram.chat_id)
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
