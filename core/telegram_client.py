"""Telegram Bot API client — sendMessage + sendDocument with retry.

Uses `httpx` (sync client). Mirrors the retry pattern used in
`blendnbubbles/scripts/daily-digest/src/telegram.js`: exponential backoff,
5xx / 429 retried with `retry_after` respected, 4xx surfaced as errors.
"""

from __future__ import annotations

import html
import logging
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"
RETRY_COUNT = 3
REQUEST_TIMEOUT_SECONDS = 20.0
DOCUMENT_TIMEOUT_SECONDS = 60.0
DOCUMENT_BYTE_LIMIT = 50 * 1024 * 1024


@dataclass(frozen=True)
class TelegramSendResult:
    message_id: int
    ok: bool


class TelegramClient:
    def __init__(self, bot_token: str, chat_id: str, *, client: httpx.Client | None = None):
        self.bot_token = bot_token
        self.chat_id = str(chat_id)
        self.base = f"{TELEGRAM_API}/bot{bot_token}"
        self._client = client or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)

    def send_message(self, text: str, *, parse_mode: str | None = "HTML") -> TelegramSendResult:
        """Send a Telegram message.

        Pass ``parse_mode=None`` to skip HTML/Markdown parsing — required
        for any free-form LLM output that could contain stray ``<``/``>``.
        """
        data: dict[str, str] = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
        if parse_mode:
            data["parse_mode"] = parse_mode
        return self._call("sendMessage", data=data)

    def send_chat_action(self, action: str = "typing") -> None:
        """Send a non-blocking chat action (``typing``, ``upload_document``).

        Telegram auto-expires the action after ~5 s, so this must be refreshed
        for long-running operations. Silently swallows transport errors — a
        missing typing indicator should never break the actual reply flow.
        """
        try:
            self._client.post(
                f"{self.base}/sendChatAction",
                data={"chat_id": self.chat_id, "action": action},
                timeout=5.0,
            )
        except httpx.HTTPError as exc:
            logger.info("sendChatAction %s failed (non-fatal): %s", action, exc)

    def send_document(
        self,
        *,
        filename: str,
        content: bytes,
        mime_type: str = "application/octet-stream",
        caption: str | None = None,
    ) -> TelegramSendResult:
        if len(content) == 0:
            raise ValueError("send_document: empty content")
        if len(content) > DOCUMENT_BYTE_LIMIT:
            raise ValueError(
                f"send_document: content is {len(content)} bytes, above Telegram's 50MB limit"
            )
        data: dict[str, str] = {"chat_id": self.chat_id}
        if caption:
            data["caption"] = caption
        files = {"document": (filename, content, mime_type)}
        return self._call("sendDocument", data=data, files=files, timeout=DOCUMENT_TIMEOUT_SECONDS)

    def _call(
        self,
        method: str,
        *,
        data: dict[str, str],
        files: dict | None = None,
        timeout: float | None = None,
    ) -> TelegramSendResult:
        last_error: Exception | None = None
        for attempt in range(1, RETRY_COUNT + 1):
            try:
                resp = self._client.post(
                    f"{self.base}/{method}",
                    data=data,
                    files=files,
                    timeout=timeout or REQUEST_TIMEOUT_SECONDS,
                )
                payload = resp.json()
                if payload.get("ok"):
                    return TelegramSendResult(
                        message_id=int(payload["result"].get("message_id", 0)),
                        ok=True,
                    )
                retry_after = (payload.get("parameters") or {}).get("retry_after")
                retryable = resp.status_code >= 500 or resp.status_code == 429
                if retryable and attempt < RETRY_COUNT:
                    sleep_for = retry_after if retry_after else 2**attempt
                    logger.warning(
                        "Telegram %s retryable (%d): %s — sleeping %ss",
                        method,
                        resp.status_code,
                        payload.get("description"),
                        sleep_for,
                    )
                    time.sleep(sleep_for)
                    continue
                raise RuntimeError(
                    f"Telegram {method} failed ({resp.status_code}): "
                    f"{payload.get('description', 'unknown error')}"
                )
            except (httpx.HTTPError, RuntimeError) as exc:
                last_error = exc
                if isinstance(exc, RuntimeError):
                    # Non-retryable business error — bubble immediately.
                    raise
                if attempt < RETRY_COUNT:
                    sleep_for = 2**attempt
                    logger.warning(
                        "Telegram %s transport error: %s — retrying in %ds", method, exc, sleep_for
                    )
                    time.sleep(sleep_for)
                    continue
                break
        assert last_error is not None  # noqa: S101 — defensive; the loop above guarantees this
        raise last_error


def escape_html(value: object) -> str:
    """Escape ``<>&"'`` for Telegram HTML parse mode."""
    return html.escape(str(value), quote=True)
