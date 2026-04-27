"""Sunday weekly portfolio check-in — pings the user to log new buys.

Sent at the configured Sunday slot (the same Cloudflare-driven schedule
that fires `weekly-top3`). The message is conversational: it doesn't
require a state machine — the user just replies with one or more
`/add` commands, the existing portfolio_commands handler does the rest.

Run guard: only fires on a Sunday. ``FORCE_RUN=true`` bypasses for ad-hoc
testing.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date

from core.config import load_config
from core.nse_data import today_in_market
from core.telegram_client import TelegramClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("weekly_portfolio_prompt")

SUNDAY_WEEKDAY = 6


def should_run_today(today: date, *, force: bool = False) -> bool:
    if force:
        return True
    return today.weekday() == SUNDAY_WEEKDAY


def build_prompt_message(*, today: date) -> str:
    return (
        f"🗓 Weekly portfolio check-in ({today.isoformat()})\n"
        "\n"
        "Did you buy or sell anything this week?\n"
        "\n"
        "• Log buys: `/add SYMBOL QTY PRICE [YYYY-MM-DD]`\n"
        "  e.g. `/add RELIANCE 50 2400 2026-04-22`\n"
        "• Log sales: `/remove SYMBOL`\n"
        "• Review: `/portfolio` to list, `/sells` to check exit rules, `/picks` for new ideas\n"
        "\n"
        "Skip if nothing changed. The morning pulse will fire fresh picks Monday."
    )


def main() -> int:
    config = load_config()
    today = today_in_market()
    force_run = os.getenv("FORCE_RUN", "false").lower() == "true"
    if not should_run_today(today, force=force_run):
        logger.info("%s is not a Sunday — skipping weekly portfolio prompt", today)
        return 0

    text = build_prompt_message(today=today)
    tg = TelegramClient(config.telegram.bot_token, config.telegram.chat_id)
    tg.send_message(text)
    logger.info("weekly-portfolio-prompt-sent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
