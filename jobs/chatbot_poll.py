"""GH Actions entry point: poll Telegram, dispatch to the Hermes agent.

Runs every 5 min via cron. Each run:

1. Loads persisted state from ``state/chatbot_state.json`` (on the
   ``chatbot-state`` branch — checkout managed by the workflow).
2. Calls ``getUpdates`` with the saved offset; Telegram returns every
   message since the previous ack.
3. For each eligible update (direct mention, DM, reply-to-bot, or
   ``/`` command), consults the in-process rate limiter, then passes the
   text to ``HermesAgent.reply()``.
4. Sends the agent's reply back via Telegram ``sendMessage`` as plain
   text (no HTML parsing — Gemini output is free-form and would otherwise
   trigger 400s or permit injection).
5. Saves the new offset to the state file. The workflow commits it to
   ``chatbot-state``. The rate-limit map is **not** persisted — it stays
   in-process only so Telegram user_ids never hit a public git branch.

Design choices:
- Only respond in authorised chats (the configured group chat, or a DM
  from the owner when ``TELEGRAM_OWNER_USER_ID`` is set) — prevents a
  random stranger from burning the Gemini budget.
- Hard cap on input length; Gemini spend is bounded.
- Ignore bot messages, edits, and non-text updates.
- Best-effort: a failure on one message never blocks later ones, but we
  un-mark the rate-limit entry if the reply send fails so the user isn't
  blocked from retrying.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import httpx

from bot.agent import HermesAgent
from bot.state import RateLimiter, load_state, save_state
from core.config import load_config
from core.telegram_client import TelegramClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("chatbot_poll")

GET_UPDATES_TIMEOUT = 10.0
MAX_INPUT_CHARS = 1000
STATE_PATH = Path(os.getenv("CHATBOT_STATE_PATH", "state/chatbot_state.json"))


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
    """Allow the configured group AND a direct DM from the owner.

    For private 1:1 chats Telegram uses chat_id == user_id, so setting
    ``TELEGRAM_OWNER_USER_ID`` to the owner's user_id enables DM replies.
    Without it, only the configured group chat is authorised.
    """
    target = str(chat_id)
    if target == str(owner_chat_id):
        return True
    return bool(owner_user_id) and target == str(owner_user_id)


def _extract_text(message: dict, bot_username: str | None) -> str | None:
    """Pull the user text, stripping only the ``@bot`` suffix.

    Handles three common Telegram patterns without losing the user's
    intent:

    - ``@pravys_market_bot what should I buy?`` — leading mention stripped
      via the Telegram ``entities`` offset, leaving ``what should I buy?``.
    - ``/today@pravys_market_bot RELIANCE`` — only the ``@bot`` suffix is
      trimmed from the command token; the ``/today`` verb is preserved so
      the agent sees ``/today RELIANCE``.
    - Plain DM text — returned verbatim.
    """
    text = (message.get("text") or "").strip()
    if not text:
        return None

    entities = message.get("entities") or []
    for ent in entities:
        if ent.get("type") == "mention" and ent.get("offset") == 0:
            length = int(ent.get("length", 0))
            text = text[length:].strip()
            break

    # Strip only the `@bot` suffix from a slash command; keep the verb.
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
    """Send a reply to ``chat_id`` with ``parse_mode=None``.

    The Gemini output is free-form prose; HTML parsing would trip on stray
    ``<`` characters in tickers or news snippets and reject the whole send.
    """
    per_chat_client = TelegramClient(
        telegram.bot_token,
        str(chat_id),
        client=telegram._client,  # noqa: SLF001 — reuse the httpx transport
    )
    per_chat_client.send_message(text, parse_mode=None)


def _handle_one(
    update: dict,
    *,
    agent: HermesAgent,
    telegram: TelegramClient,
    owner_chat_id: str,
    owner_user_id: str | None,
    bot_username: str | None,
    rate_limiter: RateLimiter,
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

    if rate_limiter.is_limited(user_id):
        logger.info("rate-limiting user_id=%s", user_id)
        return
    rate_limiter.mark(user_id)

    logger.info("dispatching to agent: user_id=%s text=%r", user_id, text[:80])
    try:
        reply = agent.reply(text)
        _send_plain(telegram, chat_id, reply.text)
    except Exception:
        # Reply never reached the user — release the rate-limit slot so they
        # aren't locked out for 30 s from a message they never saw answered.
        rate_limiter.unmark(user_id)
        raise


def main() -> int:
    config = load_config()
    state = load_state(STATE_PATH)
    offset = int(state.get("telegram_offset") or 0)

    bot_username = _bot_username(config.telegram.bot_token)
    updates = _call_get_updates(config.telegram.bot_token, offset)
    if not updates:
        logger.info("no new updates (offset=%d)", offset)
        save_state(state, STATE_PATH)
        return 0

    agent = HermesAgent(api_key=config.google.api_key, model=config.google.model)
    telegram = TelegramClient(config.telegram.bot_token, config.telegram.chat_id)
    owner_user_id = os.getenv("TELEGRAM_OWNER_USER_ID")
    rate_limiter = RateLimiter()

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
                rate_limiter=rate_limiter,
            )
        except Exception as exc:  # noqa: BLE001 — never let one bad message kill the batch
            logger.exception("handler failed for update_id=%s: %s", update_id, exc)

    state["telegram_offset"] = last_update_id + 1
    save_state(state, STATE_PATH)
    logger.info(
        "chatbot-poll done | processed=%d | next_offset=%d", len(updates), state["telegram_offset"]
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
