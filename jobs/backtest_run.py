"""Manual 6-month backtest replay against the live yfinance universe.

Usage::

    set -a; source .env; set +a
    uv run python -m jobs.backtest_run

Pulls 1y of OHLCV per symbol in `core.picks_orchestrator.default_universe()`,
slices it backwards across the last six months, and reports forward-
return statistics for every pick the panel would have produced. The
results print to stdout — no Telegram side effects.

Output:
- Aggregate hit rate + average forward return
- Per-endorsing-strategy breakdown (where strategies actually passed)

Honest limitations are documented in `core/backtest.py` — read that
module's docstring before reading too much into the numbers.
"""

from __future__ import annotations

import logging
import sys
from datetime import date, timedelta

import httpx

from core.backtest import BacktestSummary, run_backtest
from core.canslim import StockFundamentals
from core.data.screener_history import (
    HistoricalFundamentals,
    historical_fundamentals_at,
)
from core.data.screener_history import (
    fetch_history as fetch_screener_history,
)
from core.nse_data import fetch_history, fetch_nifty
from core.picks_orchestrator import default_universe

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("backtest_run")

DEFAULT_LOOKBACK_DAYS = 180
DEFAULT_FORWARD_DAYS = 20
DEFAULT_STEP_DAYS = 14
DEFAULT_MIN_COMPOSITE = 30.0
DEFAULT_SUCCESS_THRESHOLD_PCT = 5.0


def _format(summary: BacktestSummary) -> str:
    lines = [
        "=" * 60,
        f"Backtest summary — {summary.n_picks} picks",
        "=" * 60,
        f"Hit rate: {summary.hit_rate_pct:.1f}%",
        f"Average forward return: {summary.avg_forward_return_pct:+.2f}%",
        f"Median forward return: {summary.median_forward_return_pct:+.2f}%",
        "",
        "Per-strategy (endorsing) breakdown:",
    ]
    if not summary.by_strategy:
        lines.append("  (no strategy formally endorsed any pick — fundamentals data is sparse)")
    for code, s in sorted(summary.by_strategy.items()):
        lines.append(
            f"  • {code}: {s.n_picks} picks, hit {s.hit_rate_pct:.1f}%, "
            f"avg fwd {s.avg_forward_return_pct:+.2f}%"
        )
    return "\n".join(lines)


def main() -> int:
    nifty = fetch_nifty()
    if nifty is None:
        logger.error("Nifty history unavailable — aborting")
        return 1

    universe = default_universe()
    logger.info("fetching histories for %d symbols", len(universe))
    histories = {}
    for sym in universe:
        h = fetch_history(sym, period="1y")
        if h is not None:
            histories[sym] = h.history
    logger.info("fetched %d histories", len(histories))

    # Load screener.in 10-year history per symbol so the panel can replay
    # against real point-in-time ratios. Single HTTP call per symbol; the
    # ratio tables come from the same /consolidated/ page screener.in already
    # serves to /screener_in.fetch_snapshot — but we hit it again here to keep
    # this job self-contained (a future PR can merge the two fetches).
    screener_history: dict[str, HistoricalFundamentals] = {}
    with httpx.Client(timeout=10.0) as http:
        for sym in histories:
            h = fetch_screener_history(sym, http_client=http)
            if h is not None:
                screener_history[sym] = h
    logger.info("fetched %d screener histories", len(screener_history))

    def _hook(symbol: str, as_of: date) -> StockFundamentals | None:
        history = screener_history.get(symbol)
        if history is None:
            return None
        return historical_fundamentals_at(history, as_of=as_of)

    today = date.today()
    summary = run_backtest(
        symbols=list(histories.keys()),
        histories=histories,
        nifty_history=nifty.history,
        start_date=today - timedelta(days=DEFAULT_LOOKBACK_DAYS),
        end_date=today - timedelta(days=DEFAULT_FORWARD_DAYS),
        forward_window_days=DEFAULT_FORWARD_DAYS,
        success_threshold_pct=DEFAULT_SUCCESS_THRESHOLD_PCT,
        step_days=DEFAULT_STEP_DAYS,
        min_composite=DEFAULT_MIN_COMPOSITE,
        extra_fundamentals_at=_hook,
    )
    print(_format(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
