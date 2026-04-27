"""CAN SLIM scoring engine (William O'Neil methodology).

Each of the seven letters returns a boolean "passes" plus a continuous magnitude
that feeds into a tiebreak score. The binary score (0–7) is the primary rank;
stocks tied at the same binary count are ordered by the continuous component.

Data contract: callers supply a `StockFundamentals` with the fields we need.
Missing data degrades gracefully — a letter can't be evaluated becomes `None`
(neither pass nor fail) and contributes zero to the tiebreak.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StockFundamentals:
    symbol: str
    # Price / volume features
    last_close: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None
    avg_vol_50d: float | None = None
    last_volume: float | None = None
    # Earnings
    quarterly_eps_yoy_pct: float | None = None  # e.g. 34.0 for +34%
    annual_eps_3y_cagr_pct: float | None = None
    # Market leadership + institutional
    rs_rating: float | None = None  # 0–100 percentile
    fii_dii_5d_net_positive: bool | None = None  # None if FII/DII data unavailable
    # Valuation / quality ratios — optional, populated by data adapters where
    # available (screener.in scrape, yfinance Ticker.info). All default None
    # so existing CAN SLIM scoring is unaffected; non-CAN-SLIM strategies
    # consume these.
    pe_ratio: float | None = None
    pb_ratio: float | None = None
    ps_ratio: float | None = None
    pcf_ratio: float | None = None
    ev_ebitda: float | None = None
    debt_to_equity: float | None = None
    current_ratio: float | None = None
    roe_5y_avg_pct: float | None = None
    roce_pct: float | None = None
    dividend_yield_pct: float | None = None
    pays_dividend: bool | None = None
    earnings_positive_recent: bool | None = None
    momentum_6m_pct: float | None = None  # 6-month price return, used by Trending Value


@dataclass(frozen=True)
class MarketRegime:
    """Shared across every stock in one scoring run (CAN SLIM 'M').

    ``phase`` follows the MarketSmith India 4-phase classification:

    - ``"confirmed-uptrend"`` — above 50/200 DMAs, 5-day trend up, no recent
      heavy distribution. **Buy aggressively.**
    - ``"uptrend-under-pressure"`` — above 200-DMA but showing distribution or
      a weak 5-day trend. **Stay cautious, manage risk.**
    - ``"rally-attempt"`` — index trying to turn up after a downtrend
      (below 50-DMA but 5-day trend up). **Small positions, verify.**
    - ``"downtrend"`` — below 50 and 200 DMAs. **Reduce exposure.**
    """

    nifty_above_50dma: bool
    nifty_above_200dma: bool
    nifty_5d_trend_up: bool
    phase: str = "confirmed-uptrend"

    @property
    def is_uptrend(self) -> bool:
        return self.phase == "confirmed-uptrend"


def classify_phase(
    *,
    above_50dma: bool,
    above_200dma: bool,
    five_day_up: bool,
) -> str:
    """Map the three signals to one of the four named phases."""
    if above_50dma and above_200dma and five_day_up:
        return "confirmed-uptrend"
    if above_200dma and (not above_50dma or not five_day_up):
        return "uptrend-under-pressure"
    if not above_50dma and not above_200dma and five_day_up:
        return "rally-attempt"
    return "downtrend"


def phase_label(phase: str) -> str:
    return {
        "confirmed-uptrend": "Confirmed Uptrend — buy aggressively",
        "uptrend-under-pressure": "Uptrend Under Pressure — stay cautious",
        "rally-attempt": "Rally Attempt — start small",
        "downtrend": "Downtrend — reduce exposure",
    }.get(phase, phase)


@dataclass(frozen=True)
class LetterResult:
    code: str  # one of C A N S L I M
    passes: bool | None
    magnitude: float  # normalised contribution to the tiebreak
    note: str  # one-line human-readable description


@dataclass(frozen=True)
class CanslimScore:
    symbol: str
    letters: dict[str, LetterResult]
    binary_score: int  # 0..7 count of letters passing
    continuous_score: float  # sum of magnitudes across all letters

    @property
    def passed_codes(self) -> list[str]:
        return [code for code, r in self.letters.items() if r.passes]

    @property
    def failed_codes(self) -> list[str]:
        return [code for code, r in self.letters.items() if r.passes is False]


# Thresholds — sourced from the MarketSmith India CAN SLIM Playbook.
EPS_QUARTERLY_THRESHOLD_PCT = 25.0
EPS_ANNUAL_CAGR_THRESHOLD_PCT = 20.0
PRICE_NEAR_HIGH_MAX_PCT = 15.0  # "limited overhead supply (within ~15% of 52-week highs)"
VOLUME_SURGE_MULTIPLIER = 1.4  # "volume at least 40% higher than 50-day average"
RS_LEADER_THRESHOLD = 80.0


def _letter_c(f: StockFundamentals) -> LetterResult:
    v = f.quarterly_eps_yoy_pct
    if v is None:
        return LetterResult("C", None, 0.0, "quarterly EPS unavailable")
    passes = v >= EPS_QUARTERLY_THRESHOLD_PCT
    mag = max(0.0, v / 100.0) if passes else 0.0
    return LetterResult("C", passes, mag, f"Q/Q EPS {v:+.1f}%")


def _letter_a(f: StockFundamentals) -> LetterResult:
    v = f.annual_eps_3y_cagr_pct
    if v is None:
        return LetterResult("A", None, 0.0, "annual EPS CAGR unavailable")
    passes = v >= EPS_ANNUAL_CAGR_THRESHOLD_PCT
    mag = max(0.0, v / 100.0) if passes else 0.0
    return LetterResult("A", passes, mag, f"3Y EPS CAGR {v:+.1f}%")


def _letter_n(f: StockFundamentals) -> LetterResult:
    if f.last_close is None or f.high_52w is None or f.high_52w == 0:
        return LetterResult("N", None, 0.0, "price vs 52w high unavailable")
    distance_pct = (1.0 - f.last_close / f.high_52w) * 100.0
    passes = distance_pct <= PRICE_NEAR_HIGH_MAX_PCT
    mag = max(0.0, 1.0 - distance_pct / 100.0) if passes else 0.0
    return LetterResult("N", passes, mag, f"{distance_pct:.1f}% below 52w high")


def _letter_s(f: StockFundamentals) -> LetterResult:
    if not f.avg_vol_50d or not f.last_volume:
        return LetterResult("S", None, 0.0, "volume data unavailable")
    ratio = f.last_volume / f.avg_vol_50d
    passes = ratio >= VOLUME_SURGE_MULTIPLIER
    mag = max(0.0, min(ratio / 3.0, 1.0)) if passes else 0.0
    return LetterResult("S", passes, mag, f"vol {ratio:.1f}x avg")


def _letter_l(f: StockFundamentals) -> LetterResult:
    if f.rs_rating is None:
        return LetterResult("L", None, 0.0, "RS rating unavailable")
    passes = f.rs_rating >= RS_LEADER_THRESHOLD
    mag = f.rs_rating / 100.0 if passes else 0.0
    return LetterResult("L", passes, mag, f"RS {f.rs_rating:.0f}")


def _letter_i(f: StockFundamentals) -> LetterResult:
    if f.fii_dii_5d_net_positive is None:
        return LetterResult("I", None, 0.0, "FII/DII data unavailable")
    passes = bool(f.fii_dii_5d_net_positive)
    mag = 0.5 if passes else 0.0
    return LetterResult(
        "I", passes, mag, "FII+DII 5d net positive" if passes else "FII+DII 5d net negative"
    )


def _letter_m(regime: MarketRegime) -> LetterResult:
    passes = regime.is_uptrend
    mag = 0.5 if passes else 0.0
    note_bits = [
        "above 50-DMA" if regime.nifty_above_50dma else "below 50-DMA",
        "above 200-DMA" if regime.nifty_above_200dma else "below 200-DMA",
        "5d up" if regime.nifty_5d_trend_up else "5d flat/down",
    ]
    return LetterResult("M", passes, mag, ", ".join(note_bits))


def score(f: StockFundamentals, regime: MarketRegime) -> CanslimScore:
    """Compute the full CAN SLIM score for one stock under the current market regime."""
    letters: dict[str, LetterResult] = {}
    letters["C"] = _letter_c(f)
    letters["A"] = _letter_a(f)
    letters["N"] = _letter_n(f)
    letters["S"] = _letter_s(f)
    letters["L"] = _letter_l(f)
    letters["I"] = _letter_i(f)
    letters["M"] = _letter_m(regime)

    binary = sum(1 for r in letters.values() if r.passes)
    continuous = sum(r.magnitude for r in letters.values())
    return CanslimScore(
        symbol=f.symbol,
        letters=letters,
        binary_score=binary,
        continuous_score=round(continuous, 4),
    )


def rank_universe(
    fundamentals: list[StockFundamentals],
    regime: MarketRegime,
    *,
    min_binary: int = 5,
) -> list[CanslimScore]:
    """Score and sort a list of stocks. Returns scores with binary >= min_binary.

    Sorted by (binary DESC, continuous DESC).
    """
    scored = [score(f, regime) for f in fundamentals]
    filtered = [s for s in scored if s.binary_score >= min_binary]
    return sorted(filtered, key=lambda s: (s.binary_score, s.continuous_score), reverse=True)
