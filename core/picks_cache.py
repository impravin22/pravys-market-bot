"""Daily-picks cache — Upstash-backed JSON snapshot of the latest panel run.

The cron-driven daily-picks orchestrator writes here once a day. The
`/picks` Telegram command reads back the same payload and renders it
without recomputing — keeps the bot reply under a second.

Cache key: ``picks:latest``. TTL: 7 days (so a cron outage doesn't
silently delete the user's picks; freshness is checked per-call via
``is_fresh``).

Pick objects don't fully round-trip — verdicts and fundamentals are
heavy. We persist a *flattened summary* (symbol, composite, endorsers,
endorsement count) which is what the Telegram digest renders anyway.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from bot.redis_store import RedisStore
from core.daily_picks import Pick

logger = logging.getLogger(__name__)

CACHE_KEY = "picks:latest"
DEFAULT_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days


def _pick_to_dict(p: Pick) -> dict[str, Any]:
    return {
        "symbol": p.symbol,
        "composite_rating": p.composite_rating,
        "endorsement_count": p.endorsement_count,
        "endorsing_codes": list(p.endorsing_codes),
        "fundamentals_summary": _summary_str(p),
    }


def _summary_str(p: Pick) -> str:
    f = p.fundamentals
    parts: list[str] = []
    if f.last_close is not None:
        parts.append(f"px=₹{f.last_close:.2f}")
    if f.rs_rating is not None:
        parts.append(f"RS={f.rs_rating:.0f}")
    if f.quarterly_eps_yoy_pct is not None:
        parts.append(f"Q-EPS={f.quarterly_eps_yoy_pct:+.1f}%")
    if f.pe_ratio is not None:
        parts.append(f"P/E={f.pe_ratio:.1f}")
    if f.debt_to_equity is not None:
        parts.append(f"D/E={f.debt_to_equity:.2f}")
    return " · ".join(parts) if parts else ""


def picks_to_payload(picks: list[Pick]) -> str:
    """Serialise picks to JSON; includes computed_at timestamp."""
    return json.dumps(
        {
            "picks": [_pick_to_dict(p) for p in picks],
            "computed_at": datetime.now(tz=UTC).isoformat(),
        }
    )


@dataclass(frozen=True)
class CachedPicks:
    """Minimal envelope returned by `PicksCache.read`."""

    picks: list[dict[str, Any]]
    computed_at: datetime


class PicksCache:
    """Read/write the latest daily-picks snapshot."""

    def __init__(
        self,
        *,
        redis: RedisStore | None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._redis = redis
        self._ttl_seconds = ttl_seconds

    def write(self, picks: list[Pick]) -> None:
        if self._redis is None:
            logger.info("picks cache: redis unavailable, skipping write")
            return
        try:
            self._redis.call(
                "SET",
                CACHE_KEY,
                picks_to_payload(picks),
                "EX",
                str(self._ttl_seconds),
            )
        except RuntimeError as exc:
            logger.warning("picks cache write failed: %s", exc)

    def read(self) -> CachedPicks | None:
        if self._redis is None:
            return None
        try:
            raw = self._redis.call("GET", CACHE_KEY)
        except RuntimeError as exc:
            logger.warning("picks cache read failed: %s", exc)
            return None
        if raw is None:
            return None
        try:
            data = json.loads(raw)
            return CachedPicks(
                picks=list(data.get("picks", [])),
                computed_at=datetime.fromisoformat(data["computed_at"]),
            )
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            logger.warning("picks cache corrupt JSON: %s", exc)
            return None

    @staticmethod
    def is_fresh(cached: CachedPicks, *, max_age: timedelta) -> bool:
        return datetime.now(tz=UTC) - cached.computed_at <= max_age
