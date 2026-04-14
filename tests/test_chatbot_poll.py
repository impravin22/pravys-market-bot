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
    msg = {"text": "/today@pravys_market_bot RELIANCE"}
    assert _extract_text(msg, "pravys_market_bot") == "/today RELIANCE"


def test_extract_text_bare_slash_command_in_group_keeps_verb():
    msg = {"text": "/start@pravys_market_bot"}
    assert _extract_text(msg, "pravys_market_bot") == "/start"


def test_extract_text_slash_command_in_dm_unchanged():
    msg = {"text": "/start"}
    assert _extract_text(msg, "pravys_market_bot") == "/start"


def test_extract_text_mention_mid_sentence_preserved():
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


class _FakeStream:
    instances: list["_FakeStream"] = []

    def __init__(self, *, bot_token: str, chat_id, http_client):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.chunks_seen: list[str] = []
        type(self).instances.append(self)

    def stream(self, chunks):
        self.chunks_seen = list(chunks)
        return "".join(self.chunks_seen)


def _attach_fake_stream(monkeypatch):
    _FakeStream.instances.clear()
    monkeypatch.setattr("jobs.chatbot_poll.TelegramStream", _FakeStream)


def _fake_agent(text_chunks: list[str]) -> MagicMock:
    agent = MagicMock()
    agent.stream_reply.return_value = iter(text_chunks)
    return agent


def _fake_telegram() -> MagicMock:
    telegram = MagicMock()
    telegram.bot_token = "token"
    telegram._client = MagicMock()
    return telegram


def test_handle_one_streams_reply_via_telegram_stream(monkeypatch):
    _attach_fake_stream(monkeypatch)
    agent = _fake_agent(["Hello ", "world"])
    rl = RateLimiter()
    _handle_one(
        _make_update("hi"),
        agent=agent,
        telegram=_fake_telegram(),
        owner_chat_id="-100500",
        owner_user_id=None,
        bot_username="pravys_market_bot",
        rate_limiter=rl,
    )
    assert len(_FakeStream.instances) == 1
    assert _FakeStream.instances[0].chunks_seen == ["Hello ", "world"]
    agent.stream_reply.assert_called_once_with("hi")
    assert rl.is_limited(42)


def test_handle_one_skips_agent_when_rate_limited(monkeypatch):
    _attach_fake_stream(monkeypatch)
    agent = _fake_agent(["ignored"])
    rl = RateLimiter()
    rl.mark(42)
    _handle_one(
        _make_update("hi"),
        agent=agent,
        telegram=_fake_telegram(),
        owner_chat_id="-100500",
        owner_user_id=None,
        bot_username="pravys_market_bot",
        rate_limiter=rl,
    )
    agent.stream_reply.assert_not_called()
    assert _FakeStream.instances == []


def test_handle_one_ignores_unauthorised_chat(monkeypatch):
    _attach_fake_stream(monkeypatch)
    agent = _fake_agent(["ignored"])
    _handle_one(
        _make_update("hi", chat_id=999999),
        agent=agent,
        telegram=_fake_telegram(),
        owner_chat_id="-100500",
        owner_user_id=None,
        bot_username="pravys_market_bot",
        rate_limiter=RateLimiter(),
    )
    agent.stream_reply.assert_not_called()


def test_handle_one_ignores_bot_messages(monkeypatch):
    _attach_fake_stream(monkeypatch)
    agent = _fake_agent(["ignored"])
    upd = _make_update("from another bot")
    upd["message"]["from"]["is_bot"] = True
    _handle_one(
        upd,
        agent=agent,
        telegram=_fake_telegram(),
        owner_chat_id="-100500",
        owner_user_id=None,
        bot_username="pravys_market_bot",
        rate_limiter=RateLimiter(),
    )
    agent.stream_reply.assert_not_called()


def test_handle_one_ignores_missing_text(monkeypatch):
    _attach_fake_stream(monkeypatch)
    agent = _fake_agent(["ignored"])
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
        telegram=_fake_telegram(),
        owner_chat_id="-100500",
        owner_user_id=None,
        bot_username="pravys_market_bot",
        rate_limiter=RateLimiter(),
    )
    agent.stream_reply.assert_not_called()


def test_handle_one_rejects_long_input_without_agent_call(monkeypatch):
    _attach_fake_stream(monkeypatch)
    sent_direct: list[tuple] = []

    class DummyDirect:
        def __init__(self, *a, **kw):
            pass

        def send_message(self, text, *, parse_mode="HTML"):
            sent_direct.append((text, parse_mode))

    monkeypatch.setattr("jobs.chatbot_poll.TelegramClient", DummyDirect)
    agent = _fake_agent(["ignored"])
    rl = RateLimiter()
    _handle_one(
        _make_update("x" * (MAX_INPUT_CHARS + 10)),
        agent=agent,
        telegram=_fake_telegram(),
        owner_chat_id="-100500",
        owner_user_id=None,
        bot_username="pravys_market_bot",
        rate_limiter=rl,
    )
    agent.stream_reply.assert_not_called()
    assert len(sent_direct) == 1
    assert sent_direct[0][1] is None  # plain text
    assert str(MAX_INPUT_CHARS) in sent_direct[0][0]
    assert not rl.is_limited(42)


def test_handle_one_unmarks_rate_limit_on_stream_failure(monkeypatch):
    class FailingStream:
        def __init__(self, **kwargs):
            pass

        def stream(self, chunks):
            # Consume so the iterator closes cleanly.
            list(chunks)
            raise RuntimeError("edit failed")

    monkeypatch.setattr("jobs.chatbot_poll.TelegramStream", FailingStream)
    agent = _fake_agent(["hello"])
    rl = RateLimiter()
    with pytest.raises(RuntimeError):
        _handle_one(
            _make_update("hi"),
            agent=agent,
            telegram=_fake_telegram(),
            owner_chat_id="-100500",
            owner_user_id=None,
            bot_username="pravys_market_bot",
            rate_limiter=rl,
        )
    assert not rl.is_limited(42)
