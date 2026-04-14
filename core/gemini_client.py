"""Gemini 2.5 Pro wrapper for commentary generation + optional news grounding.

Two capabilities:

1. ``generate_commentary(prompt)`` — plain text generation, used for short
   digest blurbs and per-stock rationales.
2. ``summarise_with_news(symbol, context)`` — fetches the last 7 days of news
   for the stock via Google Custom Search, passes the headlines to Gemini
   alongside the numeric context, and returns a grounded commentary.

The CSE fetcher gracefully degrades to no-news mode if ``GOOGLE_SEARCH_API_KEY``
or ``GOOGLE_CSE_ID`` is missing — Gemini will then respond based on its
training knowledge alone (no live-news grounding).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from google import genai

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.5-pro"
CSE_ENDPOINT = "https://www.googleapis.com/customsearch/v1"
NEWS_LOOKBACK_DAYS = 7
MAX_NEWS_ITEMS = 5


@dataclass(frozen=True)
class NewsItem:
    title: str
    snippet: str
    url: str
    source: str


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        search_api_key: str | None = None,
        cse_id: str | None = None,
        http_client: httpx.Client | None = None,
    ):
        self.model = model
        self.search_api_key = search_api_key
        self.cse_id = cse_id
        self._genai = genai.Client(api_key=api_key)
        self._http = http_client or httpx.Client(timeout=15.0)

    def generate_commentary(self, prompt: str) -> str:
        try:
            response = self._genai.models.generate_content(
                model=self.model,
                contents=prompt,
            )
            return (response.text or "").strip()
        except Exception as exc:  # noqa: BLE001 — keep the digest resilient
            logger.warning("Gemini generation failed: %s", exc)
            return "(Gemini commentary unavailable — see numbers above)"

    def fetch_news(self, query: str, *, max_results: int = MAX_NEWS_ITEMS) -> list[NewsItem]:
        if not self.search_api_key or not self.cse_id:
            return []
        try:
            resp = self._http.get(
                CSE_ENDPOINT,
                params={
                    "key": self.search_api_key,
                    "cx": self.cse_id,
                    "q": query,
                    "num": min(max_results, 10),
                    "dateRestrict": f"d{NEWS_LOOKBACK_DAYS}",
                },
            )
            if resp.status_code != 200:
                logger.warning("CSE fetch HTTP %d: %s", resp.status_code, resp.text[:200])
                return []
            items = resp.json().get("items", [])
            out: list[NewsItem] = []
            for it in items[:max_results]:
                out.append(
                    NewsItem(
                        title=it.get("title", ""),
                        snippet=it.get("snippet", ""),
                        url=it.get("link", ""),
                        source=it.get("displayLink", ""),
                    )
                )
            return out
        except httpx.HTTPError as exc:
            logger.warning("CSE fetch threw: %s", exc)
            return []

    def summarise_with_news(self, symbol: str, context: str) -> str:
        news = self.fetch_news(f'"{symbol}" stock India news')
        news_block = (
            "\n".join(f"- {n.title} ({n.source})" for n in news)
            if news
            else "(no fresh news pulled)"
        )
        prompt = (
            "You are a disciplined, neutral equity analyst summarising a CAN SLIM candidate.\n"
            "Keep the response to 3 short sentences. Mention the numeric context. "
            "Reference the news only if clearly relevant. Never say 'buy' or 'sell' outright.\n\n"
            f"Symbol: {symbol}\n"
            f"Context:\n{context}\n\n"
            f"Recent news headlines:\n{news_block}"
        )
        return self.generate_commentary(prompt)
