"""screener.in historical tables — 10-year P&L / balance sheet / ratios / cash flow.

screener.in's company page renders five history sections we can scrape:

| HTML id | What it carries |
|---------|-----------------|
| ``profit-loss`` | annual revenue, operating profit, net profit, EPS |
| ``balance-sheet`` | borrowings, reserves, fixed/current assets, equity |
| ``ratios`` | annual ROE, ROCE, working-capital days, debt/equity |
| ``cash-flow`` | operating / investing / financing cash flow |
| ``quarters`` | last 10–12 quarters of revenue + EPS |

This module parses all five into a single `HistoricalFundamentals`
record and exposes `historical_fundamentals_at(history, as_of)` so the
backtest harness can inject point-in-time ratios — closing the
"fundamentals can't be replayed" hole.

Honest caveat: screener.in tables show **as-published** values, not
strict point-in-time. A 2022 row that gets restated in 2024 reflects
the restated value. Mild forward-bias acceptable for our scale.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from statistics import mean

import httpx
from bs4 import BeautifulSoup

from core.canslim import StockFundamentals

logger = logging.getLogger(__name__)

USER_AGENT = (
    "pravys-market-bot/0.1 (personal research; +https://github.com/impravin22/pravys-market-bot)"
)
HTTP_TIMEOUT_SECONDS = 10.0
SCREENER_BASE = "https://www.screener.in/company"

YEAR_RE = re.compile(r"(\d{4})")


# -----------------------------------------------------------------------------
# Public types
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class HistoricalFundamentals:
    """10-year history scraped from a single screener.in page."""

    symbol: str
    # Annual rows — index 0 is oldest, index -1 is latest.
    annual_years: list[int] = field(default_factory=list)
    annual_revenue: list[float | None] = field(default_factory=list)
    annual_operating_profit: list[float | None] = field(default_factory=list)
    annual_eps: list[float | None] = field(default_factory=list)
    annual_roe_pct: list[float | None] = field(default_factory=list)
    annual_roce_pct: list[float | None] = field(default_factory=list)
    annual_d_to_e: list[float | None] = field(default_factory=list)
    annual_borrowings: list[float | None] = field(default_factory=list)
    annual_reserves: list[float | None] = field(default_factory=list)
    annual_operating_cash_flow: list[float | None] = field(default_factory=list)
    # Quarterly rows — same orientation (oldest first).
    quarterly_periods: list[str] = field(default_factory=list)
    quarterly_eps: list[float | None] = field(default_factory=list)
    quarterly_sales: list[float | None] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))


# -----------------------------------------------------------------------------
# Header / number parsers
# -----------------------------------------------------------------------------


def parse_year_label(label: str) -> int | None:
    """Extract the calendar year from labels like 'Mar 2024' or 'Dec 2024'."""
    match = YEAR_RE.search(label or "")
    if not match:
        return None
    return int(match.group(1))


def _parse_number(raw: str | None) -> float | None:
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


# -----------------------------------------------------------------------------
# Section parser
# -----------------------------------------------------------------------------


def parse_section_table(html: str, section_id: str) -> dict[str, list[float | None] | list[str]]:
    """Read the first ``table.data-table`` inside ``section#{id}``.

    Returns ``{"headers": [...], "<row label>": [<values per column>], ...}``
    with the leading ``+`` decoration stripped off labels.
    Empty dict when the section or table is missing.
    """
    soup = BeautifulSoup(html, "html.parser")
    section = soup.find("section", id=section_id)
    if section is None:
        return {}
    table = section.find("table", class_="data-table")
    if table is None:
        return {}
    head = table.find("thead")
    if head is None:
        return {}
    header_cells = head.find_all("th")
    headers = [c.get_text(strip=True) for c in header_cells[1:]]  # skip leading blank cell
    out: dict[str, list[float | None] | list[str]] = {"headers": headers}
    body = table.find("tbody")
    if body is None:
        return out
    for row in body.find_all("tr"):
        cells = row.find_all("td")
        if not cells:
            continue
        label = cells[0].get_text(strip=True).rstrip("+").strip()
        values = [_parse_number(c.get_text(strip=True)) for c in cells[1:]]
        out[label] = values
    return out


# -----------------------------------------------------------------------------
# Aggregators
# -----------------------------------------------------------------------------


def compute_roe_avg_pct(values: list[float | None], *, n: int = 5) -> float | None:
    """Mean of the last ``n`` non-None values. None when nothing usable."""
    usable = [v for v in values if v is not None]
    if not usable:
        return None
    tail = usable[-n:]
    return round(mean(tail), 2)


def count_positive_years(values: list[float | None]) -> int:
    return sum(1 for v in values if v is not None and v > 0)


def compute_eps_cagr_pct(values: list[float | None]) -> float | None:
    """Endpoint CAGR over the populated EPS series. None for non-positive ends."""
    usable = [v for v in values if v is not None]
    if len(usable) < 2:
        return None
    start = usable[0]
    end = usable[-1]
    if start <= 0 or end <= 0:
        return None
    n_periods = len(usable) - 1
    cagr = (end / start) ** (1.0 / n_periods) - 1.0
    return round(cagr * 100.0, 2)


# -----------------------------------------------------------------------------
# fetch_history — HTML path with optional injection for tests
# -----------------------------------------------------------------------------


def symbol_to_url(symbol: str) -> str:
    upper = symbol.upper()
    bare = upper.removesuffix(".NS").removesuffix(".BO")
    return f"{SCREENER_BASE}/{bare}/consolidated/"


def fetch_history(
    symbol: str,
    *,
    html: str | None = None,
    http_client: httpx.Client | None = None,
) -> HistoricalFundamentals | None:
    """Build ``HistoricalFundamentals`` from a screener.in page.

    Either pass pre-fetched ``html`` (tests) or supply ``http_client``
    (runtime). Returns ``None`` on any non-200, network failure, or when
    the profit-loss section is missing — the rest of the parser tolerates
    partially-populated pages.
    """
    if html is None:
        if http_client is None:
            raise ValueError("Either html= or http_client= must be provided")
        url = symbol_to_url(symbol)
        try:
            resp = http_client.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=HTTP_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            logger.info("screener history fetch %s failed: %s", symbol, exc)
            return None
        if resp.status_code != 200:
            logger.info("screener history non-200 for %s: %s", symbol, resp.status_code)
            return None
        html = resp.text

    profit_loss = parse_section_table(html, "profit-loss")
    if not profit_loss or "headers" not in profit_loss:
        return None
    balance_sheet = parse_section_table(html, "balance-sheet")
    ratios = parse_section_table(html, "ratios")
    cash_flow = parse_section_table(html, "cash-flow")
    quarters = parse_section_table(html, "quarters")

    annual_years = [
        y for y in (parse_year_label(h) for h in profit_loss.get("headers", [])) if y is not None
    ]

    # Some labels carry minor variations across pages — tolerate the most common ones.
    def _row(table: dict, *labels: str) -> list[float | None]:
        for lbl in labels:
            if lbl in table:
                return list(table[lbl])  # type: ignore[arg-type]
        return [None] * len(annual_years)

    return HistoricalFundamentals(
        symbol=symbol,
        annual_years=annual_years,
        annual_revenue=_row(profit_loss, "Sales", "Revenue"),
        annual_operating_profit=_row(profit_loss, "Operating Profit"),
        annual_eps=_row(profit_loss, "EPS in Rs", "EPS"),
        annual_roe_pct=_row(ratios, "ROE %", "Return on Equity"),
        annual_roce_pct=_row(ratios, "ROCE %", "Return on Capital Employed"),
        annual_d_to_e=_row(ratios, "Debt / Equity", "Debt to Equity"),
        annual_borrowings=_row(balance_sheet, "Borrowings", "Total Borrowings"),
        annual_reserves=_row(balance_sheet, "Reserves"),
        annual_operating_cash_flow=_row(
            cash_flow, "Cash from Operating Activity", "Operating Cash Flow"
        ),
        quarterly_periods=list(quarters.get("headers", [])),  # type: ignore[arg-type]
        quarterly_eps=_row(quarters, "EPS in Rs", "EPS"),
        quarterly_sales=_row(quarters, "Sales", "Revenue"),
    )


# -----------------------------------------------------------------------------
# As-of helper for the backtest harness
# -----------------------------------------------------------------------------


def historical_fundamentals_at(
    history: HistoricalFundamentals,
    *,
    as_of: date,
) -> StockFundamentals | None:
    """Project ratios to a point-in-time snapshot suitable for the backtest.

    Returns ``None`` when ``as_of`` precedes every annual row in the
    history. Future dates clamp to the latest annual row.
    """
    if not history.annual_years:
        return None

    # Pick the latest annual row whose fiscal-year end (Mar X) is on or before as_of.
    latest_idx: int | None = None
    for i, year in enumerate(history.annual_years):
        # Indian fiscal year ends 31 March; if the user asks for a date before
        # 1 April of the year, that fiscal year has not closed yet.
        if as_of >= date(year, 4, 1):
            latest_idx = i
    if latest_idx is None:
        return None

    # Slice values up to and including latest_idx.
    eps = history.annual_eps[: latest_idx + 1]
    roe = history.annual_roe_pct[: latest_idx + 1]
    roce = history.annual_roce_pct[: latest_idx + 1]
    d_to_e = history.annual_d_to_e[: latest_idx + 1]

    roe_5y = compute_roe_avg_pct(roe, n=5)
    eps_3y_cagr = compute_eps_cagr_pct(eps[-4:])  # 3-year CAGR uses 4 endpoints
    eps_positive_recent = bool(eps and eps[-1] is not None and eps[-1] > 0)

    return StockFundamentals(
        symbol=history.symbol,
        quarterly_eps_yoy_pct=None,  # quarterly YoY needs current + prior-year quarter
        annual_eps_3y_cagr_pct=eps_3y_cagr,
        roe_5y_avg_pct=roe_5y,
        roce_pct=roce[-1] if roce and roce[-1] is not None else None,
        debt_to_equity=d_to_e[-1] if d_to_e and d_to_e[-1] is not None else None,
        earnings_positive_recent=eps_positive_recent,
        # Pays-dividend / dividend yield not in the historical tables we parse;
        # leave None so the strategies handle it via their data-unavailable paths.
        pays_dividend=None,
        dividend_yield_pct=None,
        # FCF-positive count surfaces via roe_5y_avg / earnings checks; the
        # Buffett strategy doesn't read FCF directly, so we don't need a field.
        # If a future strategy needs raw FCF history, expose it then.
        momentum_6m_pct=None,
        # Helpful debug hint embedded in the log for now; not consumed downstream.
        # `fcf_positive_count` could be wired into a future strategy directly.
    )
