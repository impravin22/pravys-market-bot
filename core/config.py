"""Env-var validation for Pravy's Market Bot.

Fails fast on missing or invalid values so jobs do not ship a half-configured
digest that silently omits sections.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

REQUIRED_VARS = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "GOOGLE_API_KEY",
)

OPTIONAL_NEWS_VARS = ("GOOGLE_SEARCH_API_KEY", "GOOGLE_CSE_ID")


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    chat_id: str


@dataclass(frozen=True)
class GoogleConfig:
    api_key: str
    model: str
    search_api_key: str | None
    cse_id: str | None


@dataclass(frozen=True)
class Config:
    telegram: TelegramConfig
    google: GoogleConfig
    locale_tz: str
    market_tz: str


def load_config(env: dict[str, str] | None = None) -> Config:
    env = env if env is not None else dict(os.environ)

    missing = [v for v in REQUIRED_VARS if not env.get(v)]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")

    locale_tz = env.get("DIGEST_LOCALE_TZ", "Asia/Taipei")
    market_tz = env.get("MARKET_TZ", "Asia/Kolkata")
    for tz in (locale_tz, market_tz):
        _validate_tz(tz)

    return Config(
        telegram=TelegramConfig(
            bot_token=env["TELEGRAM_BOT_TOKEN"],
            chat_id=env["TELEGRAM_CHAT_ID"],
        ),
        google=GoogleConfig(
            api_key=env["GOOGLE_API_KEY"],
            model=env.get("GOOGLE_AI_DEFAULT_MODEL", "gemini-2.5-pro"),
            search_api_key=env.get("GOOGLE_SEARCH_API_KEY"),
            cse_id=env.get("GOOGLE_CSE_ID"),
        ),
        locale_tz=locale_tz,
        market_tz=market_tz,
    )


def _validate_tz(tz: str) -> None:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        ZoneInfo(tz)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(f"Invalid IANA timezone: {tz!r}") from exc
