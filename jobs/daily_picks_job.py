"""Compute today's top picks and store the snapshot in Upstash for /picks.

Manual run::

    set -a; source .env; set +a
    uv run python -m jobs.daily_picks_job

The same logic is also invoked from `morning_pulse.py` so that the
cron-driven flow refreshes the cache automatically. Keeping this
stand-alone entrypoint helps with one-off seeds and ad-hoc testing.
"""

from __future__ import annotations

import logging
import sys

from bot.redis_store import RedisConfig, RedisStore
from core.picks_orchestrator import compute_picks

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("daily_picks_job")


def main() -> int:
    redis_config = RedisConfig.from_env()
    if redis_config is None:
        logger.error("Redis env vars missing — cannot persist picks.")
        return 1
    picks = compute_picks(redis=RedisStore(redis_config))
    logger.info("daily-picks-job done | picks=%d", len(picks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
