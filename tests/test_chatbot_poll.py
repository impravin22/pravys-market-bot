from jobs.chatbot_poll import _extract_text, _is_authorised_chat


def test_is_authorised_chat_matches_owner():
    assert _is_authorised_chat(12345, "12345")
    assert _is_authorised_chat("-100500", "-100500")


def test_is_authorised_chat_rejects_stranger():
    assert not _is_authorised_chat(99999, "12345")


def test_extract_text_strips_leading_mention():
    msg = {
        "text": "@pravys_market_bot what should I buy?",
        "entities": [{"type": "mention", "offset": 0, "length": 19}],
    }
    out = _extract_text(msg, "pravys_market_bot")
    assert out == "what should I buy?"


def test_extract_text_preserves_plain_dm():
    msg = {"text": "give me the top 5 right now"}
    assert _extract_text(msg, "pravys_market_bot") == "give me the top 5 right now"


def test_extract_text_handles_slash_command_with_bot_suffix():
    msg = {"text": "/today@pravys_market_bot RELIANCE"}
    out = _extract_text(msg, "pravys_market_bot")
    assert out == "RELIANCE"


def test_extract_text_returns_none_for_empty():
    assert _extract_text({"text": ""}, "x") is None
    assert _extract_text({}, "x") is None


def test_extract_text_ignores_non_leading_mention():
    msg = {
        "text": "hey look at @pravys_market_bot here",
        "entities": [{"type": "mention", "offset": 12, "length": 19}],
    }
    # Non-leading mention: text stays intact.
    out = _extract_text(msg, "pravys_market_bot")
    assert out == "hey look at @pravys_market_bot here"
