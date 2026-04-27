"""screener.in HTML parser tests with synthetic fixtures.

Fixtures mirror the real screener.in markup at the level the parser
cares about: a `#top-ratios` block of `li` items with a `name` and
`number` span. We do not exercise the live site in CI.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from core.data.screener_in import (
    ScreenerSnapshot,
    fetch_snapshot,
    parse_top_ratios,
    symbol_to_url,
)

FIXTURE_OK = """
<html>
  <head><title>Reliance Industries</title></head>
  <body>
    <ul id="top-ratios">
      <li class="flex">
        <span class="name">Market Cap</span>
        <span class="number">17,12,345</span>
      </li>
      <li class="flex">
        <span class="name">Current Price</span>
        <span class="number">2,520</span>
      </li>
      <li class="flex">
        <span class="name">Stock P/E</span>
        <span class="number">28.4</span>
      </li>
      <li class="flex">
        <span class="name">Book Value</span>
        <span class="number">1,256</span>
      </li>
      <li class="flex">
        <span class="name">Dividend Yield</span>
        <span class="number">0.40 %</span>
      </li>
      <li class="flex">
        <span class="name">ROCE</span>
        <span class="number">11.2 %</span>
      </li>
      <li class="flex">
        <span class="name">ROE</span>
        <span class="number">8.7 %</span>
      </li>
      <li class="flex">
        <span class="name">Debt to equity</span>
        <span class="number">0.34</span>
      </li>
    </ul>
  </body>
</html>
"""

FIXTURE_MISSING_FIELDS = """
<html>
  <body>
    <ul id="top-ratios">
      <li class="flex">
        <span class="name">Current Price</span>
        <span class="number">100</span>
      </li>
    </ul>
  </body>
</html>
"""

FIXTURE_NO_TOP_RATIOS = "<html><body><h1>nothing here</h1></body></html>"


# -----------------------------------------------------------------------------
# symbol_to_url
# -----------------------------------------------------------------------------


def test_symbol_to_url_strips_ns_suffix():
    assert symbol_to_url("RELIANCE.NS") == "https://www.screener.in/company/RELIANCE/consolidated/"


def test_symbol_to_url_accepts_bare_symbol():
    assert symbol_to_url("TCS") == "https://www.screener.in/company/TCS/consolidated/"


def test_symbol_to_url_uppercases():
    assert symbol_to_url("infy.ns") == "https://www.screener.in/company/INFY/consolidated/"


# -----------------------------------------------------------------------------
# parse_top_ratios
# -----------------------------------------------------------------------------


def test_parse_top_ratios_extracts_all_known_fields():
    ratios = parse_top_ratios(FIXTURE_OK)
    assert ratios["pe_ratio"] == 28.4
    assert ratios["dividend_yield_pct"] == 0.40
    assert ratios["roe_pct"] == 8.7
    assert ratios["roce_pct"] == 11.2
    assert ratios["debt_to_equity"] == 0.34
    assert ratios["current_price"] == 2520.0
    assert ratios["book_value"] == 1256.0


def test_parse_top_ratios_handles_missing_fields():
    """Missing keys come back as None, never raise."""
    ratios = parse_top_ratios(FIXTURE_MISSING_FIELDS)
    assert ratios["current_price"] == 100.0
    assert ratios["pe_ratio"] is None
    assert ratios["roe_pct"] is None


def test_fetch_snapshot_derives_pb_from_price_and_book_value():
    http = MagicMock()
    http.get.return_value = _mock_resp()
    snap = fetch_snapshot("X.NS", http_client=http)
    assert snap is not None
    assert snap.pb_ratio == pytest.approx(2520 / 1256, rel=1e-3)


def test_fetch_snapshot_pb_is_none_when_book_value_missing():
    http = MagicMock()
    http.get.return_value = _mock_resp(body=FIXTURE_MISSING_FIELDS)
    snap = fetch_snapshot("X.NS", http_client=http)
    assert snap is not None
    assert snap.pb_ratio is None


def test_parse_top_ratios_returns_empty_when_block_missing():
    ratios = parse_top_ratios(FIXTURE_NO_TOP_RATIOS)
    assert all(v is None for v in ratios.values())


def test_parse_top_ratios_strips_commas_and_percent_signs():
    ratios = parse_top_ratios(FIXTURE_OK)
    # Market cap and Dividend Yield use commas and % — must parse cleanly.
    assert ratios["market_cap"] == 1712345.0
    assert ratios["dividend_yield_pct"] == 0.40


# -----------------------------------------------------------------------------
# fetch_snapshot — happy + sad paths, all HTTP mocked
# -----------------------------------------------------------------------------


def _mock_resp(*, status: int = 200, body: str = FIXTURE_OK) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = body
    return resp


def test_fetch_snapshot_returns_populated_snapshot():
    http = MagicMock()
    http.get.return_value = _mock_resp()
    snap = fetch_snapshot("RELIANCE.NS", http_client=http)
    assert isinstance(snap, ScreenerSnapshot)
    assert snap.symbol == "RELIANCE.NS"
    assert snap.pe_ratio == 28.4
    assert snap.debt_to_equity == 0.34
    assert snap.dividend_yield_pct == 0.40
    assert snap.roe_5y_avg_pct == 8.7  # we currently use the headline ROE
    assert snap.pays_dividend is True  # dividend yield > 0


def test_fetch_snapshot_sets_pays_dividend_false_on_zero_yield():
    body = FIXTURE_OK.replace("0.40 %", "0.00 %")
    http = MagicMock()
    http.get.return_value = _mock_resp(body=body)
    snap = fetch_snapshot("X.NS", http_client=http)
    assert snap.pays_dividend is False


def test_fetch_snapshot_returns_none_on_404():
    http = MagicMock()
    http.get.return_value = _mock_resp(status=404, body="not found")
    snap = fetch_snapshot("UNKNOWN", http_client=http)
    assert snap is None


def test_fetch_snapshot_returns_none_on_5xx():
    http = MagicMock()
    http.get.return_value = _mock_resp(status=502, body="bad gateway")
    snap = fetch_snapshot("X", http_client=http)
    assert snap is None


def test_fetch_snapshot_returns_none_on_network_error():
    http = MagicMock()
    http.get.side_effect = httpx.ConnectError("dns failed")
    snap = fetch_snapshot("X", http_client=http)
    assert snap is None


def test_fetch_snapshot_uses_polite_user_agent():
    """Sets a self-identifying UA — basic netiquette for free scraping."""
    http = MagicMock()
    http.get.return_value = _mock_resp()
    fetch_snapshot("X", http_client=http)
    headers = http.get.call_args.kwargs.get("headers", {})
    assert "User-Agent" in headers
    assert "pravys-market-bot" in headers["User-Agent"].lower()
