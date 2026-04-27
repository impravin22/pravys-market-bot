"""Picks-section + sells-section formatter tests."""

from __future__ import annotations

from datetime import UTC, date, datetime

from core.canslim import StockFundamentals
from core.daily_picks import Pick
from core.digest_extras import (
    format_picks_section,
    format_sells_section,
)
from core.portfolio import Holding
from core.sell_signals import SellSeverity, SellSignal
from core.strategies.base import FilterCheck, StrategyVerdict


def _pick(symbol: str, *, composite: float = 88.0, count: int = 3) -> Pick:
    return Pick(
        symbol=symbol,
        composite_rating=composite,
        endorsement_count=count,
        endorsing_codes=["canslim", "buffett", "graham"][:count],
        verdicts=[
            StrategyVerdict(
                code="canslim",
                name="O'Neil",
                school="growth",
                passes=True,
                rating_0_100=composite,
                checks=[FilterCheck("C", True, "+34%")],
                notes={},
            )
        ],
        fundamentals=StockFundamentals(symbol=symbol, last_close=100.0, rs_rating=84.0),
    )


# -----------------------------------------------------------------------------
# format_picks_section
# -----------------------------------------------------------------------------


def test_picks_section_header_includes_date():
    text = format_picks_section(
        [_pick("RELIANCE.NS")], computed_at=datetime(2026, 4, 27, tzinfo=UTC)
    )
    assert "2026-04-27" in text
    assert "Top picks" in text or "top picks" in text.lower()


def test_picks_section_lists_each_symbol():
    text = format_picks_section(
        [_pick("RELIANCE.NS", composite=91.0, count=4), _pick("TCS.NS", composite=78.0, count=2)],
    )
    assert "RELIANCE" in text
    assert "TCS" in text
    assert "91" in text
    assert "78" in text


def test_picks_section_empty_returns_friendly_message():
    text = format_picks_section([])
    assert "no picks" in text.lower() or "no candidates" in text.lower()


def test_picks_section_caps_at_default_top_n():
    picks = [_pick(f"S{i}.NS") for i in range(10)]
    text = format_picks_section(picks)
    # Default cap is 5.
    assert text.count("S0.NS") + text.count("S1.NS") <= 2
    assert "S5.NS" not in text


# -----------------------------------------------------------------------------
# format_sells_section
# -----------------------------------------------------------------------------


def _holding(symbol: str = "RELIANCE.NS", buy_price: float = 2400.0) -> Holding:
    return Holding(
        symbol=symbol,
        qty=50,
        buy_price=buy_price,
        buy_date=date(2026, 4, 21),
    )


def test_sells_section_empty_holdings_returns_friendly_no_holdings():
    text = format_sells_section(holdings=[], evaluator=lambda _h: None)
    assert "no holdings" in text.lower() or "empty" in text.lower()


def test_sells_section_lists_each_holding_with_severity():
    holdings = [_holding("RELIANCE.NS"), _holding("TATAMOTORS.NS")]

    def evaluator(h):
        if h.symbol == "TATAMOTORS.NS":
            return SellSignal(SellSeverity.SELL, "stop_loss_7pct", "−7.6% breach")
        return SellSignal(SellSeverity.HOLD, "hold", "no rule fired")

    text = format_sells_section(holdings=holdings, evaluator=evaluator)
    assert "RELIANCE" in text
    assert "TATAMOTORS" in text
    assert "SELL" in text or "stop_loss_7pct" in text
    assert "HOLD" in text or "hold" in text


def test_sells_section_no_actionables_says_all_hold():
    """When every signal is HOLD the section should still render but say nothing actionable."""
    holdings = [_holding("X.NS")]
    text = format_sells_section(
        holdings=holdings,
        evaluator=lambda _h: SellSignal(SellSeverity.HOLD, "hold", "no rule fired"),
    )
    assert "no action" in text.lower() or "all clear" in text.lower() or "HOLD" in text


def test_sells_section_evaluator_returning_none_marks_data_unavailable():
    holdings = [_holding("X.NS")]
    text = format_sells_section(holdings=holdings, evaluator=lambda _h: None)
    assert "data unavailable" in text.lower()


def test_sells_section_includes_date_header():
    holdings = [_holding("X.NS")]
    text = format_sells_section(
        holdings=holdings,
        evaluator=lambda _h: SellSignal(SellSeverity.HOLD, "hold", ""),
        as_of=datetime(2026, 4, 27, tzinfo=UTC),
    )
    assert "2026-04-27" in text
