import httpx
import pytest

from core.telegram_client import TelegramClient, escape_html


class _FakeTransport(httpx.MockTransport):
    def __init__(self, responses):
        self._responses = list(responses)
        super().__init__(self._route)

    def _route(self, request):
        body = self._responses.pop(0)
        return httpx.Response(body[0], json=body[1])


def _client(responses):
    transport = _FakeTransport(responses)
    http = httpx.Client(transport=transport)
    return TelegramClient("token", "-123", client=http), transport


def test_send_message_ok():
    tc, _ = _client([(200, {"ok": True, "result": {"message_id": 42}})])
    result = tc.send_message("<b>hello</b>")
    assert result.ok is True
    assert result.message_id == 42


def test_send_message_business_error_raises():
    tc, _ = _client([(400, {"ok": False, "description": "Bad Request: chat not found"})])
    with pytest.raises(RuntimeError, match="chat not found"):
        tc.send_message("hi")


def test_send_message_retries_on_5xx_then_succeeds(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    tc, _ = _client(
        [
            (503, {"ok": False, "description": "Service Unavailable"}),
            (200, {"ok": True, "result": {"message_id": 7}}),
        ]
    )
    result = tc.send_message("hi")
    assert result.message_id == 7


def test_send_message_respects_retry_after_on_429(monkeypatch):
    sleep_calls = []
    monkeypatch.setattr("time.sleep", lambda s: sleep_calls.append(s))
    tc, _ = _client(
        [
            (
                429,
                {"ok": False, "description": "Too Many Requests", "parameters": {"retry_after": 7}},
            ),
            (200, {"ok": True, "result": {"message_id": 1}}),
        ]
    )
    tc.send_message("hi")
    assert sleep_calls == [7]


def test_send_document_rejects_empty_and_oversize():
    tc, _ = _client([])
    with pytest.raises(ValueError, match="empty"):
        tc.send_document(filename="x.pdf", content=b"")
    with pytest.raises(ValueError, match="50MB"):
        tc.send_document(filename="x.pdf", content=b"\x00" * (51 * 1024 * 1024))


def test_escape_html_covers_common_characters():
    assert escape_html("<script>&\"'") == "&lt;script&gt;&amp;&quot;&#x27;"
