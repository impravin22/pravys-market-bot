"""GH Actions entry point: poll Telegram, stream a Gemini reply back in-chat.

Each cron tick:

1. Init Sentry + Logfire if their env vars are set.
2. Load persisted offset from Upstash Redis.
3. ``getUpdates`` for anything since the previous ack.
4. For each eligible message:
   - Authorised chat only (group or owner DM).
   - Length-capped, not rate-limited, not from a bot.
   - Pull the last N turns of chat history from Redis and thread them
     into the Gemini call so the bot remembers prior context.
   - Stream the reply via ``editMessageText`` as Gemini produces chunks.
   - Append the (user, model) pair back to history.
5. Persist the new offset.

State lives exclusively in Upstash Redis — no git-branch writes — so the
bot has no PII in public git history and state survives runner
ephemerality.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path

import httpx

from bot.agent import HermesAgent
from bot.handlers.portfolio_commands import PortfolioCommands, parse_command
from bot.observability import capture_exception, init_logfire, init_sentry
from bot.redis_store import RATE_LIMIT_TTL_SECONDS, RedisConfig, RedisStore
from bot.streaming import TelegramStream
from core.canslim import StockFundamentals, classify_phase
from core.config import load_config
from core.daily_picks import composite_rating
from core.data.screener_cache import ScreenerCache
from core.data.screener_in import enrich_fundamentals_with_snapshot, fetch_snapshot
from core.fundamentals import enrich_with_earnings, fundamentals_from_history
from core.nse_data import fetch_history, fetch_nifty
from core.picks_cache import PicksCache
from core.portfolio import Holding, PortfolioStore
from core.screener import detect_market_regime
from core.sell_signals import SellSignal, evaluate_holding
from core.strategies import all_strategies
from core.telegram_client import TelegramClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("chatbot_poll")

GET_UPDATES_TIMEOUT = 10.0
MAX_INPUT_CHARS = 1000
PLAYBOOK_PATH = Path(os.getenv("CANSLIM_PLAYBOOK_PATH", "canslim-playbook.pdf"))


def _call_get_updates(bot_token: str, offset: int) -> list[dict]:
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    params = {"offset": offset, "timeout": 0, "allowed_updates": '["message"]'}
    try:
        with httpx.Client(timeout=GET_UPDATES_TIMEOUT) as client:
            resp = client.get(url, params=params)
    except httpx.HTTPError as exc:
        logger.warning("getUpdates transport error: %s", exc)
        return []
    if resp.status_code != 200:
        logger.warning("getUpdates HTTP %d: %s", resp.status_code, resp.text[:200])
        return []
    payload = resp.json()
    if not payload.get("ok"):
        logger.warning("getUpdates not ok: %s", payload)
        return []
    return list(payload.get("result", []))


def _is_authorised_chat(
    chat_id: int | str,
    owner_chat_id: str,
    *,
    owner_user_id: str | None = None,
) -> bool:
    target = str(chat_id)
    if target == str(owner_chat_id):
        return True
    return bool(owner_user_id) and target == str(owner_user_id)


def _extract_text(message: dict, bot_username: str | None) -> str | None:
    text = (message.get("text") or "").strip()
    if not text:
        return None
    entities = message.get("entities") or []
    for ent in entities:
        if ent.get("type") == "mention" and ent.get("offset") == 0:
            length = int(ent.get("length", 0))
            text = text[length:].strip()
            break
    if bot_username and text.startswith("/"):
        head, sep, rest = text.partition(" ")
        stripped_head = head.removesuffix(f"@{bot_username}")
        text = f"{stripped_head}{sep}{rest}".strip()
    return text or None


def _bot_username(bot_token: str) -> str | None:
    url = f"https://api.telegram.org/bot{bot_token}/getMe"
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(url)
    except httpx.HTTPError as exc:
        logger.warning("getMe transport error: %s", exc)
        return None
    if resp.status_code != 200:
        logger.warning("getMe HTTP %d: %s", resp.status_code, resp.text[:200])
        return None
    data = resp.json()
    if not data.get("ok"):
        logger.warning("getMe not ok: %s", data)
        return None
    return data.get("result", {}).get("username")


def _send_plain(telegram: TelegramClient, chat_id: int | str, text: str) -> None:
    per_chat = TelegramClient(
        telegram.bot_token,
        str(chat_id),
        client=telegram._client,  # noqa: SLF001 — reuse the httpx transport
    )
    per_chat.send_message(text, parse_mode=None)


def _handle_one(
    update: dict,
    *,
    agent: HermesAgent,
    telegram: TelegramClient,
    owner_chat_id: str,
    owner_user_id: str | None,
    bot_username: str | None,
    store: RedisStore,
    commands: PortfolioCommands | None = None,
) -> None:
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    user = message.get("from") or {}
    chat_id = chat.get("id")
    user_id = user.get("id")

    if user.get("is_bot") or not chat_id or not user_id:
        return
    if not _is_authorised_chat(chat_id, owner_chat_id, owner_user_id=owner_user_id):
        logger.info("ignoring message from unauthorised chat_id=%s", chat_id)
        return

    text = _extract_text(message, bot_username)
    if not text:
        return

    if len(text) > MAX_INPUT_CHARS:
        _send_plain(
            telegram,
            chat_id,
            f"Keep messages under {MAX_INPUT_CHARS} characters, please — try a shorter question.",
        )
        return

    # Try portfolio commands first — short-circuits before rate-limit + Gemini.
    if commands is not None:
        parsed = parse_command(text)
        if parsed is not None:
            cmd, args = parsed
            result = commands.handle(chat_id=int(chat_id), command=cmd, args=args)
            if result.should_skip_agent:
                _send_plain(telegram, chat_id, result.reply_text)
                logger.info(
                    "command-handled chat_id=%s cmd=%s args=%d",
                    chat_id,
                    cmd,
                    len(args),
                )
                return

    if store.is_rate_limited(user_id, seconds=RATE_LIMIT_TTL_SECONDS):
        logger.info("rate-limiting user_id=(hashed)")
        return
    store.mark_user(user_id)

    logger.info("streaming reply chat_id=%s text_len=%d", chat_id, len(text))
    try:
        history = store.get_history(chat_id)
    except Exception as exc:  # noqa: BLE001 — history is optional context
        logger.warning("history fetch failed (continuing without): %s", exc)
        capture_exception(
            exc,
            update_id=str(update.get("update_id")),
            stage="history_fetch",
        )
        history = []

    stream = TelegramStream(
        bot_token=telegram.bot_token,
        chat_id=chat_id,
        http_client=telegram._client,  # noqa: SLF001
    )

    try:
        final = stream.stream(agent.stream_reply(text, history=history))
    except Exception as exc:
        # Reply never landed. Release the rate-limit slot so the user can
        # retry without waiting, and surface the error. `unmark_user` has
        # its own failure path wrapped so an Upstash outage doesn't mask
        # the original exception.
        try:
            store.unmark_user(user_id)
        except Exception:  # noqa: BLE001
            logger.warning("unmark_user failed during stream-error recovery", exc_info=True)
        capture_exception(
            exc,
            update_id=str(update.get("update_id")),
            chat_kind=str(chat.get("type") or "unknown"),
            stage="stream",
        )
        raise

    logger.info("reply-sent chars=%d", len(final))

    # Reply already went out. A persistence failure here doesn't break the
    # user's experience — they saw the reply — but it costs us conversation
    # memory for the next turn, so flag it loudly.
    try:
        store.append_turn(chat_id, text, final)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "history persist failed chat_id=%s update_id=%s — next turn loses context: %s",
            chat_id,
            update.get("update_id"),
            exc,
        )
        capture_exception(
            exc,
            update_id=str(update.get("update_id")),
            chat_kind=str(chat.get("type") or "unknown"),
            stage="persist_history",
        )


def _make_why_evaluator(
    strategies: list, screener_cache: ScreenerCache
) -> Callable[[str], dict | None]:
    """Return a callable that scores a single symbol live.

    Live fetch ≈ 5s per symbol. Acceptable for an on-demand command;
    the cached `/picks` command serves the heavy daily run instead.
    """

    def evaluator(symbol: str) -> dict | None:
        nifty = fetch_nifty()
        if nifty is None:
            return None
        regime = detect_market_regime(nifty)
        history = fetch_history(symbol, period="1y")
        if history is None:
            return None
        base = fundamentals_from_history(symbol, history)
        with_earnings = enrich_with_earnings(base)
        snapshot = screener_cache.get_or_fetch(symbol)
        enriched = enrich_fundamentals_with_snapshot(with_earnings, snapshot)
        verdicts = [s.evaluate(enriched, regime) for s in strategies]
        # Re-classify regime phase deterministically — defensive against an
        # incomplete regime instance from older paths.
        _ = classify_phase  # type-check + keep import live
        return {
            "symbol": symbol,
            "composite_rating": composite_rating(verdicts),
            "fundamentals_summary": _fundamentals_summary(enriched),
            "verdicts": verdicts,
        }

    return evaluator


def _make_sells_evaluator() -> Callable[[Holding], SellSignal | None]:
    def evaluator(holding: Holding) -> SellSignal | None:
        history = fetch_history(holding.symbol, period="6mo")
        if history is None:
            return None
        closes = history.history["Close"].dropna()
        if closes.empty:
            return None
        current_close = float(closes.iloc[-1])
        return evaluate_holding(
            holding,
            current_close=current_close,
            history=history.history,
        )

    return evaluator


def _fundamentals_summary(f: StockFundamentals) -> str:
    parts: list[str] = []
    if f.last_close is not None:
        parts.append(f"px=₹{f.last_close:.2f}")
    if f.rs_rating is not None:
        parts.append(f"RS={f.rs_rating:.0f}")
    if f.pe_ratio is not None:
        parts.append(f"P/E={f.pe_ratio:.1f}")
    if f.pb_ratio is not None:
        parts.append(f"P/B={f.pb_ratio:.2f}")
    if f.debt_to_equity is not None:
        parts.append(f"D/E={f.debt_to_equity:.2f}")
    return " · ".join(parts)


def main() -> int:
    init_sentry()
    init_logfire()

    config = load_config()
    redis_config = RedisConfig.from_env()
    if redis_config is None:
        logger.error(
            "Redis env vars missing — set UPSTASH_REDIS_REST_URL, "
            "UPSTASH_REDIS_REST_TOKEN, and BOT_USER_ID_SALT."
        )
        return 1
    store = RedisStore(redis_config)

    offset = store.get_offset()

    bot_username = _bot_username(config.telegram.bot_token)
    updates = _call_get_updates(config.telegram.bot_token, offset)
    if not updates:
        logger.info("no new updates (offset=%d)", offset)
        return 0

    agent = HermesAgent(
        api_key=config.google.api_key,
        model=config.google.model,
        playbook_path=PLAYBOOK_PATH if PLAYBOOK_PATH.exists() else None,
    )
    telegram = TelegramClient(config.telegram.bot_token, config.telegram.chat_id)
    owner_user_id = os.getenv("TELEGRAM_OWNER_USER_ID")
    portfolio_store = PortfolioStore(redis=store)
    picks_cache = PicksCache(redis=store)

    # Reusable HTTP client for screener.in (kept for the run; closed when the
    # process exits — chatbot-poll is short-lived per cron tick).
    screener_http = httpx.Client(timeout=10.0)
    screener_cache = ScreenerCache(
        redis=store, fetcher=lambda sym: fetch_snapshot(sym, http_client=screener_http)
    )
    strategies = all_strategies()

    commands = PortfolioCommands(
        store=portfolio_store,
        picks_cache_reader=picks_cache.read,
        why_evaluator=_make_why_evaluator(strategies, screener_cache),
        sells_evaluator=_make_sells_evaluator(),
    )

    last_update_id = offset
    for update in updates:
        update_id = int(update.get("update_id") or 0)
        if update_id > last_update_id:
            last_update_id = update_id
        try:
            _handle_one(
                update,
                agent=agent,
                telegram=telegram,
                owner_chat_id=config.telegram.chat_id,
                owner_user_id=owner_user_id,
                bot_username=bot_username,
                store=store,
                commands=commands,
            )
        except Exception as exc:  # noqa: BLE001 — never let one bad message kill the batch
            logger.exception("handler failed for update_id=%s: %s", update_id, exc)

    store.set_offset(last_update_id + 1)
    logger.info(
        "chatbot-poll done | processed=%d | next_offset=%d", len(updates), last_update_id + 1
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
