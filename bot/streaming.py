"""Stream Gemini text chunks into a live Telegram message.

Pattern: send a placeholder, then edit it repeatedly as Gemini produces
text. Throttled to respect Telegram's ~1 edit/sec/chat rate limit.

Design decisions:
- Concatenate chunks in memory; do one edit per ``EDIT_INTERVAL_SECONDS``
  with the full accumulated text (Telegram edits replace, not append).
- Telegram caps a single message at 4096 characters. If Gemini's answer
  is longer, truncate with a clear footnote rather than spawning a
  second message.
- Final edit is always performed regardless of interval so the last
  token ends up visible.
- Transport errors on intermediate edits are logged and swallowed — a
  missed edit is fine, the next one catches up. Failure on the final
  edit is re-raised so the outer handler can release the rate-limit
  slot.
- HTML parse failure from our converter is detected by matching the
  exact Telegram description ``"Bad Request: can't parse entities"``
  and falls back to a plain-text edit. Any other 4xx propagates.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable

import httpx

from bot.markdown_to_html import markdown_to_html

logger = logging.getLogger(__name__)

PLACEHOLDER_TEXT = "⏳ Give me a sec, mate…"
EDIT_INTERVAL_SECONDS = 1.2
TELEGRAM_MAX_CHARS = 4096
TRUNCATION_SUFFIX = "\n\n… (response truncated at Telegram's 4 000 character limit)"

# Telegram's exact descriptions. We match these explicitly instead of
# fuzzy substring checks on the raw response body — a loose ``"parse" in
# body`` matched unrelated errors and hid real failures.
_TELEGRAM_NOT_MODIFIED = "bad request: message is not modified"
_TELEGRAM_PARSE_ERROR = "can't parse entities"


def _parse_response(resp: httpx.Response) -> dict:
    """Parse Telegram's JSON body, surfacing non-JSON bodies as RuntimeErrors."""
    try:
        return resp.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Telegram returned non-JSON body status={resp.status_code} "
            f"body={(resp.text or '')[:200]!r}"
        ) from exc


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
        # Track the last successfully rendered body *as it was sent to
        # Telegram* — keyed by parse mode so HTML / plain-text fallbacks
        # stay distinct and the dedup check in `_edit` stays honest.
        self._last_html: str | None = None
        self._last_plain: str | None = None
        self._last_edit_at = 0.0
        self._placeholder = placeholder_text

    def _start(self) -> None:
        resp = self._http.post(
            f"{self._base}/sendMessage",
            data={
                "chat_id": self._chat_id,
                "text": self._placeholder,
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        payload = _parse_response(resp)
        if not payload.get("ok"):
            raise RuntimeError(f"sendMessage failed: {payload.get('description')}")
        self._message_id = int(payload["result"]["message_id"])
        self._last_plain = self._placeholder

    def _try_plain_fallback(self, text: str) -> None:
        """Retry the edit without HTML parsing. Raises on non-OK."""
        plain_resp = self._http.post(
            f"{self._base}/editMessageText",
            data={
                "chat_id": self._chat_id,
                "message_id": self._message_id,
                "text": text,
            },
            timeout=15.0,
        )
        plain_payload = _parse_response(plain_resp)
        if plain_resp.status_code == 200 and plain_payload.get("ok"):
            self._last_plain = text
            return
        raise RuntimeError(
            f"editMessageText plain fallback failed "
            f"status={plain_resp.status_code} body={(plain_resp.text or '')[:200]!r}"
        )

    def _edit(self, text: str) -> None:
        if self._message_id is None:
            raise RuntimeError("edit called before start")
        rendered = markdown_to_html(text)
        if rendered == self._last_html:
            return
        resp = self._http.post(
            f"{self._base}/editMessageText",
            data={
                "chat_id": self._chat_id,
                "message_id": self._message_id,
                "text": rendered,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            },
            timeout=15.0,
        )
        payload = _parse_response(resp)
        if resp.status_code == 200 and payload.get("ok"):
            self._last_html = rendered
            return

        description = (payload.get("description") or "").lower()

        # 400 "not modified": our edit was a no-op. Record the rendered text
        # so future diffs recognise it.
        if resp.status_code == 400 and _TELEGRAM_NOT_MODIFIED in description:
            self._last_html = rendered
            return

        # 400 "can't parse entities": our HTML slipped through something the
        # converter should have caught. Retry once as plain text, preserving
        # formatting cues at the cost of styling.
        if resp.status_code == 400 and _TELEGRAM_PARSE_ERROR in description:
            logger.warning("HTML parse rejected; retrying as plain text: %s", description)
            self._try_plain_fallback(text)
            return

        raise RuntimeError(
            f"editMessageText failed status={resp.status_code} description={description!r}"
        )

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
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            logger.info("intermediate edit failed (non-fatal): %s", exc)
