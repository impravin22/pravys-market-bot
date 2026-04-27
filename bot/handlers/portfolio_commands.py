"""Portfolio slash-command handlers for the Telegram bot.

The chatbot loop should consult `parse_command` first; if it returns a
recognised command, dispatch to `PortfolioCommands.handle` and send the
result back to the user instead of streaming a Gemini reply. Unknown
commands fall through (`should_skip_agent=False`) so the agent can
still answer free-form questions starting with ``/``.

Supported:

| Command | Args | Effect |
|---------|------|--------|
| `/portfolio` | — | list holdings + P&L summary |
| `/add SYMBOL QTY PRICE [YYYY-MM-DD]` | 3 or 4 | append a holding |
| `/remove SYMBOL` | 1 | drop a holding |
| `/clear CONFIRM` | 1 | wipe portfolio (requires literal ``CONFIRM``) |
| `/help` | — | show this list |
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from core.portfolio import Holding, Portfolio
from core.sell_signals import SellSeverity, SellSignal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommandResult:
    """Outcome of dispatching a slash-command."""

    reply_text: str
    should_skip_agent: bool  # True ⇒ command handled, don't fall through to Gemini


class _StoreLike(Protocol):
    """Subset of `PortfolioStore` we depend on. Lets tests inject a fake."""

    def get(self, *, chat_id: int) -> Portfolio: ...
    def add(self, *, chat_id: int, holding: Holding) -> Portfolio: ...
    def remove(self, *, chat_id: int, symbol: str) -> Holding | None: ...


# -----------------------------------------------------------------------------
# Parser
# -----------------------------------------------------------------------------


def parse_command(text: str) -> tuple[str, list[str]] | None:
    """Return ``(command, args)`` if the input is a slash-command, else None.

    Command name is lower-cased; args preserve their case (symbols stay
    upper-case). Empty/whitespace-only inputs return ``None``.
    """
    stripped = text.strip()
    if not stripped or not stripped.startswith("/"):
        return None
    parts = stripped[1:].split()
    if not parts:
        return None
    command = parts[0].lower()
    args = parts[1:]
    return command, args


# -----------------------------------------------------------------------------
# Dispatcher
# -----------------------------------------------------------------------------


HELP_TEXT = (
    "Portfolio commands:\n"
    "  /portfolio — list your holdings\n"
    "  /add SYMBOL QTY PRICE [YYYY-MM-DD] — add a holding\n"
    "  /remove SYMBOL — remove a holding\n"
    "  /sells — check sell rules on every holding\n"
    "  /picks — show today's top buy candidates (cached)\n"
    "  /why SYMBOL — guru breakdown for one ticker\n"
    "  /clear CONFIRM — wipe portfolio (requires the word CONFIRM)\n"
    "  /help — this message"
)


# Type aliases for the injected callables — keeps the constructor signature
# self-documenting and lets tests pass plain functions.
PicksCacheReader = Callable[[], Any | None]
"""Returns the latest `CachedPicks` (from core.picks_cache) or None."""

WhyEvaluator = Callable[[str], dict[str, Any] | None]
"""Given a normalised symbol, return a dict with keys
``symbol``, ``composite_rating``, ``fundamentals_summary``, ``verdicts``."""

SellsEvaluator = Callable[[Holding], SellSignal | None]
"""Given a holding, return its sell-rule outcome (or None when not evaluable)."""


class PortfolioCommands:
    """Routes parsed commands to portfolio actions and formats replies.

    Heavy dependencies (picks cache reader, single-stock evaluator,
    sell-rule evaluator) are injected so tests can stub them and the
    runtime wiring stays in one place (``jobs/chatbot_poll.py``).
    """

    def __init__(
        self,
        *,
        store: _StoreLike,
        today: date | None = None,
        picks_cache_reader: PicksCacheReader | None = None,
        why_evaluator: WhyEvaluator | None = None,
        sells_evaluator: SellsEvaluator | None = None,
    ) -> None:
        self._store = store
        self._today_factory = (lambda: today) if today is not None else date.today
        self._picks_reader = picks_cache_reader
        self._why_evaluator = why_evaluator
        self._sells_evaluator = sells_evaluator

    def handle(self, *, chat_id: int, command: str, args: list[str]) -> CommandResult:
        if command == "help":
            return CommandResult(HELP_TEXT, should_skip_agent=True)
        if command == "portfolio":
            return self._cmd_portfolio(chat_id)
        if command == "add":
            return self._cmd_add(chat_id, args)
        if command == "remove":
            return self._cmd_remove(chat_id, args)
        if command == "clear":
            return self._cmd_clear(chat_id, args)
        if command == "picks":
            return self._cmd_picks()
        if command == "why":
            return self._cmd_why(args)
        if command == "sells":
            return self._cmd_sells(chat_id)
        return CommandResult("", should_skip_agent=False)

    # -------------------- /portfolio --------------------

    def _cmd_portfolio(self, chat_id: int) -> CommandResult:
        portfolio = self._store.get(chat_id=chat_id)
        if not portfolio.holdings:
            return CommandResult(
                "No holdings yet. Add one with `/add SYMBOL QTY PRICE`.",
                should_skip_agent=True,
            )
        lines = [f"Your portfolio ({len(portfolio.holdings)} positions):"]
        for h in portfolio.holdings:
            lines.append(
                f"• {h.symbol} — qty {h.qty} @ ₹{h.buy_price:.2f} "
                f"(bought {h.buy_date.isoformat()}) · Stop ₹{h.stop_loss:.2f}"
            )
        lines.append(f"\nInvested capital: ₹{portfolio.invested_capital:.2f}")
        return CommandResult("\n".join(lines), should_skip_agent=True)

    # -------------------- /add --------------------

    def _cmd_add(self, chat_id: int, args: list[str]) -> CommandResult:
        if len(args) not in (3, 4):
            return CommandResult(
                "Usage: /add SYMBOL QTY PRICE [YYYY-MM-DD]\n"
                "Example: /add RELIANCE 50 2400 2026-04-21",
                should_skip_agent=True,
            )
        symbol_raw, qty_raw, price_raw = args[0], args[1], args[2]
        date_raw = args[3] if len(args) == 4 else None

        try:
            qty = int(qty_raw)
        except ValueError:
            return CommandResult(
                f"qty must be a whole number, got '{qty_raw}'.", should_skip_agent=True
            )
        if qty <= 0:
            return CommandResult("qty must be positive.", should_skip_agent=True)

        try:
            price = float(price_raw)
        except ValueError:
            return CommandResult(
                f"price must be a number, got '{price_raw}'.", should_skip_agent=True
            )
        if price <= 0:
            return CommandResult("price must be positive.", should_skip_agent=True)

        if date_raw is None:
            buy_date = self._today_factory()
        else:
            try:
                buy_date = date.fromisoformat(date_raw)
            except ValueError:
                return CommandResult(
                    f"date must be YYYY-MM-DD, got '{date_raw}'.", should_skip_agent=True
                )

        symbol = _normalise_symbol(symbol_raw)
        holding = Holding(symbol=symbol, qty=qty, buy_price=price, buy_date=buy_date)
        self._store.add(chat_id=chat_id, holding=holding)
        return CommandResult(
            f"Added {symbol}: qty {qty} @ ₹{price:.2f} on {buy_date.isoformat()}.\n"
            f"Default stop-loss ₹{holding.stop_loss:.2f} (7% below buy).",
            should_skip_agent=True,
        )

    # -------------------- /remove --------------------

    def _cmd_remove(self, chat_id: int, args: list[str]) -> CommandResult:
        if len(args) != 1:
            return CommandResult(
                "Usage: /remove SYMBOL\nExample: /remove RELIANCE",
                should_skip_agent=True,
            )
        symbol = _normalise_symbol(args[0])
        removed = self._store.remove(chat_id=chat_id, symbol=symbol)
        if removed is None:
            return CommandResult(
                f"{symbol} is not in your portfolio. Use /portfolio to list current holdings.",
                should_skip_agent=True,
            )
        return CommandResult(
            f"Removed {removed.symbol} (qty {removed.qty} @ ₹{removed.buy_price:.2f}).",
            should_skip_agent=True,
        )

    # -------------------- /clear --------------------

    # -------------------- /picks --------------------

    def _cmd_picks(self) -> CommandResult:
        if self._picks_reader is None:
            return CommandResult(
                "Picks aren't wired in this build. Run /help.",
                should_skip_agent=True,
            )
        cached = self._picks_reader()
        if cached is None or not getattr(cached, "picks", []):
            return CommandResult(
                "No picks computed yet. The morning cron writes them daily — "
                "or run `uv run python -m jobs.daily_picks_job` once to seed.",
                should_skip_agent=True,
            )
        lines = [f"Top picks (computed {cached.computed_at.strftime('%Y-%m-%d %H:%M UTC')}):"]
        for p in cached.picks[:5]:
            sym = p.get("symbol", "?")
            comp = p.get("composite_rating", 0.0)
            count = p.get("endorsement_count", 0)
            endorsers = ", ".join(p.get("endorsing_codes", []) or ["—"])
            summary = p.get("fundamentals_summary", "")
            lines.append(
                f"• {sym} — composite {comp:.0f}/99 · "
                f"{count} guru{'s' if count != 1 else ''} ({endorsers})"
            )
            if summary:
                lines.append(f"  {summary}")
        return CommandResult("\n".join(lines), should_skip_agent=True)

    # -------------------- /why --------------------

    def _cmd_why(self, args: list[str]) -> CommandResult:
        if len(args) != 1:
            return CommandResult(
                "Usage: /why SYMBOL\nExample: /why RELIANCE",
                should_skip_agent=True,
            )
        if self._why_evaluator is None:
            return CommandResult(
                "Live single-stock evaluation isn't wired in this build.",
                should_skip_agent=True,
            )
        symbol = _normalise_symbol(args[0])
        result = self._why_evaluator(symbol)
        if result is None:
            return CommandResult(
                f"Couldn't fetch fundamentals for {symbol} right now. Try again later.",
                should_skip_agent=True,
            )
        verdicts = result.get("verdicts") or []
        composite = result.get("composite_rating", 0.0)
        summary = result.get("fundamentals_summary", "")
        lines = [
            f"{symbol} — composite {composite:.0f}/99",
        ]
        if summary:
            lines.append(summary)
        lines.append("")
        for v in verdicts:
            mark = "✅" if v.passes else "❌"
            lines.append(f"{mark} {v.name} ({v.code}) — {v.rating_0_100:.0f}/100")
            for c in v.checks:
                tick = "•" if c.passes else "·"
                lines.append(f"   {tick} {c.name}: {c.note}")
        return CommandResult("\n".join(lines), should_skip_agent=True)

    # -------------------- /sells --------------------

    def _cmd_sells(self, chat_id: int) -> CommandResult:
        portfolio = self._store.get(chat_id=chat_id)
        if not portfolio.holdings:
            return CommandResult(
                "No holdings to evaluate. Add one with `/add SYMBOL QTY PRICE`.",
                should_skip_agent=True,
            )
        if self._sells_evaluator is None:
            return CommandResult(
                "Live sell-rule evaluation isn't wired in this build.",
                should_skip_agent=True,
            )
        lines = [f"Sell-rule check on {len(portfolio.holdings)} holdings:"]
        for h in portfolio.holdings:
            signal = self._sells_evaluator(h)
            if signal is None:
                lines.append(f"• {h.symbol}: data unavailable")
                continue
            badge = _severity_badge(signal.severity)
            lines.append(
                f"{badge} {h.symbol} — {signal.severity.value.upper()} "
                f"({signal.rule}): {signal.reason}"
            )
        return CommandResult("\n".join(lines), should_skip_agent=True)

    def _cmd_clear(self, chat_id: int, args: list[str]) -> CommandResult:
        if len(args) != 1 or args[0] != "CONFIRM":
            return CommandResult(
                "Destructive. Send `/clear CONFIRM` to wipe your portfolio.",
                should_skip_agent=True,
            )
        portfolio = self._store.get(chat_id=chat_id)
        for h in list(portfolio.holdings):
            self._store.remove(chat_id=chat_id, symbol=h.symbol)
        return CommandResult("Portfolio cleared.", should_skip_agent=True)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _normalise_symbol(raw: str) -> str:
    """Upper-case and ensure an NSE/BSE suffix.

    Default to ``.NS`` if the user typed a bare ticker like ``RELIANCE``.
    Preserve any explicit ``.BO`` (BSE) suffix.
    """
    upper = raw.strip().upper()
    if upper.endswith(".NS") or upper.endswith(".BO"):
        return upper
    return f"{upper}.NS"


def _severity_badge(severity: SellSeverity) -> str:
    """Map a sell severity to a small visual marker for Telegram."""
    return {
        SellSeverity.SELL: "🚨",
        SellSeverity.TRIM: "⚠️",
        SellSeverity.WATCH: "👁",
        SellSeverity.HOLD: "✅",
    }.get(severity, "•")
