"""DSPy-powered narrative layer for picks and sell signals.

LLMs are NOT a source of truth for numbers — every figure that appears in
the final Telegram digest is derived from the deterministic scoring layer.
DSPy here is purely a structured prompting wrapper that turns
(deterministic facts) → (short prose blurb + risk flag).

Tests inject fake predictors so no live LLM call is ever made by the suite.
At runtime the caller wires `dspy.LM(...)` once at start-up and passes the
default predictors built lazily here.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

try:
    import dspy  # type: ignore
except ImportError:  # pragma: no cover — DSPy is a hard dep, this is defensive
    dspy = None  # type: ignore

from core.canslim import StockFundamentals
from core.daily_picks import Pick
from core.portfolio import Holding
from core.sell_signals import SellSeverity, SellSignal

# -----------------------------------------------------------------------------
# Fundamentals → short summary string (deterministic, no LLM)
# -----------------------------------------------------------------------------


def summarise_fundamentals(f: StockFundamentals) -> str:
    """Compact one-line summary of the populated fields. Skips Nones."""
    parts: list[str] = []
    if f.last_close is not None:
        parts.append(f"px=₹{f.last_close:.2f}")
    if f.rs_rating is not None:
        parts.append(f"RS={f.rs_rating:.0f}")
    if f.quarterly_eps_yoy_pct is not None:
        parts.append(f"Q-EPS={f.quarterly_eps_yoy_pct:+.1f}%")
    if f.annual_eps_3y_cagr_pct is not None:
        parts.append(f"3yEPS={f.annual_eps_3y_cagr_pct:+.1f}%")
    if f.pe_ratio is not None:
        parts.append(f"P/E={f.pe_ratio:.1f}")
    if f.pb_ratio is not None:
        parts.append(f"P/B={f.pb_ratio:.2f}")
    if f.debt_to_equity is not None:
        parts.append(f"D/E={f.debt_to_equity:.2f}")
    if f.roe_5y_avg_pct is not None:
        parts.append(f"ROE5y={f.roe_5y_avg_pct:.1f}%")
    if f.dividend_yield_pct is not None:
        parts.append(f"DY={f.dividend_yield_pct:.2f}%")
    if not parts:
        return "no fundamentals available"
    return " · ".join(parts)


# -----------------------------------------------------------------------------
# DSPy signatures (built lazily so the import works even if DSPy missing)
# -----------------------------------------------------------------------------


def _build_pick_signature():
    if dspy is None:
        raise RuntimeError("dspy not installed — install dspy-ai")

    class PickReasoning(dspy.Signature):  # type: ignore[misc]
        """Explain why a stock is a strong pick *today* given guru endorsements + recent news.

        Numbers in your output must come from the fundamentals_summary and
        composite_rating inputs — do NOT invent figures or quote prices not
        present in the input."""

        symbol: str = dspy.InputField()
        composite_rating: float = dspy.InputField(desc="0–100 weighted blend across gurus")
        endorsing_gurus: str = dspy.InputField(
            desc="comma-separated guru codes endorsing this stock"
        )
        fundamentals_summary: str = dspy.InputField()
        recent_news: str = dspy.InputField(desc="bulletised 7-day news; may be empty")

        rationale: str = dspy.OutputField(
            desc="2 sentences, max 50 words, why-now narrative grounded in inputs"
        )
        top_3_reasons: str = dspy.OutputField(
            desc="three short bullet lines, one per line, no leading bullet character"
        )
        risk_flag: str = dspy.OutputField(desc="one sentence on the biggest risk")

    return PickReasoning


def _build_sell_signature():
    if dspy is None:
        raise RuntimeError("dspy not installed — install dspy-ai")

    class SellExplanation(dspy.Signature):  # type: ignore[misc]
        """Translate a deterministic sell rule into one user-readable explanation."""

        symbol: str = dspy.InputField()
        severity: str = dspy.InputField(desc="sell | trim | watch")
        rule: str = dspy.InputField(desc="rule code like stop_loss_7pct")
        reason: str = dspy.InputField(desc="numeric reason from the rule engine")
        holding_summary: str = dspy.InputField(desc="symbol qty bought_at on date")

        plain_english: str = dspy.OutputField(
            desc="2 short sentences explaining what happened in plain language"
        )
        next_action: str = dspy.OutputField(
            desc="one imperative line telling the user what to do next"
        )

    return SellExplanation


# -----------------------------------------------------------------------------
# Result types
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class PickReasoningResult:
    symbol: str
    rationale: str
    top_3_reasons: list[str]
    risk_flag: str


@dataclass(frozen=True)
class SellReasoningResult:
    symbol: str
    plain_english: str
    next_action: str


# -----------------------------------------------------------------------------
# Engine
# -----------------------------------------------------------------------------


PickPredictor = Callable[..., Any]
SellPredictor = Callable[..., Any]


class ReasoningEngine:
    """Wraps DSPy predictors. Inject fakes in tests; pass `lm=` at runtime."""

    def __init__(
        self,
        *,
        pick_predictor: PickPredictor | None = None,
        sell_predictor: SellPredictor | None = None,
        lm: Any | None = None,
    ) -> None:
        self._pick_predictor = pick_predictor
        self._sell_predictor = sell_predictor
        self._lm = lm

    def _ensure_pick(self) -> PickPredictor:
        if self._pick_predictor is None:
            if dspy is None:
                raise RuntimeError(
                    "dspy not installed — install dspy-ai or inject pick_predictor=..."
                )
            self._pick_predictor = dspy.ChainOfThought(_build_pick_signature())
        return self._pick_predictor

    def _ensure_sell(self) -> SellPredictor:
        if self._sell_predictor is None:
            if dspy is None:
                raise RuntimeError(
                    "dspy not installed — install dspy-ai or inject sell_predictor=..."
                )
            self._sell_predictor = dspy.ChainOfThought(_build_sell_signature())
        return self._sell_predictor

    def _ctx(self):
        if self._lm is not None and dspy is not None:
            return dspy.context(lm=self._lm)
        return contextlib.nullcontext()

    def explain_pick(self, pick: Pick, *, news_text: str = "") -> PickReasoningResult:
        predictor = self._ensure_pick()
        with self._ctx():
            result = predictor(
                symbol=pick.symbol,
                composite_rating=pick.composite_rating,
                endorsing_gurus=", ".join(pick.endorsing_codes) or "none",
                fundamentals_summary=summarise_fundamentals(pick.fundamentals),
                recent_news=news_text or "no recent news provided",
            )
        return PickReasoningResult(
            symbol=pick.symbol,
            rationale=str(result.rationale).strip(),
            top_3_reasons=_clean_bullets(str(result.top_3_reasons)),
            risk_flag=str(result.risk_flag).strip(),
        )

    def explain_sell(
        self,
        *,
        holding: Holding,
        signal: SellSignal,
        current_close: float,
    ) -> SellReasoningResult:
        # HOLD does not need an LLM — emit a deterministic message.
        if signal.severity == SellSeverity.HOLD:
            return SellReasoningResult(
                symbol=holding.symbol,
                plain_english=(
                    f"Hold {holding.symbol}. No sell rule triggered. "
                    f"Current ₹{current_close:.2f} vs buy ₹{holding.buy_price:.2f} "
                    f"({holding.pnl_pct(current_close):+.1f}%)."
                ),
                next_action="No action — review again tomorrow after close.",
            )

        predictor = self._ensure_sell()
        holding_summary = (
            f"{holding.symbol} qty {holding.qty} bought ₹{holding.buy_price:.2f} "
            f"on {holding.buy_date.isoformat()}"
        )
        with self._ctx():
            result = predictor(
                symbol=holding.symbol,
                severity=signal.severity.value,
                rule=signal.rule,
                reason=signal.reason,
                holding_summary=holding_summary,
            )
        return SellReasoningResult(
            symbol=holding.symbol,
            plain_english=str(result.plain_english).strip(),
            next_action=str(result.next_action).strip(),
        )


def _clean_bullets(raw: str) -> list[str]:
    """Strip leading bullets/whitespace and skip blank lines."""
    return [line.strip().lstrip("-•*· ").strip() for line in raw.splitlines() if line.strip()]
