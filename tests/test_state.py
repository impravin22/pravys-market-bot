import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bot.state import RATE_LIMIT_SECONDS, RateLimiter, load_state, save_state


def test_load_state_returns_empty_when_missing(tmp_path: Path):
    path = tmp_path / "missing.json"
    state = load_state(path)
    assert state == {"telegram_offset": 0, "last_run_at": None}


def test_load_state_returns_empty_and_logs_on_corrupt(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """Corrupt JSON must log at ERROR — silent reset would replay every queued update."""
    path = tmp_path / "bad.json"
    path.write_text("{not valid json", "utf8")
    with caplog.at_level(logging.ERROR, logger="bot.state"):
        state = load_state(path)
    assert state == {"telegram_offset": 0, "last_run_at": None}
    assert any("corrupt" in rec.message.lower() for rec in caplog.records)


def test_load_state_preserves_corrupt_file_as_backup(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text("garbage", "utf8")
    load_state(path)
    # Original path gone, a timestamped backup remains for forensics.
    assert not path.exists()
    backups = list(tmp_path.glob("bad.corrupt-*.json"))
    assert backups, "expected a .corrupt-<ts>.json backup file"


def test_load_state_empty_file_returns_empty(tmp_path: Path):
    path = tmp_path / "empty.json"
    path.write_text("", "utf8")
    state = load_state(path)
    assert state["telegram_offset"] == 0


def test_load_state_drops_unknown_fields(tmp_path: Path):
    """Forwards-compat: unknown fields in the file are ignored, not kept.

    Also closes the PII sink — a state file that somehow gained a
    `rate_limit` key never re-enters memory.
    """
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"telegram_offset": 7, "last_run_at": None, "rate_limit": {"42": "x"}}),
        "utf8",
    )
    reloaded = load_state(path)
    assert reloaded == {"telegram_offset": 7, "last_run_at": None}
    assert "rate_limit" not in reloaded


def test_save_state_round_trip(tmp_path: Path):
    path = tmp_path / "state.json"
    save_state({"telegram_offset": 42}, path)
    reloaded = load_state(path)
    assert reloaded["telegram_offset"] == 42
    # last_run_at was stamped.
    assert datetime.fromisoformat(reloaded["last_run_at"])


def test_save_state_is_atomic_via_temp_rename(tmp_path: Path, monkeypatch):
    """If write fails mid-way, the original file must not be truncated."""
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"telegram_offset": 99, "last_run_at": None}), "utf8")

    def boom(self, target):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError):
        save_state({"telegram_offset": 100}, path)
    # Original file intact.
    assert json.loads(path.read_text("utf8"))["telegram_offset"] == 99


def test_save_state_refuses_to_persist_rate_limit_keys(tmp_path: Path):
    """PII guard: even if caller passes a rate_limit dict, it is never written."""
    path = tmp_path / "state.json"
    save_state({"telegram_offset": 1, "rate_limit": {"1234": "ts"}}, path)
    raw = path.read_text("utf8")
    assert "rate_limit" not in raw
    assert "1234" not in raw


def test_rate_limiter_not_limited_for_unseen_user():
    rl = RateLimiter()
    assert rl.is_limited(42) is False


def test_rate_limiter_limited_after_mark():
    rl = RateLimiter()
    rl.mark(42)
    assert rl.is_limited(42) is True


def test_rate_limiter_expires_after_window():
    rl = RateLimiter(seconds=1)
    rl._last["42"] = datetime.now(tz=UTC) - timedelta(seconds=5)  # noqa: SLF001
    assert rl.is_limited(42) is False


def test_rate_limiter_unmark_releases_slot():
    rl = RateLimiter()
    rl.mark(42)
    rl.unmark(42)
    assert rl.is_limited(42) is False


def test_rate_limiter_stringifies_user_ids():
    """Treat 42 (int) and '42' (str) as the same user."""
    rl = RateLimiter()
    rl.mark(42)
    assert rl.is_limited("42")


def test_rate_limiter_uses_default_window_constant():
    """Ensure the default window matches the exported RATE_LIMIT_SECONDS."""
    rl = RateLimiter()
    rl.mark(42)
    assert rl.is_limited(42)
    # Force expiry by back-dating the mark exactly RATE_LIMIT_SECONDS+1 seconds.
    rl._last["42"] = datetime.now(tz=UTC) - timedelta(  # noqa: SLF001
        seconds=RATE_LIMIT_SECONDS + 1
    )
    assert rl.is_limited(42) is False
