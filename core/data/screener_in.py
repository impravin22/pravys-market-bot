"""screener.in HTML adapter — extracts the top-ratios block for a stock.

screener.in's company page (e.g. https://www.screener.in/company/RELIANCE/consolidated/)
exposes a ``<ul id="top-ratios">`` block listing Market Cap, Current Price,
P/E, Book Value, Dividend Yield, ROCE, ROE, Debt-to-Equity, etc. We parse
the headline figures and synthesise P/B = Current Price / Book Value
ourselves (screener.in does not surface P/B directly in that block).

Limits:
- Free, no auth. Rate-limit yourself; the cache layer (``core.data.cache``)
  enforces a 24-hour TTL by default.
- HTML structure can change. Failures fall back to ``None`` per field; the
  calling strategy then fails its own check naturally.
- ``ROE`` here is the headline current-period ROE. The 5-year average
  Buffett-style ratio needs the historical ROE table — phase 2 work.

This adapter never raises. It returns ``None`` if the page is missing,
non-200, or unparseable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup

from core.canslim import StockFundamentals

logger = logging.getLogger(__name__)

USER_AGENT = (
    "pravys-market-bot/0.1 (personal research; +https://github.com/impravin22/pravys-market-bot)"
)
HTTP_TIMEOUT_SECONDS = 10.0
SCREENER_BASE = "https://www.screener.in/company"

# Mapping screener.in label → our snapshot field.
_FIELD_MAP: dict[str, str] = {
    "Market Cap": "market_cap",
    "Current Price": "current_price",
    "Stock P/E": "pe_ratio",
    "Book Value": "book_value",
    "Dividend Yield": "dividend_yield_pct",
    "ROCE": "roce_pct",
    "ROE": "roe_pct",
    "Debt to equity": "debt_to_equity",
    "Face Value": "face_value",
}


@dataclass(frozen=True)
class ScreenerSnapshot:
    """A single-page snapshot of screener.in's headline figures."""

    symbol: str
    market_cap: float | None
    current_price: float | None
    pe_ratio: float | None
    pb_ratio: float | None
    book_value: float | None
    dividend_yield_pct: float | None
    pays_dividend: bool | None
    roe_pct: float | None
    roe_5y_avg_pct: float | None  # currently equals roe_pct; 5y history is phase-2
    roce_pct: float | None
    debt_to_equity: float | None
    face_value: float | None
    fetched_at: datetime


# -----------------------------------------------------------------------------
# URL helpers
# -----------------------------------------------------------------------------


def symbol_to_url(symbol: str) -> str:
    """Convert ``RELIANCE.NS`` (or lower-case ``infy.ns``) to the consolidated company URL."""
    upper = symbol.upper()
    bare = upper.removesuffix(".NS").removesuffix(".BO")
    return f"{SCREENER_BASE}/{bare}/consolidated/"


# -----------------------------------------------------------------------------
# Parser
# -----------------------------------------------------------------------------


def _parse_number(raw: str | None) -> float | None:
    """Strip commas, % signs, currency markers; return float or None."""
    if raw is None:
        return None
    cleaned = (
        raw.strip().replace(",", "").replace("%", "").replace("₹", "").replace("Cr.", "").strip()
    )
    if not cleaned or cleaned in {"-", "—"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_top_ratios(html: str) -> dict[str, float | None]:
    """Extract the top-ratios block. Returns {field: float | None} for known names."""
    out: dict[str, float | None] = {field: None for field in _FIELD_MAP.values()}
    soup = BeautifulSoup(html, "html.parser")
    block = soup.find("ul", id="top-ratios")
    if block is None:
        return out
    for li in block.find_all("li"):
        name_el = li.find("span", class_="name")
        number_el = li.find("span", class_="number")
        if name_el is None or number_el is None:
            continue
        label = name_el.get_text(strip=True)
        if label not in _FIELD_MAP:
            continue
        out[_FIELD_MAP[label]] = _parse_number(number_el.get_text(strip=True))
    return out


# -----------------------------------------------------------------------------
# fetch
# -----------------------------------------------------------------------------


def fetch_snapshot(symbol: str, *, http_client: httpx.Client) -> ScreenerSnapshot | None:
    """GET screener.in for the symbol and parse a snapshot. Never raises.

    Returns ``None`` for any non-200, network failure, or parse failure.
    """
    url = symbol_to_url(symbol)
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = http_client.get(url, headers=headers, timeout=HTTP_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        logger.info("screener.in fetch %s failed: %s", symbol, exc)
        return None
    if resp.status_code != 200:
        logger.info("screener.in non-200 for %s: %s", symbol, resp.status_code)
        return None

    ratios = parse_top_ratios(resp.text)
    return _snapshot_from_ratios(symbol, ratios)


def _snapshot_from_ratios(symbol: str, ratios: dict[str, Any]) -> ScreenerSnapshot:
    """Derive composite fields (P/B, pays_dividend) from headline ratios."""
    current_price = ratios.get("current_price")
    book_value = ratios.get("book_value")
    pb_ratio: float | None = None
    if current_price is not None and book_value not in (None, 0):
        pb_ratio = round(current_price / book_value, 4)

    div_yield = ratios.get("dividend_yield_pct")
    pays_dividend: bool | None = None
    if div_yield is not None:
        pays_dividend = div_yield > 0.0

    roe_pct = ratios.get("roe_pct")
    return ScreenerSnapshot(
        symbol=symbol,
        market_cap=ratios.get("market_cap"),
        current_price=current_price,
        pe_ratio=ratios.get("pe_ratio"),
        pb_ratio=pb_ratio,
        book_value=book_value,
        dividend_yield_pct=div_yield,
        pays_dividend=pays_dividend,
        roe_pct=roe_pct,
        roe_5y_avg_pct=roe_pct,  # phase-1 approximation
        roce_pct=ratios.get("roce_pct"),
        debt_to_equity=ratios.get("debt_to_equity"),
        face_value=ratios.get("face_value"),
        fetched_at=datetime.now(tz=UTC),
    )


# -----------------------------------------------------------------------------
# StockFundamentals enrichment
# -----------------------------------------------------------------------------


def enrich_fundamentals_with_snapshot(
    base: StockFundamentals,
    snapshot: ScreenerSnapshot | None,
) -> StockFundamentals:
    """Merge a snapshot's ratio fields into a `StockFundamentals` record.

    Only backfills fields that are currently ``None`` on ``base`` — explicit
    values from the caller win. Returns the same instance if ``snapshot`` is
    ``None`` (no-op fast path).
    """
    if snapshot is None:
        return base

    updates: dict[str, Any] = {}
    if base.pe_ratio is None and snapshot.pe_ratio is not None:
        updates["pe_ratio"] = snapshot.pe_ratio
    if base.pb_ratio is None and snapshot.pb_ratio is not None:
        updates["pb_ratio"] = snapshot.pb_ratio
    if base.debt_to_equity is None and snapshot.debt_to_equity is not None:
        updates["debt_to_equity"] = snapshot.debt_to_equity
    if base.roe_5y_avg_pct is None and snapshot.roe_5y_avg_pct is not None:
        updates["roe_5y_avg_pct"] = snapshot.roe_5y_avg_pct
    if base.dividend_yield_pct is None and snapshot.dividend_yield_pct is not None:
        updates["dividend_yield_pct"] = snapshot.dividend_yield_pct
    if base.pays_dividend is None and snapshot.pays_dividend is not None:
        updates["pays_dividend"] = snapshot.pays_dividend
    # Derive `earnings_positive_recent` from a positive P/E — a positive
    # ratio implies positive trailing EPS.
    if (
        base.earnings_positive_recent is None
        and snapshot.pe_ratio is not None
        and snapshot.pe_ratio > 0
    ):
        updates["earnings_positive_recent"] = True

    return replace(base, **updates) if updates else base
