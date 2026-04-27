"""Section formatters for cron digests — picks block + sell-rule block.

Both sections are designed to send as **separate Telegram messages**
appended after the existing morning pulse / evening recap. That keeps
the original digest unchanged (small blast radius) and lets us A/B
the new sections without touching `core/digest_builder`.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from core.daily_picks import Pick
from core.portfolio import Holding
from core.sell_signals import SellSeverity, SellSignal

DEFAULT_TOP_N = 5

SellEvaluator = Callable[[Holding], SellSignal | None]


def format_picks_section(
    picks: list[Pick],
    *,
    top_n: int = DEFAULT_TOP_N,
    computed_at: datetime | None = None,
) -> str:
    """Render today's picks as a Telegram-ready block."""
    when = (computed_at or datetime.now(tz=UTC)).strftime("%Y-%m-%d")
    if not picks:
        return (
            f"📈 Top picks ({when})\n"
            "No candidates pass the threshold today. Stay defensive — "
            "wait for the next confirmed uptrend signal."
        )
    lines = [f"📈 Top picks ({when}):"]
    for p in picks[:top_n]:
        endorsers = ", ".join(p.endorsing_codes) if p.endorsing_codes else "—"
        s = "s" if p.endorsement_count != 1 else ""
        lines.append(
            f"• {p.symbol} — composite {p.composite_rating:.0f}/99 · "
            f"{p.endorsement_count} guru{s} ({endorsers})"
        )
        summary = _fundamentals_summary(p.fundamentals)
        if summary:
            lines.append(f"  {summary}")
    lines.append("")
    lines.append("Sized by O'Neil's risk rules: 7% stop, 6–8 positions max.")
    return "\n".join(lines)


def format_sells_section(
    *,
    holdings: list[Holding],
    evaluator: SellEvaluator,
    as_of: datetime | None = None,
) -> str:
    """Run the sell-rule engine on every holding and render the result."""
    when = (as_of or datetime.now(tz=UTC)).strftime("%Y-%m-%d")
    if not holdings:
        return (
            f"🧾 Sell-rule check ({when})\n"
            "No holdings to evaluate. Add one with `/add SYMBOL QTY PRICE`."
        )

    lines = [f"🧾 Sell-rule check ({when}):"]
    actionable = 0
    for h in holdings:
        signal = evaluator(h)
        if signal is None:
            lines.append(f"• {h.symbol} — data unavailable")
            continue
        if signal.severity != SellSeverity.HOLD:
            actionable += 1
        badge = _severity_badge(signal.severity)
        lines.append(
            f"{badge} {h.symbol} — {signal.severity.value.upper()} ({signal.rule}): {signal.reason}"
        )
    if actionable == 0:
        lines.append("")
        lines.append("All clear — no action required tonight.")
    return "\n".join(lines)


# -----------------------------------------------------------------------------
# helpers
# -----------------------------------------------------------------------------


def _severity_badge(severity: SellSeverity) -> str:
    return {
        SellSeverity.SELL: "🚨",
        SellSeverity.TRIM: "⚠️",
        SellSeverity.WATCH: "👁",
        SellSeverity.HOLD: "✅",
    }.get(severity, "•")


def _fundamentals_summary(f) -> str:
    parts: list[str] = []
    if f.last_close is not None:
        parts.append(f"px=₹{f.last_close:.2f}")
    if f.rs_rating is not None:
        parts.append(f"RS={f.rs_rating:.0f}")
    if f.pe_ratio is not None:
        parts.append(f"P/E={f.pe_ratio:.1f}")
    if f.pb_ratio is not None:
        parts.append(f"P/B={f.pb_ratio:.2f}")
    return " · ".join(parts)
