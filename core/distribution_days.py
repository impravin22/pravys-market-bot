"""Rolling 25-trading-day distribution day counter, persisted in Upstash Redis.

A "distribution day" is any session where the index closes down ≥0.2% on
volume higher than the previous session — the signature of institutional
selling. The MarketSmith methodology tracks these over a 25-session window:
3+ active distribution days warrants caution, 5–6 typically precedes a
market correction.

Keys are written under ``marketsmith:dd:YYYY-MM-DD`` with values "1" (active
distribution day) or "0" (no distribution). A 30-day TTL keeps the keyspace
bounded — older entries roll out automatically.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

import httpx

logger = logging.getLogger(__name__)

KEY_PREFIX = "marketsmith:dd:"
TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days
DEFAULT_LOOKBACK_TRADING_DAYS = 25
DEFAULT_CALENDAR_LOOKBACK_DAYS = 40  # ~25 trading sessions plus weekends/holidays buffer


@dataclass(frozen=True)
class DistributionDayResult:
    """Outcome of recording today's session."""

    is_distribution_day: bool
    nifty_change_pct: float
    volume_change_pct: float
    active_count: int  # rolling count INCLUDING today


class DistributionDayTracker:
    """Upstash-backed rolling counter. Failures degrade to ``active_count=0``."""

    def __init__(self, *, redis_url: str, redis_token: str, http_timeout: float = 5.0) -> None:
        self._url = redis_url.rstrip("/")
        self._token = redis_token
        self._timeout = http_timeout

    def _get(self, key: str) -> str | None:
        try:
            resp = httpx.get(
                f"{self._url}/get/{key}",
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            payload = resp.json()
            value = payload.get("result") if isinstance(payload, dict) else None
            return value if isinstance(value, str) else None
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("distribution_days redis GET %s failed: %s", key, exc)
            return None

    def _set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        try:
            resp = httpx.post(
                f"{self._url}/set/{key}/{value}",
                headers={"Authorization": f"Bearer {self._token}"},
                params={"EX": ttl_seconds},
                timeout=self._timeout,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("distribution_days redis SET %s failed: %s", key, exc)

    @staticmethod
    def _key_for(d: date) -> str:
        return f"{KEY_PREFIX}{d.isoformat()}"

    @staticmethod
    def is_today_distribution(
        nifty_change_pct: float, volume_change_pct: float, *, threshold_decline_pct: float = -0.2
    ) -> bool:
        """Distribution = decline ≥0.2% AND today's volume above prior session."""
        return nifty_change_pct <= threshold_decline_pct and volume_change_pct > 0.0

    def record_today(
        self,
        *,
        today: date,
        nifty_change_pct: float,
        volume_change_pct: float,
    ) -> DistributionDayResult:
        """Persist today's distribution flag and return the rolling count."""
        is_dd = self.is_today_distribution(nifty_change_pct, volume_change_pct)
        self._set(self._key_for(today), "1" if is_dd else "0", ttl_seconds=TTL_SECONDS)
        active_count = self.count_active(today=today)
        return DistributionDayResult(
            is_distribution_day=is_dd,
            nifty_change_pct=nifty_change_pct,
            volume_change_pct=volume_change_pct,
            active_count=active_count,
        )

    def count_active(
        self,
        *,
        today: date,
        lookback_trading_days: int = DEFAULT_LOOKBACK_TRADING_DAYS,
    ) -> int:
        """Sum the "1" entries across the last N trading days INCLUDING today.

        We don't know the NSE trading calendar inside Redis, so we walk back
        ``DEFAULT_CALENDAR_LOOKBACK_DAYS`` calendar days and ignore weekends.
        Entries written by ``record_today`` only exist for trading days, so
        missing keys (weekends, holidays) contribute 0 naturally.
        """
        count = 0
        seen_trading_days = 0
        cursor = today
        while seen_trading_days < lookback_trading_days and cursor >= today - timedelta(
            days=DEFAULT_CALENDAR_LOOKBACK_DAYS
        ):
            if cursor.weekday() < 5:  # Mon-Fri
                value = self._get(self._key_for(cursor))
                if value is not None:
                    seen_trading_days += 1
                    if value == "1":
                        count += 1
            cursor -= timedelta(days=1)
        return count
