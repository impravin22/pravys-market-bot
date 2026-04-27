"""Weekly portfolio prompt tests — message builder + run guard."""

from __future__ import annotations

from datetime import date

from jobs.weekly_portfolio_prompt import (
    build_prompt_message,
    should_run_today,
)

# -----------------------------------------------------------------------------
# build_prompt_message
# -----------------------------------------------------------------------------


def test_message_includes_add_instruction():
    text = build_prompt_message(today=date(2026, 4, 26))
    assert "/add" in text
    assert "SYMBOL" in text
    assert "QTY" in text
    assert "PRICE" in text


def test_message_includes_review_commands():
    text = build_prompt_message(today=date(2026, 4, 26))
    assert "/portfolio" in text
    assert "/sells" in text or "/picks" in text


def test_message_calls_out_the_week_just_finished():
    text = build_prompt_message(today=date(2026, 4, 26))
    # Says weekly, references the week, dated.
    assert "weekly" in text.lower() or "week" in text.lower()
    assert "2026-04-26" in text or "26 Apr" in text


# -----------------------------------------------------------------------------
# should_run_today
# -----------------------------------------------------------------------------


def test_should_run_today_true_on_sunday():
    sunday = date(2026, 4, 26)  # 2026-04-26 is a Sunday
    assert sunday.weekday() == 6
    assert should_run_today(sunday) is True


def test_should_run_today_false_on_other_days():
    monday = date(2026, 4, 27)
    assert monday.weekday() == 0
    assert should_run_today(monday) is False
    friday = date(2026, 4, 24)
    assert should_run_today(friday) is False


def test_force_run_overrides_weekday_check():
    monday = date(2026, 4, 27)
    assert should_run_today(monday, force=True) is True
