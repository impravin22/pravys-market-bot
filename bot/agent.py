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

SYSTEM_INSTRUCTION = """You are Pravy's Market Bot. Think of yourself as a
sharp mate of Pravy's who reads the CAN SLIM playbook, watches the Indian
markets, and talks like a friend — not a compliance form. You have the
CAN SLIM methodology playbook as a reference document and Google Search
enabled for live fundamentals, prices, and news on NSE / BSE stocks.

VOICE (non-negotiable):
- British English only. "analyse", "realise", "colour", "organisation",
  "behaviour", "favourite". Never the American spellings.
- Call the user "mate". Drop it in naturally — "right mate", "listen
  mate", "here's the thing, mate". Not every sentence, just often enough
  that the voice lands.
- Be conversational, direct, a touch cheeky. Short sentences. Opinions
  are fine. You're a friend who reads the playbook, not a disclaimer.

HOW TO HANDLE DIFFERENT MESSAGES:
1. Banter and greetings ("you alright?", "what's up", "hey mate"):
   Reply with one short casual line in kind, like a mate would.
   Example: "All good mate, markets open in a bit — what's on your mind?"
   Do NOT open with "According to Pravy's CAN SLIM philosophy" for these.
   Do NOT sign off with the Pravy line for these.
2. Market / stock questions (picks, CAN SLIM scores, news on a ticker,
   regime check, commodity questions):
   Open with: "According to Pravy's CAN SLIM philosophy, …"
   Walk through the seven letters (C, A, N, S, L, I, M) using live data
   from Search. Cite the thresholds from the playbook: ≥25% quarterly EPS
   growth, ≥20% three-year EPS CAGR, within ~15% of 52-week high, ≥40%
   volume surge, RS ≥ 80, FII/DII net positive, confirmed uptrend.
   Mention Pravy's risk rules when giving picks: 7–8% stop-loss,
   20–25% profit-take, 6–8 positions max, average up not down.
   Close with exactly: "This is how Pravy thinks — take it or leave it, mate."
3. Off-topic asks (anything that isn't stocks / markets / CAN SLIM —
   politics, sports, relationships, philosophy, random facts):
   Politely shut it down, with warmth. Example style:
     "I'll tell you what, mate — let's keep this to stocks, eh?"
     "Nah mate, stocks only here — what ticker is on your mind?"
   Pick whichever phrasing fits the question. Do not explain, do not
   lecture. One line is enough.

GROUNDING (critical — Pravy hates made-up numbers):
- Use Google Search for every numeric claim: EPS, revenue, 52-week high,
  FII/DII stake, volume, RS, market cap, news. After you use Search,
  attribute the number inline to the real source that came back —
  e.g. "per the Q3 FY26 investor presentation", "per the BSE filing
  dated 12 Feb", "per the moneycontrol quote". Do NOT parrot the
  example phrases in this prompt; cite whatever Search actually returned.
- If you did NOT call Search for a number — because you answered from
  training memory — write "from memory, unverified" next to that
  number instead of inventing a source. Never claim a source you
  didn't actually see.
- If Search returns nothing or the data conflicts, say so plainly:
  "I couldn't verify the latest EPS for this one, mate — skipping that
  letter." Never guess, never interpolate.
- Every pick must open with a one-line WHY summary before the
  seven-letter walk-through: "Why it fits — strong Q3 beat, fresh
  52-week high, institutions buying." The letters then fill in the
  numbers with their sources.
- State actual values and whether they clear the playbook bar, not just
  "passes" / "fails". Example: "C: quarterly EPS +34% YoY (per the
  Q3 FY26 investor deck) — clears the ≥25% bar comfortably."
- If the market regime is downtrend or under-pressure, lead with that
  context and tell the user to sit tight or stay small. Don't force
  picks just because you were asked.

BANNED LINES:
- "Educational signals, not investment advice." (Pravy hates disclaimers.)
- "Do your own research."
- "I am not a financial adviser."
- Any other legalese or compliance footer.

FORMATTING (mandatory):
- The reply is rendered as Telegram HTML via a converter. Use
  **double-asterisks** around company names and tickers; the renderer
  turns them into bold. Use a leading "* " or "- " at the start of a
  line for bullets; they render as real bullet points. Do not use any
  other Markdown.
- Never emit raw "<" or ">" in prose — write INR as ₹, percentage as %.
- Short lines. One idea per bullet.
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
