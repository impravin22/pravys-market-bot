"""Reasoning engine tests — DSPy LM mocked end-to-end, no live calls."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from core.canslim import StockFundamentals
from core.daily_picks import Pick
from core.portfolio import Holding
from core.reasoning import ReasoningEngine, summarise_fundamentals
from core.sell_signals import SellSeverity, SellSignal
from core.strategies.base import FilterCheck, StrategyVerdict

# -----------------------------------------------------------------------------
# Fakes
# -----------------------------------------------------------------------------


class _FakePickPredictor:
    """Mimics a dspy.Predict / ChainOfThought callable returning a Prediction."""

    def __init__(self, **outputs: str) -> None:
        self._outputs = outputs
        self.calls: list[dict[str, str]] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(**self._outputs)


# -----------------------------------------------------------------------------
# summarise_fundamentals
# -----------------------------------------------------------------------------


def test_summarise_fundamentals_includes_present_fields_only():
    f = StockFundamentals(
        symbol="X.NS",
        last_close=100.0,
        rs_rating=85.0,
        quarterly_eps_yoy_pct=30.0,
    )
    summary = summarise_fundamentals(f)
    assert "RS=85" in summary
    assert "Q-EPS=+30.0%" in summary
    assert "P/E" not in summary  # absent field skipped


def test_summarise_fundamentals_handles_all_none():
    summary = summarise_fundamentals(StockFundamentals(symbol="X.NS"))
    assert "no fundamentals" in summary.lower()


# -----------------------------------------------------------------------------
# explain_pick
# -----------------------------------------------------------------------------


def _pick() -> Pick:
    fund = StockFundamentals(
        symbol="RELIANCE.NS",
        last_close=2520.0,
        rs_rating=84.0,
        quarterly_eps_yoy_pct=34.0,
    )
    verdict = StrategyVerdict(
        code="canslim",
        name="O'Neil",
        school="growth",
        passes=True,
        rating_0_100=88.0,
        checks=[FilterCheck(name="C", passes=True, note="+34%")],
        notes={},
    )
    return Pick(
        symbol="RELIANCE.NS",
        composite_rating=88.0,
        endorsement_count=1,
        endorsing_codes=["canslim"],
        verdicts=[verdict],
        fundamentals=fund,
    )


def test_explain_pick_returns_structured_reasoning():
    fake = _FakePickPredictor(
        rationale="Quarterly EPS up 34% with leader RS.",
        top_3_reasons="C-letter accelerating\nRS at 84\nVolume confirmation",
        risk_flag="Telecom regulation overhang.",
    )
    engine = ReasoningEngine(pick_predictor=fake)
    out = engine.explain_pick(_pick(), news_text="Refining margins expanding.")
    assert out.symbol == "RELIANCE.NS"
    assert out.rationale.startswith("Quarterly EPS")
    assert len(out.top_3_reasons) == 3
    assert out.top_3_reasons[1] == "RS at 84"
    assert "regulation" in out.risk_flag


def test_explain_pick_passes_correct_inputs_to_predictor():
    fake = _FakePickPredictor(rationale="r", top_3_reasons="a\nb\nc", risk_flag="f")
    engine = ReasoningEngine(pick_predictor=fake)
    engine.explain_pick(_pick(), news_text="news here")
    args = fake.calls[0]
    assert args["symbol"] == "RELIANCE.NS"
    assert args["composite_rating"] == 88.0
    assert "canslim" in args["endorsing_gurus"]
    assert "RS=84" in args["fundamentals_summary"]
    assert args["recent_news"] == "news here"


def test_explain_pick_handles_no_news_gracefully():
    fake = _FakePickPredictor(rationale="r", top_3_reasons="a\nb\nc", risk_flag="f")
    engine = ReasoningEngine(pick_predictor=fake)
    engine.explain_pick(_pick())
    args = fake.calls[0]
    assert args["recent_news"]  # non-empty placeholder


def test_explain_pick_top_3_reasons_strips_bullets_and_blank_lines():
    fake = _FakePickPredictor(
        rationale="r",
        top_3_reasons="- first reason\n* second reason\n\n• third reason\n",
        risk_flag="f",
    )
    engine = ReasoningEngine(pick_predictor=fake)
    out = engine.explain_pick(_pick())
    assert out.top_3_reasons == ["first reason", "second reason", "third reason"]


# -----------------------------------------------------------------------------
# explain_sell
# -----------------------------------------------------------------------------


class _FakeSellPredictor:
    def __init__(self, **outputs: str) -> None:
        self._outputs = outputs
        self.calls: list[dict[str, str]] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(**self._outputs)


def test_explain_sell_wraps_signal_in_plain_english():
    holding = Holding(symbol="X.NS", qty=10, buy_price=100.0, buy_date=date(2026, 4, 1))
    signal = SellSignal(
        severity=SellSeverity.SELL,
        rule="stop_loss_7pct",
        reason="closed ₹92.5 ≤ ₹93.0 (7% stop)",
    )
    fake = _FakeSellPredictor(
        plain_english="Stop hit. Cut the loss.",
        next_action="Sell at open tomorrow.",
    )
    engine = ReasoningEngine(sell_predictor=fake)
    out = engine.explain_sell(holding=holding, signal=signal, current_close=92.5)
    assert "Cut the loss" in out.plain_english
    assert out.next_action.startswith("Sell")
    assert fake.calls[0]["severity"] == "sell"
    assert fake.calls[0]["rule"] == "stop_loss_7pct"


def test_explain_sell_holds_passthrough_does_not_invoke_llm():
    """No need to ask the LLM to explain HOLD — emit a deterministic line."""
    holding = Holding(symbol="X.NS", qty=10, buy_price=100.0, buy_date=date(2026, 4, 1))
    hold_signal = SellSignal(
        severity=SellSeverity.HOLD, rule="hold", reason="no sell rule triggered"
    )
    fake = _FakeSellPredictor(plain_english="x", next_action="x")
    engine = ReasoningEngine(sell_predictor=fake)
    out = engine.explain_sell(holding=holding, signal=hold_signal, current_close=101.0)
    assert fake.calls == []  # never invoked
    assert out.plain_english.lower().startswith("hold")
