from unittest.mock import MagicMock

import httpx
import pytest

from bot.streaming import TELEGRAM_MAX_CHARS, TRUNCATION_SUFFIX, TelegramStream


def _fake_response(payload: dict, *, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.text = str(payload)
    resp.raise_for_status = MagicMock()
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "bad status", request=MagicMock(), response=resp
        )
    return resp


def _capture_client(responses_by_method):
    sent = []

    def post(url, data=None, timeout=None):
        endpoint = url.rsplit("/", 1)[-1]
        sent.append((endpoint, data))
        return responses_by_method[endpoint].pop(0)

    http = MagicMock()
    http.post.side_effect = post
    return http, sent


def test_stream_single_chunk_sends_placeholder_then_edits_final():
    http, sent = _capture_client(
        {
            "sendMessage": [
                _fake_response({"ok": True, "result": {"message_id": 101}}),
            ],
            "editMessageText": [
                _fake_response({"ok": True, "result": {}}),
            ],
        }
    )
    stream = TelegramStream(bot_token="tok", chat_id="-100500", http_client=http)
    final = stream.stream(iter(["Hello world"]))
    assert final == "Hello world"
    endpoints = [e for e, _ in sent]
    assert endpoints[0] == "sendMessage"
    assert "editMessageText" in endpoints


def test_stream_renders_markdown_as_html_on_edit():
    http, sent = _capture_client(
        {
            "sendMessage": [
                _fake_response({"ok": True, "result": {"message_id": 101}}),
            ],
            "editMessageText": [
                _fake_response({"ok": True, "result": {}}),
            ],
        }
    )
    stream = TelegramStream(bot_token="tok", chat_id="-100500", http_client=http)
    stream.stream(iter(["**PFC** is a PSU NBFC"]))
    # First call is sendMessage (placeholder, no parse_mode). Last is the
    # final edit with HTML conversion + parse_mode set.
    final_edit_payload = sent[-1][1]
    assert final_edit_payload["parse_mode"] == "HTML"
    assert "<b>PFC</b>" in final_edit_payload["text"]


def test_stream_escapes_raw_angle_brackets_in_edit():
    http, sent = _capture_client(
        {
            "sendMessage": [
                _fake_response({"ok": True, "result": {"message_id": 101}}),
            ],
            "editMessageText": [
                _fake_response({"ok": True, "result": {}}),
            ],
        }
    )
    stream = TelegramStream(bot_token="tok", chat_id="-100500", http_client=http)
    stream.stream(iter(["profit <up> & revenue flat"]))
    payload = sent[-1][1]
    assert "&lt;up&gt;" in payload["text"]
    assert "&amp;" in payload["text"]


def test_stream_falls_back_to_plain_text_on_parse_entity_error(monkeypatch):
    # Suppress intermediate edits so only the final edit hits the mock queue.
    monkeypatch.setattr("bot.streaming.EDIT_INTERVAL_SECONDS", 1e9)
    http, sent = _capture_client(
        {
            "sendMessage": [
                _fake_response({"ok": True, "result": {"message_id": 101}}),
            ],
            "editMessageText": [
                _fake_response(
                    {"ok": False, "description": "Bad Request: can't parse entities"},
                    status=400,
                ),
                _fake_response({"ok": True, "result": {}}),
            ],
        }
    )
    stream = TelegramStream(bot_token="tok", chat_id="-100500", http_client=http)
    final = stream.stream(iter(["<weird> **bold**"]))
    assert final == "<weird> **bold**"
    endpoints = [e for e, _ in sent]
    assert endpoints.count("editMessageText") == 2
    assert "parse_mode" not in sent[-1][1]


def test_stream_throttles_intermediate_edits(monkeypatch):
    """With time frozen, only the final edit should fire — no intermediates."""
    http, sent = _capture_client(
        {
            "sendMessage": [
                _fake_response({"ok": True, "result": {"message_id": 101}}),
            ],
            "editMessageText": [_fake_response({"ok": True, "result": {}}) for _ in range(5)],
        }
    )
    monkeypatch.setattr("bot.streaming.time.monotonic", lambda: 0.0)

    stream = TelegramStream(bot_token="tok", chat_id="-100500", http_client=http)
    stream.stream(iter(["a", "b", "c", "d", "e"]))
    endpoints = [e for e, _ in sent]
    # Placeholder + exactly one final edit. Intermediate edits are gated
    # by the >= EDIT_INTERVAL_SECONDS check.
    assert endpoints.count("sendMessage") == 1
    assert endpoints.count("editMessageText") == 1


def test_stream_fires_intermediate_edits_when_time_advances(monkeypatch):
    """Sanity check the other side — when the interval elapses, edits fire."""
    http, sent = _capture_client(
        {
            "sendMessage": [
                _fake_response({"ok": True, "result": {"message_id": 101}}),
            ],
            "editMessageText": [_fake_response({"ok": True, "result": {}}) for _ in range(10)],
        }
    )
    counter = {"n": 0.0}

    def fake_monotonic():
        counter["n"] += 10.0
        return counter["n"]

    monkeypatch.setattr("bot.streaming.time.monotonic", fake_monotonic)

    stream = TelegramStream(bot_token="tok", chat_id="-100500", http_client=http)
    stream.stream(iter(["a", "b", "c"]))
    endpoints = [e for e, _ in sent]
    # 1 placeholder + 3 intermediate edits (one per chunk because time flies)
    # + 1 final edit. Dedup may skip the final edit if the last intermediate
    # already sent the full content, so we allow 3 or 4.
    assert endpoints.count("sendMessage") == 1
    assert 3 <= endpoints.count("editMessageText") <= 4


def test_stream_truncates_past_telegram_limit():
    http, sent = _capture_client(
        {
            "sendMessage": [
                _fake_response({"ok": True, "result": {"message_id": 101}}),
            ],
            "editMessageText": [_fake_response({"ok": True, "result": {}}) for _ in range(5)],
        }
    )
    stream = TelegramStream(bot_token="tok", chat_id="-100500", http_client=http)
    # Feed more than the Telegram cap in a single chunk.
    final = stream.stream(iter(["x" * (TELEGRAM_MAX_CHARS + 500)]))
    assert len(final) <= TELEGRAM_MAX_CHARS
    assert TRUNCATION_SUFFIX.strip() in final


def test_stream_intermediate_edit_errors_are_swallowed(monkeypatch):
    http, sent = _capture_client(
        {
            "sendMessage": [
                _fake_response({"ok": True, "result": {"message_id": 101}}),
            ],
            "editMessageText": [
                _fake_response({"ok": False, "description": "boom"}, status=500),
                _fake_response({"ok": True, "result": {}}),
            ],
        }
    )
    # Force every chunk to trigger an edit attempt.
    t = {"now": 0.0}
    monkeypatch.setattr(
        "bot.streaming.time.monotonic", lambda: t.__setitem__("now", t["now"] + 10) or t["now"]
    )

    stream = TelegramStream(bot_token="tok", chat_id="-100500", http_client=http)
    final = stream.stream(iter(["hello ", "world"]))
    assert final == "hello world"


def test_stream_final_edit_failure_propagates(monkeypatch):
    monkeypatch.setattr("bot.streaming.EDIT_INTERVAL_SECONDS", 1e9)
    http, sent = _capture_client(
        {
            "sendMessage": [
                _fake_response({"ok": True, "result": {"message_id": 101}}),
            ],
            "editMessageText": [
                _fake_response({"ok": False, "description": "Forbidden"}, status=403),
            ],
        }
    )
    stream = TelegramStream(bot_token="tok", chat_id="-100500", http_client=http)
    with pytest.raises(RuntimeError):
        stream.stream(iter(["final answer"]))


def test_stream_plain_text_fallback_failure_surfaces_real_error(monkeypatch):
    """Both the HTML edit and the plain-text retry fail → error reflects the retry status."""
    monkeypatch.setattr("bot.streaming.EDIT_INTERVAL_SECONDS", 1e9)
    http, sent = _capture_client(
        {
            "sendMessage": [
                _fake_response({"ok": True, "result": {"message_id": 101}}),
            ],
            "editMessageText": [
                _fake_response(
                    {"ok": False, "description": "Bad Request: can't parse entities"},
                    status=400,
                ),
                _fake_response(
                    {"ok": False, "description": "Forbidden: bot was kicked"},
                    status=403,
                ),
            ],
        }
    )
    stream = TelegramStream(bot_token="tok", chat_id="-100500", http_client=http)
    with pytest.raises(RuntimeError) as excinfo:
        stream.stream(iter(["<weird> **bold**"]))
    # The error surfaces the plain-retry's 403, not the earlier HTML 400.
    assert "plain fallback" in str(excinfo.value)
    assert "403" in str(excinfo.value) or "Forbidden" in str(excinfo.value)


def test_stream_non_parse_400_does_not_trigger_plain_fallback(monkeypatch):
    """A 400 that isn't about parse entities must not retry as plain text."""
    monkeypatch.setattr("bot.streaming.EDIT_INTERVAL_SECONDS", 1e9)
    http, sent = _capture_client(
        {
            "sendMessage": [
                _fake_response({"ok": True, "result": {"message_id": 101}}),
            ],
            "editMessageText": [
                _fake_response(
                    {"ok": False, "description": "Bad Request: message is too long"},
                    status=400,
                ),
            ],
        }
    )
    stream = TelegramStream(bot_token="tok", chat_id="-100500", http_client=http)
    with pytest.raises(RuntimeError):
        stream.stream(iter(["anything"]))
    endpoints = [e for e, _ in sent]
    # Exactly one edit attempt — no fallback.
    assert endpoints.count("editMessageText") == 1


def test_stream_non_json_body_surfaces_runtime_error(monkeypatch):
    """Non-JSON Telegram response (Cloudflare HTML etc.) must raise RuntimeError, not ValueError."""
    monkeypatch.setattr("bot.streaming.EDIT_INTERVAL_SECONDS", 1e9)

    def make_non_json_response(status: int = 500) -> MagicMock:
        resp = MagicMock()
        resp.status_code = status
        resp.json.side_effect = ValueError("not json")
        resp.text = "<html>proxy error</html>"
        resp.raise_for_status = MagicMock()
        if status >= 400:
            resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "bad", request=MagicMock(), response=resp
            )
        return resp

    http, sent = _capture_client(
        {
            "sendMessage": [_fake_response({"ok": True, "result": {"message_id": 101}})],
            "editMessageText": [make_non_json_response(status=500)],
        }
    )
    stream = TelegramStream(bot_token="tok", chat_id="-100500", http_client=http)
    with pytest.raises(RuntimeError) as excinfo:
        stream.stream(iter(["hello"]))
    assert "non-JSON" in str(excinfo.value)


def test_stream_placeholder_send_failure_raises():
    http, _ = _capture_client(
        {
            "sendMessage": [
                _fake_response({"ok": False, "description": "chat not found"}, status=400),
            ],
        }
    )
    stream = TelegramStream(bot_token="tok", chat_id="-100500", http_client=http)
    with pytest.raises((httpx.HTTPStatusError, RuntimeError)):
        stream.stream(iter(["hi"]))


def test_stream_placeholder_ok_false_raises():
    http, _ = _capture_client(
        {
            "sendMessage": [
                _fake_response({"ok": False, "description": "bot blocked"}),
            ],
        }
    )
    stream = TelegramStream(bot_token="tok", chat_id="-100500", http_client=http)
    with pytest.raises(RuntimeError, match="sendMessage failed"):
        stream.stream(iter(["hi"]))
