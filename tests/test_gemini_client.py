from unittest.mock import MagicMock

import httpx
import pytest

from core.gemini_client import GeminiClient, NewsItem


@pytest.fixture
def fake_genai(monkeypatch):
    """Patch google.genai.Client so GeminiClient uses a fake."""
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = MagicMock(text="Gemini says hi.")

    class FakeGenaiModule:
        @staticmethod
        def Client(api_key):  # noqa: N802 — matches real API
            fake_client.api_key = api_key
            return fake_client

    monkeypatch.setattr("core.gemini_client.genai", FakeGenaiModule)
    return fake_client


def _cse_http(items):
    def handler(request):
        return httpx.Response(200, json={"items": items})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_generate_commentary_strips_whitespace(fake_genai):
    fake_genai.models.generate_content.return_value = MagicMock(text="  neutral take.  ")
    gc = GeminiClient(api_key="k")
    assert gc.generate_commentary("prompt") == "neutral take."


def test_generate_commentary_degrades_gracefully_on_exception(fake_genai):
    fake_genai.models.generate_content.side_effect = RuntimeError("quota exceeded")
    gc = GeminiClient(api_key="k")
    result = gc.generate_commentary("prompt")
    assert "unavailable" in result


def test_fetch_news_returns_empty_when_cse_unconfigured(fake_genai):
    gc = GeminiClient(api_key="k", search_api_key=None, cse_id=None)
    assert gc.fetch_news("RELIANCE") == []


def test_fetch_news_parses_cse_items(fake_genai):
    items = [
        {
            "title": "Reliance quarterly results",
            "snippet": "Strong beat on earnings",
            "link": "https://example.com/reliance-q",
            "displayLink": "example.com",
        },
        {
            "title": "RIL capex plan",
            "snippet": "Expansion into renewables",
            "link": "https://example.com/ril-capex",
            "displayLink": "example.com",
        },
    ]
    gc = GeminiClient(
        api_key="k",
        search_api_key="s",
        cse_id="c",
        http_client=_cse_http(items),
    )
    news = gc.fetch_news("RELIANCE")
    assert len(news) == 2
    assert news[0] == NewsItem(
        title="Reliance quarterly results",
        snippet="Strong beat on earnings",
        url="https://example.com/reliance-q",
        source="example.com",
    )


def test_summarise_with_news_uses_all_inputs(fake_genai):
    gc = GeminiClient(
        api_key="k",
        search_api_key="s",
        cse_id="c",
        http_client=_cse_http([]),  # no news — grounded block should say so
    )
    gc.summarise_with_news("RELIANCE", "RS 92, EPS +34%, near 52w high")
    args, kwargs = fake_genai.models.generate_content.call_args
    prompt = kwargs.get("contents") or args[-1]
    assert "RELIANCE" in prompt
    assert "RS 92" in prompt
    assert "no fresh news pulled" in prompt.lower()
