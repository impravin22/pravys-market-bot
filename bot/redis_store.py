"""Upstash Redis REST wrapper for bot state and conversation memory.

We use Upstash's HTTPS REST API instead of the Redis wire protocol so the
chatbot can talk to Redis from GitHub Actions without any networking
configuration. The free tier (10k commands/day, 256 MB) is well above
what this bot needs.

Three kinds of state live here:

- ``telegram:offset`` — the next ``update_id`` to fetch from Telegram's
  ``getUpdates``. Replaces the ``chatbot-state`` git branch.
- ``rate_limit:{hashed_user_id}`` — ISO timestamp of the last message a
  user sent; checked against a 30-second window. The user_id is hashed
  with a per-deployment salt so raw Telegram user_ids never hit the
  Redis payload.
- ``chat_history:{chat_id}`` — JSON array of the last N ``(role, text)``
  pairs in a chat. Fed back into Gemini on every reply so the bot
  remembers the prior turn ("you alright?" → "what about PFC?").

This module is written as a thin, testable client — every HTTP call is
funnelled through ``_call`` so tests can swap ``httpx.Client`` for a
mock without monkeypatching the whole module.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 5.0
CHAT_HISTORY_LIMIT = 10
CHAT_HISTORY_TTL_SECONDS = 7 * 24 * 3600
RATE_LIMIT_TTL_SECONDS = 60
OFFSET_KEY = "telegram:offset"


@dataclass(frozen=True)
class RedisConfig:
    url: str  # e.g. https://us1-smart-dog-12345.upstash.io
    token: str  # REST auth token
    user_id_salt: str  # per-deployment secret for hashing user_ids

    @classmethod
    def from_env(cls) -> RedisConfig | None:
        url = os.getenv("UPSTASH_REDIS_REST_URL")
        token = os.getenv("UPSTASH_REDIS_REST_TOKEN")
        salt = os.getenv("BOT_USER_ID_SALT")
        if not url or not token or not salt:
            return None
        return cls(url=url.rstrip("/"), token=token, user_id_salt=salt)


def _hash_user_id(user_id: int | str, salt: str) -> str:
    """HMAC-SHA256 a Telegram user_id. First 16 hex chars is enough for a key."""
    digest = hmac.new(salt.encode("utf-8"), str(user_id).encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()[:16]


class RedisStore:
    """Upstash REST client wrapping the few commands this bot needs."""

    def __init__(self, config: RedisConfig, *, http_client: httpx.Client | None = None):
        self._config = config
        self._http = http_client or httpx.Client(timeout=DEFAULT_TIMEOUT)

    @property
    def user_id_salt(self) -> str:
        """Public read-only access to the per-deployment hashing salt.

        Exposed so layered stores (e.g. ``core.portfolio.PortfolioStore``)
        can hash chat_ids consistently without poking at private fields.
        """
        return self._config.user_id_salt

    def call(self, *args: str) -> Any:
        """Public command runner — same semantics as ``_call`` (kept for back-compat)."""
        return self._call(*args)

    def _call(self, *args: str) -> Any:
        """POST a command to Upstash. Returns the parsed ``result`` field.

        Classifies failures into a single ``RuntimeError`` type so callers
        do not have to catch ``httpx`` exceptions directly:
        - Transport failure (DNS, timeout, connection refused).
        - Non-JSON response body (proxy error pages).
        - 5xx status codes.
        - Upstash payload with an ``"error"`` field.
        """
        try:
            resp = self._http.post(
                self._config.url,
                headers={"Authorization": f"Bearer {self._config.token}"},
                json=list(args),
            )
        except httpx.TimeoutException as exc:
            raise RuntimeError(f"Upstash {args[0]} timed out after {DEFAULT_TIMEOUT}s") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"Upstash {args[0]} transport error: {type(exc).__name__}: {exc}"
            ) from exc

        if resp.status_code >= 500:
            raise RuntimeError(
                f"Upstash {args[0]} 5xx status={resp.status_code} body={(resp.text or '')[:200]!r}"
            )
        try:
            payload = resp.json()
        except ValueError as exc:
            raise RuntimeError(
                f"Upstash returned non-JSON body status={resp.status_code} "
                f"body={(resp.text or '')[:200]!r}"
            ) from exc
        if isinstance(payload, dict) and "error" in payload:
            raise RuntimeError(f"Upstash {args[0]} failed: {payload['error']}")
        return payload.get("result") if isinstance(payload, dict) else None

    # -------------------- telegram offset --------------------

    def get_offset(self) -> int:
        """Return the persisted Telegram ``update_id`` to fetch from.

        A missing key is treated as a clean start (returns 0). A corrupted
        value raises loudly: silently resetting to 0 would trigger a mass
        replay of every update Telegram still has queued (~24 h window).
        """
        raw = self._call("GET", OFFSET_KEY)
        if raw is None:
            return 0
        try:
            return int(raw)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"telegram:offset corrupt in Redis (value={raw!r}); investigate "
                "before allowing a reset — replaying every queued update is dangerous."
            ) from exc

    def set_offset(self, offset: int) -> None:
        self._call("SET", OFFSET_KEY, str(int(offset)))

    # -------------------- rate limit --------------------

    def _rate_limit_key(self, user_id: int | str) -> str:
        return f"rate_limit:{_hash_user_id(user_id, self._config.user_id_salt)}"

    def is_rate_limited(self, user_id: int | str, *, seconds: int) -> bool:
        raw = self._call("GET", self._rate_limit_key(user_id))
        if raw is None:
            return False
        try:
            last = datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            return False
        return datetime.now(tz=UTC) - last < timedelta(seconds=seconds)

    def mark_user(self, user_id: int | str) -> None:
        self._call(
            "SET",
            self._rate_limit_key(user_id),
            datetime.now(tz=UTC).isoformat(),
            "EX",
            str(RATE_LIMIT_TTL_SECONDS),
        )

    def unmark_user(self, user_id: int | str) -> None:
        self._call("DEL", self._rate_limit_key(user_id))

    # -------------------- chat history --------------------

    def _history_key(self, chat_id: int | str) -> str:
        # Hash the chat identifier too: for private Telegram DMs the chat_id
        # equals the user_id, so an un-hashed key would leak a raw Telegram
        # user_id into the Upstash keyspace. Hashing here makes the key
        # opaque without breaking per-chat isolation.
        return f"chat_history:{_hash_user_id(chat_id, self._config.user_id_salt)}"

    def get_history(self, chat_id: int | str) -> list[dict[str, str]]:
        raw = self._call("GET", self._history_key(chat_id))
        if raw is None:
            return []
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            logger.warning("chat_history for %s corrupt; resetting", chat_id)
            return []
        if not isinstance(value, list):
            return []
        return [turn for turn in value if isinstance(turn, dict)]

    def append_turn(
        self,
        chat_id: int | str,
        user_text: str,
        bot_text: str,
        *,
        limit: int = CHAT_HISTORY_LIMIT,
    ) -> None:
        history = self.get_history(chat_id)
        history.append({"role": "user", "text": user_text})
        history.append({"role": "model", "text": bot_text})
        # Keep the last ``limit`` turns. A turn here is one user+model pair,
        # so keep ``2 * limit`` entries.
        if len(history) > 2 * limit:
            history = history[-2 * limit :]
        self._call(
            "SET",
            self._history_key(chat_id),
            json.dumps(history),
            "EX",
            str(CHAT_HISTORY_TTL_SECONDS),
        )
