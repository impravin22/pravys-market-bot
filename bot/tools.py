"""Tools the Hermes-style Gemini agent can call to answer market questions.

Each function is a plain Python callable with typed parameters so `google-genai`
can auto-derive the function-calling schema. Keep docstrings concrete — Gemini
uses them as the tool description.

All tools return JSON-serialisable dicts so the response can be round-tripped
through the function-calling protocol cleanly.
"""

from __future__ import annotations

import logging
from pathlib import Path

from core.canslim import CanslimScore
from core.nse_data import fetch_commodity_quotes, fetch_history
from core.screener import detect_market_regime, run_screener
from core.watchlist import add_symbols, get_watchlist, remove_symbol

logger = logging.getLogger(__name__)


def _to_yahoo(symbol: str) -> str:
    """Normalise a bare NSE symbol (`RELIANCE`) to Yahoo format (`RELIANCE.NS`)."""
    s = symbol.strip().upper()
    if "." in s:
        return s
    return f"{s}.NS"


def _score_to_payload(score: CanslimScore) -> dict:
    return {
        "symbol": score.symbol,
        "binary_score": score.binary_score,
        "continuous_score": score.continuous_score,
        "passed": score.passed_codes,
        "failed": score.failed_codes,
        "letters": {
            code: {
                "code": r.code,
                "passes": r.passes,
                "magnitude": round(r.magnitude, 4),
                "note": r.note,
            }
            for code, r in score.letters.items()
        },
    }


def top_canslim_picks(limit: int = 5, min_binary: int = 5) -> dict:
    """Return the current top CAN SLIM picks across the Nifty 500 + commodity ETF universe.

    Use this when the user asks "what should I buy?", "give me top picks", or
    "best stocks right now". Results are ranked by binary count (how many of
    the 7 CAN SLIM letters pass) then by continuous tiebreak.

    Args:
        limit: How many top picks to return. Default 5, max 10.
        min_binary: Minimum CAN SLIM binary score to qualify. Default 5 of 7.
            Raise to 6 or 7 for a stricter list.

    Returns:
        Dict with keys ``regime``, ``universe_size``, ``picks`` (list of dicts
        with each stock's full CAN SLIM breakdown).
    """
    limit = max(1, min(limit, 10))
    min_binary = max(1, min(min_binary, 7))
    result = run_screener(min_binary=min_binary)
    if result is None:
        return {"error": "Screener run failed — market data unavailable."}
    return {
        "regime": {
            "phase": result.regime.phase,
            "nifty_above_50dma": result.regime.nifty_above_50dma,
            "nifty_above_200dma": result.regime.nifty_above_200dma,
            "nifty_5d_trend_up": result.regime.nifty_5d_trend_up,
        },
        "nifty_last_close": result.nifty_last_close,
        "universe_size": result.universe_size,
        "elapsed_seconds": round(result.elapsed_seconds, 1),
        "picks": [_score_to_payload(s) for s in result.scored[:limit]],
    }


def explain_canslim_for(symbol: str) -> dict:
    """Compute the full CAN SLIM 7-letter breakdown for one specific stock.

    Use this when the user asks "what's the CAN SLIM for TCS?", "is RELIANCE
    a buy?", or "why is INFY on the list?". The returned note strings
    explain each letter (e.g. "Q/Q EPS +34%").

    Args:
        symbol: The NSE ticker. Accepts either bare ("RELIANCE") or Yahoo
            format ("RELIANCE.NS") — normalised internally.
    """
    yahoo = _to_yahoo(symbol)
    # Run a single-stock screener with universe=[yahoo] so the RS percentile
    # is computed against that one point (degenerate but deterministic).
    result = run_screener(universe=[yahoo], min_binary=0)
    if result is None or not result.scored:
        # run_screener may filter by min_binary; use min_binary=0 above so
        # we always get the score back, even if the stock fails every letter.
        return {"error": f"Could not evaluate {yahoo} — no market data available."}
    return {
        "regime_phase": result.regime.phase,
        "canslim": _score_to_payload(result.scored[0]),
    }


def market_regime_now() -> dict:
    """Return the current Nifty market regime (CAN SLIM 'M' letter).

    Use this when the user asks about the market direction, "is it a good time
    to buy?", or "what phase is Nifty in?". Returns one of four MarketSmith
    India phases: confirmed-uptrend, uptrend-under-pressure, rally-attempt,
    downtrend.
    """
    from core.nse_data import fetch_nifty  # noqa: PLC0415

    nifty = fetch_nifty()
    if nifty is None:
        return {"error": "Nifty data unavailable."}
    regime = detect_market_regime(nifty)
    last_close = float(nifty.history["Close"].iloc[-1])
    return {
        "phase": regime.phase,
        "nifty_last_close": last_close,
        "above_50dma": regime.nifty_above_50dma,
        "above_200dma": regime.nifty_above_200dma,
        "five_day_trend_up": regime.nifty_5d_trend_up,
    }


def price_action(symbol: str, period: str = "1mo") -> dict:
    """Return recent price movement for a stock.

    Use this when the user asks "how has INFY moved this week?" or "what's
    RELIANCE's price today?".

    Args:
        symbol: NSE ticker (bare or .NS format).
        period: Yahoo-compatible period (1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y).
    """
    yahoo = _to_yahoo(symbol)
    hist = fetch_history(yahoo, period=period)
    if hist is None or hist.history.empty:
        return {"error": f"No price history for {yahoo} over {period}."}
    closes = hist.history["Close"].dropna()
    volumes = hist.history["Volume"].dropna()
    if len(closes) < 2:
        return {"error": f"Insufficient data for {yahoo}."}
    first = float(closes.iloc[0])
    last = float(closes.iloc[-1])
    high = float(closes.max())
    low = float(closes.min())
    avg_vol = float(volumes.mean()) if not volumes.empty else 0.0
    return {
        "symbol": yahoo,
        "period": period,
        "start_close": first,
        "last_close": last,
        "period_change_pct": round((last / first - 1.0) * 100.0, 2) if first else 0.0,
        "period_high": high,
        "period_low": low,
        "avg_volume": avg_vol,
        "days": int(len(closes)),
    }


def commodity_snapshot() -> dict:
    """Return latest gold, silver, crude, and USD/INR levels.

    Use this when the user asks about commodities, gold, silver, crude, or
    the rupee.
    """
    quotes = fetch_commodity_quotes()
    if not quotes:
        return {"error": "Commodity quotes unavailable."}
    return {
        "quotes": [
            {
                "label": q.label,
                "symbol": q.symbol,
                "last": q.last,
                "change_pct": round(q.change_pct, 2),
            }
            for q in quotes
        ],
    }


class WatchlistTools:
    """Tool set bound to one user's watchlist on a concrete JSON path.

    The store is intentionally synchronous-looking to the agent (Gemini's
    function-calling runtime is synchronous); internally we use
    `asyncio.run()` to invoke the async watchlist helpers.
    """

    def __init__(self, store_path: Path, user_id: str):
        self._path = Path(store_path)
        self._user_id = str(user_id)

    def add(self, symbol: str) -> dict:
        """Add a stock to the current user's watchlist. Returns the updated list."""
        import asyncio  # noqa: PLC0415 — optional-ish import keeps module init cheap

        yahoo = _to_yahoo(symbol)
        items = asyncio.run(add_symbols(self._path, self._user_id, [yahoo]))
        return {"ok": True, "added": yahoo, "items": items}

    def remove(self, symbol: str) -> dict:
        """Remove a stock from the current user's watchlist. Returns the updated list."""
        import asyncio  # noqa: PLC0415

        yahoo = _to_yahoo(symbol)
        items = asyncio.run(remove_symbol(self._path, self._user_id, yahoo))
        return {"ok": True, "removed": yahoo, "items": items}

    def list_items(self) -> dict:
        """Return the current user's watchlist."""
        import asyncio  # noqa: PLC0415

        items = asyncio.run(get_watchlist(self._path, self._user_id))
        return {"user_id": self._user_id, "items": list(items)}


# Default tool registry — synchronous tools safe to pass to `google-genai`.
# Watchlist tools are attached per-request with the user_id bound.
DEFAULT_TOOLS = (
    top_canslim_picks,
    explain_canslim_for,
    market_regime_now,
    price_action,
    commodity_snapshot,
)

__all__ = [
    "DEFAULT_TOOLS",
    "WatchlistTools",
    "commodity_snapshot",
    "explain_canslim_for",
    "market_regime_now",
    "price_action",
    "top_canslim_picks",
]
