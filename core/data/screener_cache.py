"""Upstash-backed cache layer for screener.in snapshots.

Two-layer freshness model:

1. **Fresh (≤24h)** — return cached snapshot directly, no HTTP.
2. **Stale (>24h)** — try to refetch; if the fetcher returns a snapshot,
   write through. If the fetcher fails (network down, screener.in 5xx),
   surface the stale snapshot rather than ``None`` so the screener doesn't
   collapse on transient failures.

Cache miss + fetcher failure ⇒ ``None``.

Redis is optional: pass ``redis=None`` to bypass the cache entirely (useful
in dev when Upstash creds are absent).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from bot.redis_store import RedisStore
from core.data.screener_in import ScreenerSnapshot

logger = logging.getLogger(__name__)

KEY_PREFIX = "screener:"
DEFAULT_TTL_SECONDS = 60 * 60 * 24 * 7  # keep stale up to a week
DEFAULT_FRESH_AFTER = timedelta(hours=24)


def snapshot_to_dict(snap: ScreenerSnapshot) -> dict[str, Any]:
    data = asdict(snap)
    data["fetched_at"] = snap.fetched_at.isoformat()
    return data


def _snapshot_key(symbol: str) -> str:
    return f"{KEY_PREFIX}{symbol.upper()}"


class ScreenerCache:
    """Cache around a ``fetch_snapshot``-shaped fetcher."""

    def __init__(
        self,
        *,
        redis: RedisStore | None,
        fetcher: Callable[[str], ScreenerSnapshot | None],
        fresh_after: timedelta = DEFAULT_FRESH_AFTER,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._redis = redis
        self._fetcher = fetcher
        self._fresh_after = fresh_after
        self._ttl_seconds = ttl_seconds

    # -------------------- public API --------------------

    def get_or_fetch(self, symbol: str) -> ScreenerSnapshot | None:
        cached = self._read(symbol)
        if cached is not None and self._is_fresh(cached):
            return cached

        fresh = self._fetcher(symbol)
        if fresh is not None:
            self._write(symbol, fresh)
            return fresh

        # Fetcher failed; better to serve stale than nothing.
        if cached is not None:
            logger.info("screener.in fetch failed for %s, serving stale", symbol)
            return cached
        return None

    # -------------------- internals --------------------

    def _is_fresh(self, snap: ScreenerSnapshot) -> bool:
        return datetime.now(tz=UTC) - snap.fetched_at <= self._fresh_after

    def _read(self, symbol: str) -> ScreenerSnapshot | None:
        if self._redis is None:
            return None
        try:
            raw = self._redis.call("GET", _snapshot_key(symbol))
        except RuntimeError as exc:
            logger.warning("screener cache redis GET %s failed: %s", symbol, exc)
            return None
        if raw is None:
            return None
        try:
            return self._deserialise_snapshot(json.loads(raw))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("screener cache corrupt JSON for %s: %s", symbol, exc)
            return None

    def _write(self, symbol: str, snap: ScreenerSnapshot) -> None:
        if self._redis is None:
            return
        try:
            self._redis.call(
                "SET",
                _snapshot_key(symbol),
                json.dumps(snapshot_to_dict(snap)),
                "EX",
                str(self._ttl_seconds),
            )
        except RuntimeError as exc:
            logger.warning("screener cache redis SET %s failed: %s", symbol, exc)

    @staticmethod
    def _deserialise_snapshot(data: dict[str, Any]) -> ScreenerSnapshot | None:
        try:
            return ScreenerSnapshot(
                symbol=data["symbol"],
                market_cap=data.get("market_cap"),
                current_price=data.get("current_price"),
                pe_ratio=data.get("pe_ratio"),
                pb_ratio=data.get("pb_ratio"),
                book_value=data.get("book_value"),
                dividend_yield_pct=data.get("dividend_yield_pct"),
                pays_dividend=data.get("pays_dividend"),
                roe_pct=data.get("roe_pct"),
                roe_5y_avg_pct=data.get("roe_5y_avg_pct"),
                roce_pct=data.get("roce_pct"),
                debt_to_equity=data.get("debt_to_equity"),
                face_value=data.get("face_value"),
                fetched_at=datetime.fromisoformat(data["fetched_at"]),
            )
        except (KeyError, TypeError, ValueError):
            return None
