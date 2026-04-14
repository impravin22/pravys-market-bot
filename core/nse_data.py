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
# they surface alongside equities when scoring well.
COMMODITY_ETFS = (
    "GOLDBEES.NS",  # Nippon India ETF Gold BeES — most liquid
    "AXISGOLD.NS",  # Axis Gold ETF
    "GOLDSHARE.NS",  # SBI Gold ETF
    "HDFCMFGETF.NS",  # HDFC Gold ETF
    "KOTAKGOLD.NS",  # Kotak Gold ETF
    "SILVERBEES.NS",  # Nippon India Silver ETF
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
    """Fetch NSE holiday calendar. Uses `nselib`. Falls back to empty set on failure."""
    try:
        from nselib import capital_market  # noqa: PLC0415

        year = year or today_in_market().year
        df = capital_market.nse_holiday_list(year=year)
        # nselib returns a DataFrame; date column typically "Date"
        if df is None or df.empty:
            return set()
        out: set[date] = set()
        for raw in df.get("Date", []):
            parsed = pd.to_datetime(raw, errors="coerce")
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
    """Return the Nifty 500 constituent symbols in Yahoo format (TICKER.NS)."""
    try:
        from nselib import capital_market  # noqa: PLC0415

        df = capital_market.nifty500_equity_list()
        if df is None or df.empty:
            return []
        symbols = df["Symbol"].dropna().astype(str).str.strip().tolist()
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
    """Recent FII/DII daily net flows. Returns None on failure."""
    try:
        from nselib import capital_market  # noqa: PLC0415

        return capital_market.fii_dii_trading_activity()
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch_fii_dii_activity failed: %s", exc)
        return None
