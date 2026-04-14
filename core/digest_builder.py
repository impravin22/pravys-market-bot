"""Compose morning pulse, evening recap, weekly top-3, and @mention messages.

All output is HTML-escape-safe for Telegram parse_mode=HTML.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from core.canslim import CanslimScore, MarketRegime, phase_label
from core.nse_data import Quote
from core.telegram_client import escape_html

DISCLAIMER = "<i>Educational signals, not investment advice. Do your own research.</i>"

RISK_RULES_FOOTER = (
    "<b>📏 CAN SLIM risk rules</b>\n"
    "  • Cut losses at <b>7–8%</b> below entry — no exceptions\n"
    "  • Take profits around <b>20–25%</b> above entry (the 25/8 plan)\n"
    "  • If a pick rises <b>20%+ in 3 weeks</b>, hold it for at least <b>8 weeks</b>\n"
    "  • Average <b>up</b>, never <b>down</b>\n"
    "  • Keep the portfolio to <b>6–8 positions</b>, not 30+"
)


@dataclass(frozen=True)
class IndexSnapshot:
    label: str
    last: float
    change_pct: float


@dataclass(frozen=True)
class DailyMover:
    symbol: str
    change_pct: float
    volume_multiple: float
    note: str = ""


def _fmt_time(now: datetime, tz_name: str) -> str:
    tz = ZoneInfo(tz_name)
    return now.astimezone(tz).strftime("%d %b %Y, %H:%M")


def _fmt_indices(indices: list[IndexSnapshot]) -> list[str]:
    if not indices:
        return ["  • (index snapshot unavailable)"]
    return [
        f"  • {escape_html(ix.label)}: {ix.last:,.2f} ({ix.change_pct:+.2f}%)" for ix in indices
    ]


def _fmt_commodities(quotes: list[Quote]) -> list[str]:
    if not quotes:
        return ["  • (commodity quotes unavailable)"]
    return [f"  • {escape_html(q.label)}: {q.last:,.2f} ({q.change_pct:+.2f}%)" for q in quotes]


def _fmt_score_line(rank: int, s: CanslimScore) -> str:
    letters = "".join(c if c in s.passed_codes else "·" for c in "CANSLIM")
    rs = s.letters["L"].note.replace("RS ", "")
    eps = s.letters["C"].note
    return (
        f"  {rank}. {escape_html(s.symbol)}  "
        f"[{letters}] {s.binary_score}/7  "
        f"RS {escape_html(rs)}  {escape_html(eps)}"
    )


def build_morning_pulse(
    *,
    now: datetime,
    market_tz: str,
    regime: MarketRegime,
    indices: list[IndexSnapshot],
    commodities: list[Quote],
    top_scores: list[CanslimScore],
    global_cues_commentary: str = "",
) -> str:
    lines: list[str] = []
    lines.append("☀️ <b>Pravy's Market — Morning Pulse</b>")
    lines.append(f"{escape_html(_fmt_time(now, market_tz))} IST · market opens soon")
    lines.append("")

    lines.append('🌡 <b>Market Direction (CAN SLIM "M")</b>')
    phase_icon = {
        "confirmed-uptrend": "✅",
        "uptrend-under-pressure": "⚠",
        "rally-attempt": "🧪",
        "downtrend": "🛑",
    }.get(regime.phase, "•")
    lines.append(f"  {phase_icon} {escape_html(phase_label(regime.phase))}")
    dma_parts = []
    dma_parts.append("above 50-DMA" if regime.nifty_above_50dma else "below 50-DMA")
    dma_parts.append("above 200-DMA" if regime.nifty_above_200dma else "below 200-DMA")
    dma_parts.append("5d trend up" if regime.nifty_5d_trend_up else "5d flat/down")
    lines.append(f"  {escape_html(', '.join(dma_parts))}")
    lines.append("")

    lines.append("📊 <b>Index Snapshot</b>")
    lines.extend(_fmt_indices(indices))
    lines.append("")

    lines.append("🥇 <b>Commodities &amp; FX</b>")
    lines.extend(_fmt_commodities(commodities))
    lines.append("")

    lines.append(f"🔝 <b>Top CAN SLIM Scorers (top {len(top_scores)})</b>")
    if top_scores:
        for i, s in enumerate(top_scores, start=1):
            lines.append(_fmt_score_line(i, s))
    else:
        lines.append("  • No stocks met the CAN SLIM bar this morning")
    lines.append("")

    if global_cues_commentary:
        lines.append("🌍 <b>Global cues</b>")
        lines.append(f"  {escape_html(global_cues_commentary)}")
        lines.append("")

    lines.append(DISCLAIMER)
    return "\n".join(lines)


def build_evening_recap(
    *,
    now: datetime,
    market_tz: str,
    indices: list[IndexSnapshot],
    commodities: list[Quote],
    top_gainers: list[DailyMover],
    top_losers: list[DailyMover],
    watchlist_actions: list[str],
    narrative: str = "",
) -> str:
    lines: list[str] = []
    lines.append("🌙 <b>Pravy's Market — Evening Recap</b>")
    lines.append(f"{escape_html(_fmt_time(now, market_tz))} IST · 15 min after close")
    lines.append("")

    lines.append("📈 <b>How the market did today</b>")
    lines.extend(_fmt_indices(indices))
    lines.append("")

    lines.append("🥇 <b>Commodities &amp; FX</b>")
    lines.extend(_fmt_commodities(commodities))
    lines.append("")

    lines.append("⭐ <b>Top gainers</b>")
    if top_gainers:
        for m in top_gainers:
            note = f" — {escape_html(m.note)}" if m.note else ""
            lines.append(
                f"  • {escape_html(m.symbol)}  {m.change_pct:+.2f}% on {m.volume_multiple:.1f}× vol{note}"
            )
    else:
        lines.append("  • (data unavailable)")
    lines.append("")

    lines.append("🔻 <b>Top losers</b>")
    if top_losers:
        for m in top_losers:
            lines.append(
                f"  • {escape_html(m.symbol)}  {m.change_pct:+.2f}% on {m.volume_multiple:.1f}× vol"
            )
    else:
        lines.append("  • (data unavailable)")
    lines.append("")

    if watchlist_actions:
        lines.append("📋 <b>Watchlist action</b>")
        for a in watchlist_actions:
            lines.append(f"  • {escape_html(a)}")
        lines.append("")

    if narrative:
        lines.append("🧠 <b>Gemini's take</b>")
        lines.append(f"  {escape_html(narrative)}")
        lines.append("")

    lines.append(DISCLAIMER)
    return "\n".join(lines)


def build_weekly_top3(
    *,
    now: datetime,
    market_tz: str,
    picks: list[tuple[CanslimScore, str]],  # (score, rationale)
) -> str:
    lines: list[str] = []
    lines.append("📅 <b>Pravy's Weekly Top 3</b>")
    lines.append(f"Week ending {escape_html(_fmt_time(now, market_tz))}")
    lines.append("")

    if not picks:
        lines.append("  • No stocks met the CAN SLIM bar this week.")
    else:
        for i, (s, rationale) in enumerate(picks[:3], start=1):
            lines.append(f"<b>{i}. {escape_html(s.symbol)} — CAN SLIM {s.binary_score}/7</b>")
            for code in "CANSLIM":
                r = s.letters[code]
                status = "✅" if r.passes else ("❔" if r.passes is None else "❌")
                lines.append(f"   {status} {code}: {escape_html(r.note)}")
            if rationale:
                lines.append(f"   📖 {escape_html(rationale)}")
            lines.append("")

    lines.append(RISK_RULES_FOOTER)
    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def build_on_demand_top5(
    *,
    now: datetime,
    market_tz: str,
    top_scores: list[CanslimScore],
    commentary: str = "",
) -> str:
    lines: list[str] = []
    lines.append("🎯 <b>Top 5 CAN SLIM picks right now</b>")
    lines.append(f"Pulled at {escape_html(_fmt_time(now, market_tz))} IST")
    lines.append("")

    if not top_scores:
        lines.append("  • No stocks currently meet the CAN SLIM bar.")
    else:
        for i, s in enumerate(top_scores[:5], start=1):
            lines.append(_fmt_score_line(i, s))

    if commentary:
        lines.append("")
        lines.append(f"🧠 {escape_html(commentary)}")

    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)
