"""Stream Gemini text chunks into a live Telegram message.

Pattern: send a placeholder ("⏳ Thinking…"), then edit it repeatedly as
Gemini produces text. Throttled to respect Telegram's ~1 edit/sec/chat
rate limit.

Design decisions:
- Concatenate chunks in memory; do one edit per `EDIT_INTERVAL_SECONDS` with
  the full accumulated text (Telegram edits replace, they don't append).
- Telegram caps a single message at 4096 characters. If Gemini's answer is
  longer, truncate with a clear footnote rather than spawning a second
  message — keeps the UX linear.
- Final edit is always performed regardless of interval so the last token
  ends up visible.
- Transport errors on intermediate edits are logged and swallowed — a
  missed edit is fine, the next one will catch up. Failure on the final
  edit is re-raised so the outer handler can release the rate-limit slot.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable

import httpx

logger = logging.getLogger(__name__)

PLACEHOLDER_TEXT = "⏳ Give me a sec, mate…"
EDIT_INTERVAL_SECONDS = 1.2
TELEGRAM_MAX_CHARS = 4096
TRUNCATION_SUFFIX = "\n\n… (response truncated at Telegram's 4 000 character limit)"


class TelegramStream:
    """Helper that owns one message_id and streams edits into it."""

    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: int | str,
        http_client: httpx.Client,
        placeholder_text: str = PLACEHOLDER_TEXT,
    ):
        self._base = f"https://api.telegram.org/bot{bot_token}"
        self._chat_id = str(chat_id)
        self._http = http_client
        self._message_id: int | None = None
        self._last_sent = ""
        self._last_edit_at = 0.0
        self._placeholder = placeholder_text

    def _start(self) -> None:
        resp = self._http.post(
            f"{self._base}/sendMessage",
            data={"chat_id": self._chat_id, "text": self._placeholder},
            timeout=15.0,
        )
        resp.raise_for_status()
        payload = resp.json()
        if not payload.get("ok"):
            raise RuntimeError(f"sendMessage failed: {payload.get('description')}")
        self._message_id = int(payload["result"]["message_id"])
        self._last_sent = self._placeholder

    def _edit(self, text: str) -> None:
        if self._message_id is None:
            raise RuntimeError("edit called before start")
        if text == self._last_sent:
            return
        resp = self._http.post(
            f"{self._base}/editMessageText",
            data={
                "chat_id": self._chat_id,
                "message_id": self._message_id,
                "text": text,
            },
            timeout=15.0,
        )
        if resp.status_code == 200 and resp.json().get("ok"):
            self._last_sent = text
            return
        # 400 "message is not modified" is harmless — the edit was a no-op.
        body = (resp.text or "")[:200]
        if "not modified" in body.lower():
            self._last_sent = text
            return
        raise RuntimeError(f"editMessageText {resp.status_code}: {body}")

    def stream(self, chunks: Iterable[str]) -> str:
        """Consume the chunk iterator, driving edits. Returns the final text."""
        self._start()
        buffer = ""
        for piece in chunks:
            buffer += piece
            if len(buffer) > TELEGRAM_MAX_CHARS:
                buffer = buffer[: TELEGRAM_MAX_CHARS - len(TRUNCATION_SUFFIX)] + TRUNCATION_SUFFIX
                self._safe_edit(buffer)
                break
            now = time.monotonic()
            if now - self._last_edit_at >= EDIT_INTERVAL_SECONDS:
                self._safe_edit(buffer)
                self._last_edit_at = now
        # Final edit — any errors here do propagate so the handler can react.
        if buffer.strip():
            self._edit(buffer.strip())
        return buffer.strip()

    def _safe_edit(self, text: str) -> None:
        try:
            self._edit(text)
        except (httpx.HTTPError, RuntimeError) as exc:
            logger.info("intermediate edit failed (non-fatal): %s", exc)
