from unittest.mock import MagicMock

import pytest

from bot.state import RateLimiter
from jobs.chatbot_poll import (
    MAX_INPUT_CHARS,
    _extract_text,
    _handle_one,
    _is_authorised_chat,
)

# -------------------- authorisation --------------------


def test_is_authorised_chat_matches_owner():
    assert _is_authorised_chat(12345, "12345")
    assert _is_authorised_chat("-100500", "-100500")


def test_is_authorised_chat_rejects_stranger():
    assert not _is_authorised_chat(99999, "12345")


def test_is_authorised_chat_allows_owner_dm():
    assert _is_authorised_chat(42, "-100500", owner_user_id="42")
    assert not _is_authorised_chat(42, "-100500")


def test_is_authorised_chat_rejects_other_user_dm_even_with_owner_set():
    assert not _is_authorised_chat(99, "-100500", owner_user_id="42")


# -------------------- _extract_text --------------------


def test_extract_text_strips_leading_mention():
    msg = {
        "text": "@pravys_market_bot what should I buy?",
        "entities": [{"type": "mention", "offset": 0, "length": 19}],
    }
    assert _extract_text(msg, "pravys_market_bot") == "what should I buy?"


def test_extract_text_preserves_plain_dm():
    msg = {"text": "give me the top 5 right now"}
    assert _extract_text(msg, "pravys_market_bot") == "give me the top 5 right now"


def test_extract_text_slash_command_keeps_verb_in_group():
    """/today@bot RELIANCE must become '/today RELIANCE' — don't drop the verb."""
    msg = {"text": "/today@pravys_market_bot RELIANCE"}
    assert _extract_text(msg, "pravys_market_bot") == "/today RELIANCE"


def test_extract_text_bare_slash_command_in_group_keeps_verb():
    """'/start@bot' alone must surface as '/start' so greetings still work."""
    msg = {"text": "/start@pravys_market_bot"}
    assert _extract_text(msg, "pravys_market_bot") == "/start"


def test_extract_text_slash_command_in_dm_unchanged():
    """Without @bot suffix (typical DM), leave /start intact for the agent."""
    msg = {"text": "/start"}
    assert _extract_text(msg, "pravys_market_bot") == "/start"


def test_extract_text_mention_mid_sentence_preserved():
    """A mid-text mention must NOT be stripped — the whole sentence is the intent."""
    msg = {
        "text": "hey look at @pravys_market_bot here",
        "entities": [{"type": "mention", "offset": 12, "length": 19}],
    }
    assert _extract_text(msg, "pravys_market_bot") == "hey look at @pravys_market_bot here"


def test_extract_text_returns_none_for_empty():
    assert _extract_text({"text": ""}, "x") is None
    assert _extract_text({}, "x") is None
    assert _extract_text({"text": "   "}, "x") is None


# -------------------- _handle_one --------------------


def _make_update(text: str, *, chat_id: int = -100500, user_id: int = 42) -> dict:
    return {
        "update_id": 1,
        "message": {
            "text": text,
            "chat": {"id": chat_id, "type": "group"},
            "from": {"id": user_id, "is_bot": False},
        },
    }


def _make_mocks(monkeypatch):
    agent = MagicMock()
    agent.reply.return_value = MagicMock(text="the reply", tool_calls_made=1)
    telegram = MagicMock()
    telegram.bot_token = "token"
    telegram._client = MagicMock()

    sent: list[tuple] = []

    class DummySendClient:
        def __init__(self, *a, **kw):
            self.calls = []

        def send_message(self, text, *, parse_mode="HTML"):
            sent.append((text, parse_mode))

    monkeypatch.setattr("jobs.chatbot_poll.TelegramClient", DummySendClient)
    return agent, telegram, sent


def test_handle_one_dispatches_and_sends_plain_text(monkeypatch):
    agent, telegram, sent = _make_mocks(monkeypatch)
    rl = RateLimiter()
    _handle_one(
        _make_update("hello"),
        agent=agent,
        telegram=telegram,
        owner_chat_id="-100500",
        owner_user_id=None,
        bot_username="pravys_market_bot",
        rate_limiter=rl,
    )
    agent.reply.assert_called_once_with("hello")
    assert sent == [("the reply", None)]
    assert rl.is_limited(42)


def test_handle_one_skips_agent_when_rate_limited(monkeypatch):
    agent, telegram, sent = _make_mocks(monkeypatch)
    rl = RateLimiter()
    rl.mark(42)  # already rate-limited
    _handle_one(
        _make_update("hello"),
        agent=agent,
        telegram=telegram,
        owner_chat_id="-100500",
        owner_user_id=None,
        bot_username="pravys_market_bot",
        rate_limiter=rl,
    )
    agent.reply.assert_not_called()
    assert sent == []


def test_handle_one_ignores_unauthorised_chat(monkeypatch):
    agent, telegram, sent = _make_mocks(monkeypatch)
    update = _make_update("hi", chat_id=999999)
    _handle_one(
        update,
        agent=agent,
        telegram=telegram,
        owner_chat_id="-100500",
        owner_user_id=None,
        bot_username="pravys_market_bot",
        rate_limiter=RateLimiter(),
    )
    agent.reply.assert_not_called()
    assert sent == []


def test_handle_one_ignores_bot_messages(monkeypatch):
    agent, telegram, sent = _make_mocks(monkeypatch)
    update = _make_update("from another bot")
    update["message"]["from"]["is_bot"] = True
    _handle_one(
        update,
        agent=agent,
        telegram=telegram,
        owner_chat_id="-100500",
        owner_user_id=None,
        bot_username="pravys_market_bot",
        rate_limiter=RateLimiter(),
    )
    agent.reply.assert_not_called()


def test_handle_one_ignores_missing_text(monkeypatch):
    agent, telegram, sent = _make_mocks(monkeypatch)
    update = {
        "update_id": 1,
        "message": {
            "photo": [{"file_id": "x"}],
            "chat": {"id": -100500, "type": "group"},
            "from": {"id": 42, "is_bot": False},
        },
    }
    _handle_one(
        update,
        agent=agent,
        telegram=telegram,
        owner_chat_id="-100500",
        owner_user_id=None,
        bot_username="pravys_market_bot",
        rate_limiter=RateLimiter(),
    )
    agent.reply.assert_not_called()


def test_handle_one_rejects_long_input_without_agent_call(monkeypatch):
    agent, telegram, sent = _make_mocks(monkeypatch)
    rl = RateLimiter()
    long_text = "x" * (MAX_INPUT_CHARS + 10)
    _handle_one(
        _make_update(long_text),
        agent=agent,
        telegram=telegram,
        owner_chat_id="-100500",
        owner_user_id=None,
        bot_username="pravys_market_bot",
        rate_limiter=rl,
    )
    agent.reply.assert_not_called()
    assert len(sent) == 1
    # Plain text reply, not HTML.
    assert sent[0][1] is None
    assert str(MAX_INPUT_CHARS) in sent[0][0]
    # Rate limiter NOT marked for a rejected request — user can retry shorter.
    assert not rl.is_limited(42)


def test_handle_one_unmarks_rate_limit_on_send_failure(monkeypatch):
    agent = MagicMock()
    agent.reply.return_value = MagicMock(text="the reply")
    telegram = MagicMock()
    telegram.bot_token = "token"
    telegram._client = MagicMock()

    class FailingSendClient:
        def __init__(self, *a, **kw):
            pass

        def send_message(self, text, *, parse_mode="HTML"):
            raise RuntimeError("boom")

    monkeypatch.setattr("jobs.chatbot_poll.TelegramClient", FailingSendClient)

    rl = RateLimiter()
    with pytest.raises(RuntimeError):
        _handle_one(
            _make_update("hello"),
            agent=agent,
            telegram=telegram,
            owner_chat_id="-100500",
            owner_user_id=None,
            bot_username="pravys_market_bot",
            rate_limiter=rl,
        )
    # User not locked out — they can retry.
    assert not rl.is_limited(42)
