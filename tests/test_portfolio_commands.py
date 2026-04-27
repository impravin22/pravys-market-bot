"""Telegram portfolio-command parser + dispatcher tests."""

from __future__ import annotations

from datetime import date

from bot.handlers.portfolio_commands import (
    CommandResult,
    PortfolioCommands,
    parse_command,
)
from core.portfolio import Holding, Portfolio

# -----------------------------------------------------------------------------
# parse_command
# -----------------------------------------------------------------------------


def test_parse_command_returns_none_for_non_command():
    assert parse_command("hello mate") is None


def test_parse_command_returns_none_for_empty():
    assert parse_command("") is None


def test_parse_command_extracts_command_and_args():
    parsed = parse_command("/add RELIANCE 50 2400 2026-04-21")
    assert parsed == ("add", ["RELIANCE", "50", "2400", "2026-04-21"])


def test_parse_command_lowercases_command_name():
    parsed = parse_command("/PORTFOLIO")
    assert parsed == ("portfolio", [])


def test_parse_command_handles_extra_whitespace():
    parsed = parse_command("  /add   X   10   100  ")
    assert parsed == ("add", ["X", "10", "100"])


# -----------------------------------------------------------------------------
# PortfolioCommands — fakes
# -----------------------------------------------------------------------------


class _FakeStore:
    """Minimal in-memory PortfolioStore stand-in."""

    def __init__(self) -> None:
        self.portfolios: dict[int, Portfolio] = {}

    def get(self, *, chat_id: int) -> Portfolio:
        return self.portfolios.get(chat_id, Portfolio(chat_id=chat_id))

    def add(self, *, chat_id: int, holding: Holding) -> Portfolio:
        current = self.get(chat_id=chat_id)
        new = Portfolio(
            chat_id=chat_id,
            holdings=[*current.holdings, holding],
        )
        self.portfolios[chat_id] = new
        return new

    def remove(self, *, chat_id: int, symbol: str) -> Holding | None:
        current = self.get(chat_id=chat_id)
        target = next((h for h in current.holdings if h.symbol == symbol), None)
        if target is None:
            return None
        kept = [h for h in current.holdings if h.symbol != symbol]
        self.portfolios[chat_id] = Portfolio(chat_id=chat_id, holdings=kept)
        return target


# -----------------------------------------------------------------------------
# /help
# -----------------------------------------------------------------------------


def test_help_lists_supported_commands():
    cmds = PortfolioCommands(store=_FakeStore())
    out = cmds.handle(chat_id=1, command="help", args=[])
    assert isinstance(out, CommandResult)
    assert out.should_skip_agent is True
    assert "/portfolio" in out.reply_text
    assert "/add" in out.reply_text
    assert "/remove" in out.reply_text


def test_unknown_command_returns_skip_false_to_fall_back_to_agent():
    cmds = PortfolioCommands(store=_FakeStore())
    out = cmds.handle(chat_id=1, command="ticker", args=["RELIANCE"])
    assert out.should_skip_agent is False  # let Gemini handle it


# -----------------------------------------------------------------------------
# /add
# -----------------------------------------------------------------------------


def test_add_with_three_args_uses_today_as_buy_date():
    store = _FakeStore()
    cmds = PortfolioCommands(store=store, today=date(2026, 4, 27))
    out = cmds.handle(chat_id=42, command="add", args=["RELIANCE", "50", "2400"])
    assert out.should_skip_agent is True
    assert "Added" in out.reply_text
    holdings = store.get(chat_id=42).holdings
    assert len(holdings) == 1
    h = holdings[0]
    assert h.symbol == "RELIANCE.NS"  # auto-suffix
    assert h.qty == 50
    assert h.buy_price == 2400.0
    assert h.buy_date == date(2026, 4, 27)


def test_add_with_explicit_date():
    store = _FakeStore()
    cmds = PortfolioCommands(store=store)
    cmds.handle(chat_id=1, command="add", args=["TCS", "10", "3500", "2026-04-21"])
    h = store.get(chat_id=1).holdings[0]
    assert h.buy_date == date(2026, 4, 21)


def test_add_validates_arg_count():
    cmds = PortfolioCommands(store=_FakeStore())
    out = cmds.handle(chat_id=1, command="add", args=["JUST_ONE"])
    assert out.should_skip_agent is True
    assert "Usage" in out.reply_text or "usage" in out.reply_text.lower()


def test_add_validates_numeric_qty_and_price():
    cmds = PortfolioCommands(store=_FakeStore())
    out = cmds.handle(chat_id=1, command="add", args=["X", "abc", "100"])
    assert (
        "qty must be a whole number" in out.reply_text.lower()
        or "invalid" in out.reply_text.lower()
    )


def test_add_rejects_negative_qty():
    cmds = PortfolioCommands(store=_FakeStore())
    out = cmds.handle(chat_id=1, command="add", args=["X", "-5", "100"])
    assert "must be" in out.reply_text.lower()


def test_add_already_uppercase_symbol_with_suffix_unchanged():
    store = _FakeStore()
    cmds = PortfolioCommands(store=store)
    cmds.handle(chat_id=1, command="add", args=["TCS.BO", "10", "3500"])
    h = store.get(chat_id=1).holdings[0]
    assert h.symbol == "TCS.BO"  # don't double-suffix


# -----------------------------------------------------------------------------
# /remove
# -----------------------------------------------------------------------------


def test_remove_existing_holding_returns_pnl_summary():
    store = _FakeStore()
    cmds = PortfolioCommands(store=store)
    cmds.handle(chat_id=1, command="add", args=["X", "10", "100"])
    out = cmds.handle(chat_id=1, command="remove", args=["X"])
    assert out.should_skip_agent is True
    assert "Removed" in out.reply_text
    assert store.get(chat_id=1).holdings == []


def test_remove_unknown_holding_returns_friendly_message():
    cmds = PortfolioCommands(store=_FakeStore())
    out = cmds.handle(chat_id=1, command="remove", args=["GHOST"])
    assert "not in your portfolio" in out.reply_text.lower()


def test_remove_validates_arg_count():
    cmds = PortfolioCommands(store=_FakeStore())
    out = cmds.handle(chat_id=1, command="remove", args=[])
    assert "usage" in out.reply_text.lower()


# -----------------------------------------------------------------------------
# /portfolio
# -----------------------------------------------------------------------------


def test_portfolio_empty_returns_friendly_empty_state():
    cmds = PortfolioCommands(store=_FakeStore())
    out = cmds.handle(chat_id=1, command="portfolio", args=[])
    assert out.should_skip_agent is True
    assert "no holdings" in out.reply_text.lower() or "empty" in out.reply_text.lower()


def test_portfolio_lists_holdings_with_buy_price_and_stop():
    store = _FakeStore()
    cmds = PortfolioCommands(store=store)
    cmds.handle(chat_id=1, command="add", args=["RELIANCE", "50", "2400"])
    cmds.handle(chat_id=1, command="add", args=["TCS", "10", "3500"])
    out = cmds.handle(chat_id=1, command="portfolio", args=[])
    assert "RELIANCE" in out.reply_text
    assert "TCS" in out.reply_text
    assert "2400" in out.reply_text
    assert "3500" in out.reply_text
    assert "Stop" in out.reply_text or "stop" in out.reply_text


# -----------------------------------------------------------------------------
# /clear
# -----------------------------------------------------------------------------


def test_clear_requires_confirm_flag():
    """Destructive — must be /clear CONFIRM."""
    store = _FakeStore()
    cmds = PortfolioCommands(store=store)
    cmds.handle(chat_id=1, command="add", args=["X", "10", "100"])
    out = cmds.handle(chat_id=1, command="clear", args=[])
    assert "confirm" in out.reply_text.lower()
    # Not actually cleared yet.
    assert len(store.get(chat_id=1).holdings) == 1


def test_clear_with_confirm_flag_wipes_portfolio():
    store = _FakeStore()
    cmds = PortfolioCommands(store=store)
    cmds.handle(chat_id=1, command="add", args=["X", "10", "100"])
    cmds.handle(chat_id=1, command="add", args=["Y", "5", "200"])
    out = cmds.handle(chat_id=1, command="clear", args=["CONFIRM"])
    assert "cleared" in out.reply_text.lower()
    assert store.get(chat_id=1).holdings == []
