"""NSE market data access: holiday calendar, price history, the Nifty index itself.

Primary data source is `yfinance`. `nselib` is used for the NSE holiday list and
the Nifty 500 constituents. All network calls are wrapped with graceful
fall-back; callers must tolerate a None/empty return rather than crashing the
digest.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger(__name__)

MARKET_TZ = ZoneInfo("Asia/Kolkata")
NIFTY_TICKER = "^NSEI"
SENSEX_TICKER = "^BSESN"
BANK_NIFTY_TICKER = "^NSEBANK"
INDIA_VIX_TICKER = "^INDIAVIX"

# NSE-listed commodity ETFs — included in the CAN SLIM screener universe so
# they surface alongside equities when scoring well. Symbols verified to resolve
# on Yahoo Finance (delisted tickers like GOLDSHARE, HDFCMFGETF, KOTAKGOLD removed).
COMMODITY_ETFS = (
    "GOLDBEES.NS",  # Nippon India ETF Gold BeES — most liquid
    "AXISGOLD.NS",  # Axis Gold ETF
    "SETFGOLD.NS",  # SBI Gold ETF (NSE symbol changed from GOLDSHARE)
    "HDFCGOLD.NS",  # HDFC Gold ETF
    "BSLGOLDETF.NS",  # Aditya Birla Sun Life Gold ETF
    "SILVERBEES.NS",  # Nippon India Silver ETF
    "SILVERIETF.NS",  # ICICI Prudential Silver ETF
)

# Commodity + FX tracked as a standalone "commodities pulse" section
# (not scored via CAN SLIM — displayed as market context).
COMMODITY_TRACKERS = {
    "Gold (USD/oz)": "GC=F",
    "Silver (USD/oz)": "SI=F",
    "Crude (USD/bbl)": "CL=F",
    "USD/INR": "INR=X",
}


@dataclass(frozen=True)
class PriceBar:
    close: float
    volume: int
    high: float
    low: float


@dataclass(frozen=True)
class StockHistory:
    symbol: str
    history: pd.DataFrame  # columns: Open, High, Low, Close, Volume — tz-aware index


def today_in_market() -> date:
    """Calendar date in the NSE trading timezone (Asia/Kolkata)."""
    return datetime.now(tz=MARKET_TZ).date()


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5  # Sat=5, Sun=6


def is_trading_day(d: date, holidays: set[date] | None = None) -> bool:
    """True when NSE is expected to be open on the given date.

    Holidays must be supplied explicitly (or fetched via `nse_holidays()`). A
    missing holiday list degrades to weekend-only filtering with a warning.
    """
    if is_weekend(d):
        return False
    if holidays is None:
        logger.warning("is_trading_day: no holiday list provided, only filtering weekends")
        return True
    return d not in holidays


@lru_cache(maxsize=1)
def nse_holidays(year: int | None = None) -> set[date]:
    """Fetch NSE holiday calendar. Uses `nselib`. Falls back to empty set on failure.

    ``year`` is accepted for backward compatibility but nselib's trading holiday
    calendar endpoint is not year-filterable; it returns the current year's list.
    """
    _ = year  # signature kept for future use; nselib endpoint is not parameterisable
    try:
        from nselib.capital_market.capital_market_data import (  # noqa: PLC0415
            trading_holiday_calendar,
        )

        df = trading_holiday_calendar()
        if df is None or df.empty:
            return set()
        equities = df[df["Product"] == "Equities"] if "Product" in df.columns else df
        out: set[date] = set()
        for raw in equities.get("tradingDate", []):
            parsed = pd.to_datetime(raw, errors="coerce", format="%d-%b-%Y")
            if pd.notna(parsed):
                out.add(parsed.date())
        return out
    except Exception as exc:  # noqa: BLE001 — we want blanket protection
        logger.warning("nse_holidays fetch failed: %s", exc)
        return set()


def fetch_history(symbol: str, *, period: str = "1y", interval: str = "1d") -> StockHistory | None:
    """Fetch OHLCV for a Yahoo-format NSE symbol (e.g. 'RELIANCE.NS').

    Returns None on failure; caller must handle.
    """
    try:
        import yfinance as yf  # noqa: PLC0415

        ticker = yf.Ticker(symbol)
        hist = ticker.history(period=period, interval=interval, auto_adjust=False)
        if hist is None or hist.empty:
            logger.info("fetch_history %s returned empty", symbol)
            return None
        return StockHistory(symbol=symbol, history=hist)
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch_history %s failed: %s", symbol, exc)
        return None


def fetch_nifty(period: str = "1y") -> StockHistory | None:
    """Fetch Nifty 50 index history (^NSEI)."""
    return fetch_history(NIFTY_TICKER, period=period)


def fetch_nifty_500_symbols() -> list[str]:
    """Return the Nifty 500 constituent symbols in Yahoo format (TICKER.NS).

    `nselib` does not expose a single Nifty 500 endpoint, so we compose it from
    the four overlapping lists that together cover the same universe:
    Nifty 50 + Next 50 + Midcap 150 + Smallcap 250 = 500 tickers.
    """
    try:
        from nselib.capital_market.capital_market_data import (  # noqa: PLC0415
            nifty50_equity_list,
            niftymidcap150_equity_list,
            niftynext50_equity_list,
            niftysmallcap250_equity_list,
        )

        seen: set[str] = set()
        symbols: list[str] = []
        for fetcher in (
            nifty50_equity_list,
            niftynext50_equity_list,
            niftymidcap150_equity_list,
            niftysmallcap250_equity_list,
        ):
            try:
                df = fetcher()
            except Exception as inner:  # noqa: BLE001 — per-list failure isn't fatal
                logger.warning("%s failed: %s", fetcher.__name__, inner)
                continue
            if df is None or df.empty or "Symbol" not in df.columns:
                continue
            for raw in df["Symbol"].dropna().astype(str).str.strip():
                if raw and raw not in seen:
                    seen.add(raw)
                    symbols.append(raw)
        return [f"{s}.NS" for s in symbols]
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch_nifty_500_symbols failed: %s", exc)
        return []


@dataclass(frozen=True)
class Quote:
    symbol: str
    label: str
    last: float
    prev_close: float

    @property
    def change_pct(self) -> float:
        return (self.last / self.prev_close - 1.0) * 100.0 if self.prev_close else 0.0


def fetch_commodity_quotes() -> list[Quote]:
    """Latest prices for the commodity + FX tracker set.

    Uses yfinance 1-day history with `auto_adjust=False`. Free Yahoo quotes are
    ~15 min delayed during market hours, which is fine for the morning/evening
    digest context lines.
    """
    try:
        import yfinance as yf  # noqa: PLC0415
    except ImportError:
        return []

    out: list[Quote] = []
    for label, symbol in COMMODITY_TRACKERS.items():
        try:
            hist = yf.Ticker(symbol).history(period="5d", interval="1d", auto_adjust=False)
            if hist is None or hist.empty or len(hist) < 2:
                continue
            last = float(hist["Close"].iloc[-1])
            prev = float(hist["Close"].iloc[-2])
            out.append(Quote(symbol=symbol, label=label, last=last, prev_close=prev))
        except Exception as exc:  # noqa: BLE001
            logger.warning("fetch_commodity_quotes %s failed: %s", symbol, exc)
    return out


def fetch_fii_dii_activity() -> pd.DataFrame | None:
    """Recent FII/DII daily net flows. Returns None on failure.

    Returns a DataFrame with columns ``category``, ``date``, ``buyValue``,
    ``sellValue``, ``netValue`` — two rows per trading date (one FII/FPI, one DII).
    """
    try:
        from nselib.capital_market.capital_market_data import (  # noqa: PLC0415
            fii_dii_trading_activity,
        )

        return fii_dii_trading_activity()
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch_fii_dii_activity failed: %s", exc)
        return None
