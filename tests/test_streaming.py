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
    # 1 sendMessage (placeholder) + 1 editMessageText (final)
    endpoints = [e for e, _ in sent]
    assert endpoints[0] == "sendMessage"
    assert "editMessageText" in endpoints


def test_stream_throttles_intermediate_edits(monkeypatch):
    http, sent = _capture_client(
        {
            "sendMessage": [
                _fake_response({"ok": True, "result": {"message_id": 101}}),
            ],
            "editMessageText": [_fake_response({"ok": True, "result": {}}) for _ in range(20)],
        }
    )
    # Freeze time so the interval gate always evaluates True (edit every chunk).
    t = {"now": 0.0}

    def fake_monotonic():
        t["now"] += 10.0  # always past the interval
        return t["now"]

    monkeypatch.setattr("bot.streaming.time.monotonic", fake_monotonic)

    stream = TelegramStream(bot_token="tok", chat_id="-100500", http_client=http)
    stream.stream(iter(["a", "b", "c"]))
    endpoints = [e for e, _ in sent]
    # 1 placeholder + intermediate edits + final edit; bounded by chunk count + 1.
    assert endpoints.count("sendMessage") == 1
    assert endpoints.count("editMessageText") >= 1


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
    # sendMessage ok, but the terminal edit returns a hard error — caller must
    # see it so it can release the rate-limit slot. Suppress intermediate
    # edits so only the final edit hits the mock.
    monkeypatch.setattr("bot.streaming.EDIT_INTERVAL_SECONDS", 1e9)
    http, sent = _capture_client(
        {
            "sendMessage": [
                _fake_response({"ok": True, "result": {"message_id": 101}}),
            ],
            "editMessageText": [
                _fake_response({"ok": False, "description": "forbidden"}, status=403),
            ],
        }
    )
    stream = TelegramStream(bot_token="tok", chat_id="-100500", http_client=http)
    with pytest.raises(RuntimeError):
        stream.stream(iter(["final answer"]))
