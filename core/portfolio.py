"""Portfolio model + Upstash-backed store.

A `Portfolio` belongs to one Telegram chat (multi-user safe). Holdings
are stored as JSON under ``portfolio:{hashed_chat_id}`` so the raw
chat_id never lands in Redis — same discipline used for chat history
and rate limiting.

Stop-loss defaults to 7% below buy price (O'Neil's defensive rule). The
`source_guru` field tags which strategy surfaced the pick so a future
weekly recap can compute hit rate per guru.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, date, datetime
from typing import Any

from bot.redis_store import RedisStore, _hash_user_id

logger = logging.getLogger(__name__)

DEFAULT_STOP_LOSS_PCT = 0.07  # 7% O'Neil rule
KEY_PREFIX = "portfolio:"
PORTFOLIO_TTL_SECONDS = 60 * 60 * 24 * 365  # 1 year


@dataclass(frozen=True)
class Holding:
    """One open position. Frozen so updates produce new copies."""

    symbol: str
    qty: int
    buy_price: float
    buy_date: date
    source_guru: str | None = None
    pivot_price: float | None = None
    stop_loss: float = 0.0  # populated by __post_init__-style helper below
    target_price: float | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        # frozen=True forbids assignment, so use object.__setattr__ for the
        # one defaulted field. Only fills in when caller passed 0.0 / default.
        if self.stop_loss == 0.0:
            object.__setattr__(
                self, "stop_loss", round(self.buy_price * (1.0 - DEFAULT_STOP_LOSS_PCT), 2)
            )

    def pnl_pct(self, current_price: float) -> float:
        if self.buy_price == 0:
            return 0.0
        return round((current_price / self.buy_price - 1.0) * 100.0, 2)

    def pnl_value(self, current_price: float) -> float:
        return round((current_price - self.buy_price) * self.qty, 2)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["buy_date"] = self.buy_date.isoformat()
        return d

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Holding:
        return cls(
            symbol=data["symbol"],
            qty=int(data["qty"]),
            buy_price=float(data["buy_price"]),
            buy_date=date.fromisoformat(data["buy_date"]),
            source_guru=data.get("source_guru"),
            pivot_price=data.get("pivot_price"),
            stop_loss=float(data.get("stop_loss") or 0.0),
            target_price=data.get("target_price"),
            notes=str(data.get("notes") or ""),
        )


@dataclass(frozen=True)
class Portfolio:
    chat_id: int
    holdings: list[Holding] = field(default_factory=list)
    cash_remaining: float = 0.0
    last_updated: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    @property
    def invested_capital(self) -> float:
        return round(sum(h.qty * h.buy_price for h in self.holdings), 2)

    def market_value(self, quotes: Mapping[str, float]) -> float:
        return round(
            sum(h.qty * quotes.get(h.symbol, h.buy_price) for h in self.holdings),
            2,
        )

    def total_value(self, quotes: Mapping[str, float]) -> float:
        return round(self.market_value(quotes) + self.cash_remaining, 2)

    def to_json(self) -> str:
        return json.dumps(
            {
                "chat_id": self.chat_id,
                "holdings": [h.to_dict() for h in self.holdings],
                "cash_remaining": self.cash_remaining,
                "last_updated": self.last_updated.isoformat(),
            }
        )

    @classmethod
    def from_json(cls, raw: str, *, chat_id: int) -> Portfolio:
        data = json.loads(raw)
        return cls(
            chat_id=int(data.get("chat_id", chat_id)),
            holdings=[Holding.from_dict(h) for h in data.get("holdings", [])],
            cash_remaining=float(data.get("cash_remaining", 0.0)),
            last_updated=_parse_dt(data.get("last_updated")),
        )


def _parse_dt(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(tz=UTC)
    try:
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return datetime.now(tz=UTC)


class PortfolioStore:
    """Per-chat portfolio persistence layered on `RedisStore`.

    Reuses the same hashing salt + REST client so we don't open a second
    Upstash connection. Read-modify-write is intentional: this collection
    is small (≤30 holdings per user) and writes are infrequent.
    """

    def __init__(self, redis: RedisStore) -> None:
        self._redis = redis

    def _key(self, chat_id: int) -> str:
        return f"{KEY_PREFIX}{_hash_user_id(chat_id, self._redis.user_id_salt)}"

    def get(self, *, chat_id: int) -> Portfolio:
        raw = self._redis.call("GET", self._key(chat_id))
        if raw is None:
            return Portfolio(chat_id=chat_id)
        try:
            return Portfolio.from_json(raw, chat_id=chat_id)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            # Corruption is rare but never silent — log + reset to empty so
            # the user gets a fresh portfolio rather than crashes on read.
            logger.warning(
                "portfolio JSON corrupt for chat_id=%s; resetting to empty (%s)",
                chat_id,
                exc,
            )
            return Portfolio(chat_id=chat_id)

    def _save(self, portfolio: Portfolio) -> None:
        updated = replace(portfolio, last_updated=datetime.now(tz=UTC))
        self._redis.call(
            "SET",
            self._key(portfolio.chat_id),
            updated.to_json(),
            "EX",
            str(PORTFOLIO_TTL_SECONDS),
        )

    def add(self, *, chat_id: int, holding: Holding) -> Portfolio:
        current = self.get(chat_id=chat_id)
        updated = replace(current, holdings=[*current.holdings, holding])
        self._save(updated)
        return updated

    def remove(self, *, chat_id: int, symbol: str) -> Holding | None:
        current = self.get(chat_id=chat_id)
        target = next((h for h in current.holdings if h.symbol == symbol), None)
        if target is None:
            return None
        kept = [h for h in current.holdings if h.symbol != symbol]
        self._save(replace(current, holdings=kept))
        return target

    def update_holding(self, *, chat_id: int, symbol: str, **fields: Any) -> Holding | None:
        current = self.get(chat_id=chat_id)
        target_idx = next((i for i, h in enumerate(current.holdings) if h.symbol == symbol), None)
        if target_idx is None:
            return None
        new_holding = replace(current.holdings[target_idx], **fields)
        new_holdings = list(current.holdings)
        new_holdings[target_idx] = new_holding
        self._save(replace(current, holdings=new_holdings))
        return new_holding
