from unittest.mock import MagicMock

import pytest

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


class _FakeStore:
    def __init__(self, *, rate_limited: bool = False, history: list[dict] | None = None):
        self.rate_limited = rate_limited
        self.history = history or []
        self.marks: list[int | str] = []
        self.unmarks: list[int | str] = []
        self.turns: list[tuple[int | str, str, str]] = []

    def is_rate_limited(self, user_id, *, seconds):
        return self.rate_limited

    def mark_user(self, user_id):
        self.marks.append(user_id)

    def unmark_user(self, user_id):
        self.unmarks.append(user_id)

    def get_history(self, chat_id):
        return list(self.history)

    def append_turn(self, chat_id, user_text, bot_text):
        self.turns.append((chat_id, user_text, bot_text))


def test_handle_one_streams_reply_and_appends_history(monkeypatch):
    _attach_fake_stream(monkeypatch)
    agent = _fake_agent(["Hello ", "world"])
    store = _FakeStore()
    _handle_one(
        _make_update("hi"),
        agent=agent,
        telegram=_fake_telegram(),
        owner_chat_id="-100500",
        owner_user_id=None,
        bot_username="pravys_market_bot",
        store=store,
    )
    agent.stream_reply.assert_called_once()
    call_args = agent.stream_reply.call_args
    assert call_args.args[0] == "hi"
    assert call_args.kwargs.get("history") == []
    assert store.marks == [42]
    assert store.turns == [(-100500, "hi", "Hello world")]


def test_handle_one_threads_prior_history_into_agent(monkeypatch):
    _attach_fake_stream(monkeypatch)
    agent = _fake_agent(["ok"])
    store = _FakeStore(
        history=[
            {"role": "user", "text": "you alright?"},
            {"role": "model", "text": "all good mate, what's on your mind?"},
        ]
    )
    _handle_one(
        _make_update("what about PFC?"),
        agent=agent,
        telegram=_fake_telegram(),
        owner_chat_id="-100500",
        owner_user_id=None,
        bot_username="pravys_market_bot",
        store=store,
    )
    assert agent.stream_reply.call_args.kwargs["history"] == [
        {"role": "user", "text": "you alright?"},
        {"role": "model", "text": "all good mate, what's on your mind?"},
    ]


def test_handle_one_skips_agent_when_rate_limited(monkeypatch):
    _attach_fake_stream(monkeypatch)
    agent = _fake_agent(["ignored"])
    store = _FakeStore(rate_limited=True)
    _handle_one(
        _make_update("hi"),
        agent=agent,
        telegram=_fake_telegram(),
        owner_chat_id="-100500",
        owner_user_id=None,
        bot_username="pravys_market_bot",
        store=store,
    )
    agent.stream_reply.assert_not_called()
    assert store.marks == []


def test_handle_one_ignores_unauthorised_chat(monkeypatch):
    _attach_fake_stream(monkeypatch)
    agent = _fake_agent(["ignored"])
    store = _FakeStore()
    _handle_one(
        _make_update("hi", chat_id=999999),
        agent=agent,
        telegram=_fake_telegram(),
        owner_chat_id="-100500",
        owner_user_id=None,
        bot_username="pravys_market_bot",
        store=store,
    )
    agent.stream_reply.assert_not_called()


def test_handle_one_ignores_bot_messages(monkeypatch):
    _attach_fake_stream(monkeypatch)
    agent = _fake_agent(["ignored"])
    store = _FakeStore()
    upd = _make_update("from another bot")
    upd["message"]["from"]["is_bot"] = True
    _handle_one(
        upd,
        agent=agent,
        telegram=_fake_telegram(),
        owner_chat_id="-100500",
        owner_user_id=None,
        bot_username="pravys_market_bot",
        store=store,
    )
    agent.stream_reply.assert_not_called()


def test_handle_one_ignores_missing_text(monkeypatch):
    _attach_fake_stream(monkeypatch)
    agent = _fake_agent(["ignored"])
    store = _FakeStore()
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
        store=store,
    )
    agent.stream_reply.assert_not_called()


def test_handle_one_dispatches_portfolio_command_and_skips_agent(monkeypatch):
    """`/portfolio` short-circuits before rate-limit and never calls Gemini."""
    _attach_fake_stream(monkeypatch)
    agent = _fake_agent(["should not run"])
    store = _FakeStore()

    captured: list[tuple[int | str, str]] = []

    def fake_send_plain(_telegram, chat_id, text):
        captured.append((chat_id, text))

    monkeypatch.setattr("jobs.chatbot_poll._send_plain", fake_send_plain)

    class _StubCommands:
        def handle(self, *, chat_id, command, args):
            from bot.handlers.portfolio_commands import CommandResult

            assert command == "portfolio"
            assert args == []
            return CommandResult("No holdings yet.", should_skip_agent=True)

    _handle_one(
        _make_update("/portfolio"),
        agent=agent,
        telegram=_fake_telegram(),
        owner_chat_id="-100500",
        owner_user_id=None,
        bot_username="pravys_market_bot",
        store=store,
        commands=_StubCommands(),
    )

    agent.stream_reply.assert_not_called()
    assert store.marks == []  # no rate-limit mark when commands short-circuit
    assert captured == [(-100500, "No holdings yet.")]


def test_handle_one_unrecognised_command_falls_through_to_agent(monkeypatch):
    """`/foo` is not a portfolio command — should still hit Gemini."""
    _attach_fake_stream(monkeypatch)
    agent = _fake_agent(["agent reply"])
    store = _FakeStore()

    class _StubCommands:
        def handle(self, *, chat_id, command, args):
            from bot.handlers.portfolio_commands import CommandResult

            return CommandResult("", should_skip_agent=False)

    _handle_one(
        _make_update("/foo"),
        agent=agent,
        telegram=_fake_telegram(),
        owner_chat_id="-100500",
        owner_user_id=None,
        bot_username="pravys_market_bot",
        store=store,
        commands=_StubCommands(),
    )

    agent.stream_reply.assert_called_once()


def test_handle_one_rejects_long_input(monkeypatch):
    _attach_fake_stream(monkeypatch)
    sent_direct: list[tuple] = []

    class DummyDirect:
        def __init__(self, *a, **kw):
            pass

        def send_message(self, text, *, parse_mode="HTML"):
            sent_direct.append((text, parse_mode))

    monkeypatch.setattr("jobs.chatbot_poll.TelegramClient", DummyDirect)
    agent = _fake_agent(["ignored"])
    store = _FakeStore()
    _handle_one(
        _make_update("x" * (MAX_INPUT_CHARS + 10)),
        agent=agent,
        telegram=_fake_telegram(),
        owner_chat_id="-100500",
        owner_user_id=None,
        bot_username="pravys_market_bot",
        store=store,
    )
    agent.stream_reply.assert_not_called()
    assert len(sent_direct) == 1
    assert sent_direct[0][1] is None
    assert str(MAX_INPUT_CHARS) in sent_direct[0][0]
    assert store.marks == []


def test_handle_one_unmarks_rate_limit_on_stream_failure(monkeypatch):
    class FailingStream:
        def __init__(self, **kwargs):
            pass

        def stream(self, chunks):
            list(chunks)
            raise RuntimeError("edit failed")

    monkeypatch.setattr("jobs.chatbot_poll.TelegramStream", FailingStream)
    agent = _fake_agent(["hello"])
    store = _FakeStore()
    with pytest.raises(RuntimeError):
        _handle_one(
            _make_update("hi"),
            agent=agent,
            telegram=_fake_telegram(),
            owner_chat_id="-100500",
            owner_user_id=None,
            bot_username="pravys_market_bot",
            store=store,
        )
    assert store.unmarks == [42]
    assert store.turns == []


def test_handle_one_append_turn_failure_keeps_reply_and_rate_limit(monkeypatch):
    """If Upstash flakes AFTER the reply lands, the user still sees it and
    stays rate-limited — append_turn failure must not re-throw or unmark."""
    _attach_fake_stream(monkeypatch)
    agent = _fake_agent(["ok mate"])

    class FlakyStore(_FakeStore):
        def append_turn(self, chat_id, user_text, bot_text):
            raise RuntimeError("Upstash SET failed")

    store = FlakyStore()
    # Does NOT raise — reply already went out.
    _handle_one(
        _make_update("hi"),
        agent=agent,
        telegram=_fake_telegram(),
        owner_chat_id="-100500",
        owner_user_id=None,
        bot_username="pravys_market_bot",
        store=store,
    )
    # User saw the reply, so they stay rate-limited.
    assert store.marks == [42]
    assert store.unmarks == []


def test_handle_one_history_fetch_failure_falls_back_to_empty(monkeypatch):
    """Redis outage on history fetch must not kill the reply."""
    _attach_fake_stream(monkeypatch)
    agent = _fake_agent(["ok"])

    class HistoryFailStore(_FakeStore):
        def get_history(self, chat_id):
            raise RuntimeError("Upstash GET timed out")

    store = HistoryFailStore()
    _handle_one(
        _make_update("hi"),
        agent=agent,
        telegram=_fake_telegram(),
        owner_chat_id="-100500",
        owner_user_id=None,
        bot_username="pravys_market_bot",
        store=store,
    )
    # Agent still called — with empty history as the fallback.
    agent.stream_reply.assert_called_once()
    assert agent.stream_reply.call_args.kwargs["history"] == []


def test_handle_one_unmark_failure_does_not_mask_stream_error(monkeypatch):
    """If Upstash is down AND the stream fails, the original stream error must propagate."""

    class FailingStream:
        def __init__(self, **kwargs):
            pass

        def stream(self, chunks):
            list(chunks)
            raise RuntimeError("original stream error")

    monkeypatch.setattr("jobs.chatbot_poll.TelegramStream", FailingStream)

    class BothFlakyStore(_FakeStore):
        def unmark_user(self, user_id):
            raise RuntimeError("Upstash also down")

    agent = _fake_agent(["hello"])
    store = BothFlakyStore()
    with pytest.raises(RuntimeError, match="original stream error"):
        _handle_one(
            _make_update("hi"),
            agent=agent,
            telegram=_fake_telegram(),
            owner_chat_id="-100500",
            owner_user_id=None,
            bot_username="pravys_market_bot",
            store=store,
        )
