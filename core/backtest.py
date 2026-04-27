"""6-month price-derived backtest harness.

Replays the daily-picks panel against historical OHLCV and measures
forward returns of the picks at each step. This validates the **price-
and momentum-derived** signals in the panel (CAN SLIM N/S/L/M, Schloss
near-low, Trending Value momentum). It does NOT validate
fundamentals-derived signals truthfully:

- screener.in only exposes today's snapshot — there is no historical
  ratios endpoint we can replay against. If fundamentals are passed
  in via `extra_fundamentals_at`, they are honoured, but the default
  path leaves them ``None`` and the strategy falls back gracefully.
- This biases the harness toward CAN SLIM / Schloss / Trending Value
  validation. Buffett / Graham / Lynch / Magic Formula will produce
  little signal here without an external fundamentals time-series.

For the user's first sanity-check on the engine end-to-end, that is
acceptable. Treat the hit rate as a lower bound on the price-derived
strategies, not as proof of the full panel's edge.

Survivorship bias: picks are evaluated only on symbols that survived
to today. Stocks that delisted or got wiped are absent from the
universe by construction.
"""

from __future__ import annotations

import logging
import statistics
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from core.canslim import StockFundamentals
from core.daily_picks import daily_picks
from core.fundamentals import fundamentals_from_history
from core.nse_data import StockHistory
from core.rs_rating import ReturnPoint, compute_12m_return, rank_by_return
from core.screener import detect_market_regime
from core.strategies import all_strategies

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BacktestSample:
    as_of: date
    symbol: str
    composite_rating: float
    endorsing_codes: list[str]
    forward_return_pct: float | None
    hit: bool


@dataclass(frozen=True)
class BacktestStrategySummary:
    code: str
    n_picks: int
    n_hits: int
    avg_forward_return_pct: float

    @property
    def hit_rate_pct(self) -> float:
        if self.n_picks == 0:
            return 0.0
        return round(self.n_hits / self.n_picks * 100.0, 1)


@dataclass(frozen=True)
class BacktestSummary:
    n_picks: int
    hit_rate_pct: float
    avg_forward_return_pct: float
    median_forward_return_pct: float
    by_strategy: dict[str, BacktestStrategySummary] = field(default_factory=dict)
    samples: list[BacktestSample] = field(default_factory=list)


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------


def iter_as_of_dates(
    *,
    start: date,
    end: date,
    step_days: int = 7,
) -> Iterable[date]:
    """Yield as-of dates from start to end inclusive, skipping weekends.

    A typical 6-month replay uses ``step_days=7`` (one snapshot per week).
    """
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:  # Mon-Fri
            yield cursor
        cursor += timedelta(days=step_days)


def slice_history(df: pd.DataFrame, *, as_of: date) -> pd.DataFrame:
    """Return rows on or before ``as_of``."""
    if df is None or df.empty:
        return df
    cutoff = pd.Timestamp(as_of).normalize() + pd.Timedelta(days=1)
    return df[df.index < cutoff]


def forward_return_pct(df: pd.DataFrame, *, as_of: date, forward_days: int) -> float | None:
    """Return % change from the close at as_of to the close `forward_days` later.

    Returns None if either bookend is missing.
    """
    if df is None or df.empty:
        return None
    sliced = slice_history(df, as_of=as_of)
    if sliced.empty:
        return None
    start_close = float(sliced["Close"].dropna().iloc[-1])
    if start_close <= 0:
        return None
    end_idx = pd.Timestamp(as_of) + pd.Timedelta(days=forward_days * 2)
    forward = df[(df.index > sliced.index[-1]) & (df.index <= end_idx)]
    if len(forward) < forward_days:
        return None
    end_close = float(forward["Close"].dropna().iloc[forward_days - 1])
    return round((end_close / start_close - 1.0) * 100.0, 2)


def run_backtest(
    *,
    symbols: list[str],
    histories: dict[str, pd.DataFrame],
    nifty_history: pd.DataFrame,
    start_date: date,
    end_date: date,
    forward_window_days: int = 20,
    success_threshold_pct: float = 3.0,
    step_days: int = 7,
    min_composite: float = 60.0,
    extra_fundamentals_at: Callable[[str, date], StockFundamentals | None] | None = None,
) -> BacktestSummary:
    """Replay the panel across the window and aggregate forward-return outcomes.

    `extra_fundamentals_at(symbol, as_of)` is an optional hook that lets
    callers feed historical valuation snapshots into the picks panel. The
    default keeps fundamentals at None; only price-derived signals fire.
    """
    strategies = all_strategies()
    samples: list[BacktestSample] = []
    by_strategy_buckets: dict[str, list[BacktestSample]] = {}

    for as_of in iter_as_of_dates(start=start_date, end=end_date, step_days=step_days):
        sliced_nifty = slice_history(nifty_history, as_of=as_of)
        if len(sliced_nifty) < 200:
            continue
        regime = _regime_from_history(sliced_nifty)

        # Pre-compute RS rating across the sliced universe.
        rs_inputs = []
        sliced_histories: dict[str, pd.DataFrame] = {}
        for sym in symbols:
            h = histories.get(sym)
            if h is None:
                continue
            sliced = slice_history(h, as_of=as_of)
            if len(sliced) < 130:
                continue
            sliced_histories[sym] = sliced
            r = compute_12m_return(sliced["Close"].dropna().tolist())
            if r is not None:
                rs_inputs.append(ReturnPoint(symbol=sym, total_return=r))
        rs_ratings = rank_by_return(rs_inputs)

        fundamentals_list: list[StockFundamentals] = []
        for sym, sliced in sliced_histories.items():
            history_obj = StockHistory(symbol=sym, history=sliced)
            base = fundamentals_from_history(sym, history_obj)
            extra = extra_fundamentals_at(sym, as_of) if extra_fundamentals_at is not None else None
            merged = _merge(base, extra, rs_rating=rs_ratings.get(sym))
            fundamentals_list.append(merged)

        picks = daily_picks(
            fundamentals_list,
            regime,
            strategies,
            top_n=int(1e9),  # take everyone over the threshold for backtest stats
            min_composite=min_composite,
            block_in_downtrend=False,  # measure even in tough regimes
        )
        for pick in picks:
            ret = forward_return_pct(
                histories[pick.symbol],
                as_of=as_of,
                forward_days=forward_window_days,
            )
            sample = BacktestSample(
                as_of=as_of,
                symbol=pick.symbol,
                composite_rating=pick.composite_rating,
                endorsing_codes=list(pick.endorsing_codes),
                forward_return_pct=ret,
                hit=ret is not None and ret >= success_threshold_pct,
            )
            samples.append(sample)
            for code in pick.endorsing_codes:
                by_strategy_buckets.setdefault(code, []).append(sample)

    return _summarise(samples, by_strategy_buckets)


# -----------------------------------------------------------------------------
# Internals
# -----------------------------------------------------------------------------


def _regime_from_history(sliced_nifty: pd.DataFrame):
    """Build a `MarketRegime` from a Nifty slice via the existing detector."""
    return detect_market_regime(StockHistory(symbol="NIFTY", history=sliced_nifty))


def _merge(
    base: StockFundamentals,
    extra: StockFundamentals | None,
    *,
    rs_rating: float | None,
) -> StockFundamentals:
    """Combine the price-derived `base` with an optional fundamentals snapshot."""
    if extra is None:
        return StockFundamentals(
            symbol=base.symbol,
            last_close=base.last_close,
            high_52w=base.high_52w,
            low_52w=base.low_52w,
            avg_vol_50d=base.avg_vol_50d,
            last_volume=base.last_volume,
            quarterly_eps_yoy_pct=base.quarterly_eps_yoy_pct,
            annual_eps_3y_cagr_pct=base.annual_eps_3y_cagr_pct,
            rs_rating=rs_rating,
            momentum_6m_pct=_momentum_from_base(base),
        )
    return StockFundamentals(
        symbol=base.symbol,
        last_close=base.last_close,
        high_52w=base.high_52w,
        low_52w=base.low_52w,
        avg_vol_50d=base.avg_vol_50d,
        last_volume=base.last_volume,
        quarterly_eps_yoy_pct=extra.quarterly_eps_yoy_pct or base.quarterly_eps_yoy_pct,
        annual_eps_3y_cagr_pct=extra.annual_eps_3y_cagr_pct or base.annual_eps_3y_cagr_pct,
        rs_rating=rs_rating,
        fii_dii_5d_net_positive=extra.fii_dii_5d_net_positive,
        pe_ratio=extra.pe_ratio,
        pb_ratio=extra.pb_ratio,
        ps_ratio=extra.ps_ratio,
        pcf_ratio=extra.pcf_ratio,
        ev_ebitda=extra.ev_ebitda,
        debt_to_equity=extra.debt_to_equity,
        current_ratio=extra.current_ratio,
        roe_5y_avg_pct=extra.roe_5y_avg_pct,
        roce_pct=extra.roce_pct,
        dividend_yield_pct=extra.dividend_yield_pct,
        pays_dividend=extra.pays_dividend,
        earnings_positive_recent=extra.earnings_positive_recent,
        momentum_6m_pct=_momentum_from_base(base),
    )


def _momentum_from_base(f: StockFundamentals) -> float | None:
    if f.last_close is None or f.high_52w is None or f.high_52w == 0:
        return None
    # Approximation: distance below 52w high inverted as a momentum proxy.
    return round((f.last_close / f.high_52w - 1.0) * 100.0, 2)


def _summarise(
    samples: list[BacktestSample],
    buckets: dict[str, list[BacktestSample]],
) -> BacktestSummary:
    if not samples:
        return BacktestSummary(
            n_picks=0,
            hit_rate_pct=0.0,
            avg_forward_return_pct=0.0,
            median_forward_return_pct=0.0,
        )
    forward_returns = [s.forward_return_pct for s in samples if s.forward_return_pct is not None]
    hits = [s for s in samples if s.hit]
    avg_fwd = float(np.mean(forward_returns)) if forward_returns else 0.0
    median_fwd = statistics.median(forward_returns) if forward_returns else 0.0
    by_strategy = {
        code: BacktestStrategySummary(
            code=code,
            n_picks=len(items),
            n_hits=sum(1 for s in items if s.hit),
            avg_forward_return_pct=round(
                float(
                    np.mean(
                        [s.forward_return_pct for s in items if s.forward_return_pct is not None]
                    )
                )
                if items
                else 0.0,
                2,
            ),
        )
        for code, items in buckets.items()
    }
    return BacktestSummary(
        n_picks=len(samples),
        hit_rate_pct=round(len(hits) / len(samples) * 100.0, 1),
        avg_forward_return_pct=round(avg_fwd, 2),
        median_forward_return_pct=round(median_fwd, 2),
        by_strategy=by_strategy,
        samples=samples,
    )
