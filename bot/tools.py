"""Minimal tool surface for the chatbot.

The chatbot is a Gemini 2.5 Pro agent with the CAN SLIM playbook loaded as
document context and **Google Search grounding enabled** — Gemini fetches
its own live market data. Almost nothing needs to be a local tool.

What remains here is state we own: per-user watchlists. The scheduled
morning/evening/weekly digests still use ``core.screener`` and ``core.canslim``
directly — they are not routed through tools.
"""

from __future__ import annotations

import logging
from pathlib import Path

from core.watchlist import add_symbols, get_watchlist, remove_symbol

logger = logging.getLogger(__name__)


def _to_yahoo(symbol: str) -> str:
    """Normalise a bare NSE symbol (`RELIANCE`) to Yahoo format (`RELIANCE.NS`)."""
    s = symbol.strip().upper()
    if "." in s:
        return s
    return f"{s}.NS"


class WatchlistTools:
    """Per-user watchlist operations bound to a JSON store on a concrete path.

    Gemini's function-calling runtime is synchronous, so these tool methods
    drive the async watchlist helpers via ``asyncio.run``. File I/O is
    millisecond-scale so the ergonomic cost is negligible.
    """

    def __init__(self, store_path: Path, user_id: str):
        self._path = Path(store_path)
        self._user_id = str(user_id)

    def add(self, symbol: str) -> dict:
        """Add a stock to the current user's watchlist. Returns the updated list."""
        import asyncio  # noqa: PLC0415

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


__all__ = ["WatchlistTools"]
