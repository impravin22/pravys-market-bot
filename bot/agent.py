"""Gemini 2.5 Pro chatbot grounded in Pravy's CAN SLIM playbook.

Design:
- The CAN SLIM Playbook PDF is uploaded to the Gemini Files API once and then
  attached to every conversation as a reference document. The model reasons
  over its own training + the playbook's methodology.
- **Google Search grounding** is enabled, so Gemini fetches live Indian market
  data (prices, news, earnings) directly instead of relying on us to pre-fetch
  500 tickers on every question.
- Replies are **streamed**: callers receive chunks as Gemini produces them so
  the Telegram adapter can edit a placeholder message in near-real-time
  ("typing" that actually shows the answer materialising).

Personality (system prompt):
- Every recommendation opens with "According to Pravy's CAN SLIM philosophy, …"
- Every reply explains the WHY using the seven letters from the playbook.
- Closes with "Educational signals, not investment advice. Do your own research."
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from pathlib import Path

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = """You are Pravy's Market Bot, a conversational research
assistant for Indian equities (NSE / BSE). Your reasoning is grounded in the
CAN SLIM methodology from the attached playbook PDF. You have Google Search
enabled — use it to fetch current prices, fundamentals, earnings, and news.

HOUSE STYLE (mandatory):
- When the user asks for a recommendation, OPEN the reply with:
  "According to Pravy's CAN SLIM philosophy, …"
- Explain WHY with the seven letters (C, A, N, S, L, I, M) and the specific
  facts you found. Keep the flow scannable — short paragraphs or bullets.
- Apply the playbook's thresholds: ≥25% quarterly EPS growth, ≥20% three-year
  EPS CAGR, within ~15% of 52-week high, ≥40% volume surge, RS ≥ 80, FII/DII
  net positive, confirmed market uptrend.
- Cite the risk rules where it fits: cut losses at 7–8% below entry, take
  profits around 20–25%, keep the book to 6–8 positions, average up not down.
- Never say "buy X, it will go up". Say "X fits the CAN SLIM bar because …".
- If data is missing or contradictory, say so — never fabricate numbers.
- Close with: "Educational signals, not investment advice. Do your own research."

FORMATTING:
- Plain text. No HTML or Markdown tags. Short lines.
- For lists, use simple bullets (`•` or numbers).
"""

DEFAULT_MODEL = "gemini-2.5-pro"
GEMINI_RETRY_ATTEMPTS = 3
GEMINI_RETRY_BACKOFF_SECONDS = (1.5, 3.0)


def _is_retryable_gemini_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    if "503" in msg and ("unavailable" in msg or "overloaded" in msg or "demand" in msg):
        return True
    if "504" in msg or "502" in msg:
        return True
    return "timed out" in msg or "timeout" in msg


class HermesAgent:
    """Gemini agent with a CAN SLIM PDF reference and Google Search grounding.

    The PDF is uploaded lazily on the first streaming call so tests can
    inject a fake client and skip the network round-trip.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        playbook_path: Path | None = None,
        system_instruction: str = SYSTEM_INSTRUCTION,
    ):
        self.model = model
        self._system_instruction = system_instruction
        self._client = genai.Client(api_key=api_key)
        self._playbook_path = Path(playbook_path) if playbook_path else None
        self._playbook_file: object | None = None  # `genai.File` handle

    def _ensure_playbook(self) -> object | None:
        """Upload the playbook PDF once per process; return the cached handle."""
        if self._playbook_file is not None:
            return self._playbook_file
        if self._playbook_path is None or not self._playbook_path.exists():
            logger.info("no CAN SLIM playbook attached; continuing without document context")
            return None
        try:
            self._playbook_file = self._client.files.upload(file=str(self._playbook_path))
            logger.info("CAN SLIM playbook uploaded: %s", self._playbook_path.name)
        except Exception as exc:  # noqa: BLE001 — fail open: agent still works without PDF
            logger.warning("playbook upload failed (continuing without): %s", exc)
            self._playbook_file = None
        return self._playbook_file

    def stream_reply(self, user_message: str) -> Iterable[str]:
        """Yield non-empty text chunks as Gemini produces them.

        The caller decides whether to concatenate (for logs) or edit an open
        Telegram message (for streaming UX).
        """
        playbook = self._ensure_playbook()
        contents: list[object] = [user_message]
        if playbook is not None:
            contents.append(playbook)

        config = types.GenerateContentConfig(
            system_instruction=self._system_instruction,
            tools=[types.Tool(google_search=types.GoogleSearch())],
        )

        last_exc: Exception | None = None
        for attempt in range(1, GEMINI_RETRY_ATTEMPTS + 1):
            try:
                stream = self._client.models.generate_content_stream(
                    model=self.model,
                    contents=contents,
                    config=config,
                )
                emitted_any = False
                for chunk in stream:
                    piece = getattr(chunk, "text", None) or ""
                    if piece:
                        emitted_any = True
                        yield piece
                if not emitted_any:
                    yield "I'm not sure — can you rephrase the question?"
                return
            except Exception as exc:  # noqa: BLE001 — categorised via helper below
                last_exc = exc
                if attempt < GEMINI_RETRY_ATTEMPTS and _is_retryable_gemini_error(exc):
                    backoff = GEMINI_RETRY_BACKOFF_SECONDS[
                        min(attempt - 1, len(GEMINI_RETRY_BACKOFF_SECONDS) - 1)
                    ]
                    logger.warning(
                        "Gemini transient failure (attempt %d/%d): %s — retrying in %.1fs",
                        attempt,
                        GEMINI_RETRY_ATTEMPTS,
                        exc,
                        backoff,
                    )
                    time.sleep(backoff)
                    continue
                break

        assert last_exc is not None  # noqa: S101 — loop guarantees this
        logger.exception("Hermes agent failed on message: %s", last_exc)
        yield ("Sorry — I hit a snag fetching the market data right now. Try again in a minute.")
