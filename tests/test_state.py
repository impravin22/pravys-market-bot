import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bot.state import (
    RATE_LIMIT_SECONDS,
    is_rate_limited,
    load_state,
    mark_user,
    save_state,
)


def test_load_state_returns_empty_when_missing(tmp_path: Path):
    path = tmp_path / "missing.json"
    state = load_state(path)
    assert state == {"telegram_offset": 0, "rate_limit": {}, "last_run_at": None}


def test_load_state_returns_empty_on_corrupt(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text("{not valid json", "utf8")
    state = load_state(path)
    assert state["telegram_offset"] == 0
    assert state["rate_limit"] == {}


def test_round_trip(tmp_path: Path):
    path = tmp_path / "state.json"
    state = load_state(path)
    state["telegram_offset"] = 42
    mark_user(state, 123)
    save_state(state, path)

    reloaded = load_state(path)
    assert reloaded["telegram_offset"] == 42
    assert "123" in reloaded["rate_limit"]
    assert reloaded["last_run_at"] is not None


def test_is_rate_limited_false_for_unseen_user():
    assert is_rate_limited({"rate_limit": {}}, 42) is False


def test_is_rate_limited_true_within_window():
    just_now = datetime.now(tz=UTC).isoformat()
    state = {"rate_limit": {"42": just_now}}
    assert is_rate_limited(state, 42) is True


def test_is_rate_limited_false_after_window():
    long_ago = (datetime.now(tz=UTC) - timedelta(seconds=RATE_LIMIT_SECONDS * 2)).isoformat()
    state = {"rate_limit": {"42": long_ago}}
    assert is_rate_limited(state, 42) is False


def test_save_state_caps_rate_limit_dict(tmp_path: Path):
    path = tmp_path / "state.json"
    state = {"telegram_offset": 0, "rate_limit": {}, "last_run_at": None}
    base = datetime.now(tz=UTC)
    for i in range(300):
        state["rate_limit"][str(i)] = (base - timedelta(seconds=i)).isoformat()
    save_state(state, path)
    reloaded = json.loads(path.read_text("utf8"))
    assert len(reloaded["rate_limit"]) <= 200


def test_save_state_stamps_last_run_at(tmp_path: Path):
    path = tmp_path / "state.json"
    save_state({"telegram_offset": 1, "rate_limit": {}, "last_run_at": None}, path)
    saved = json.loads(path.read_text("utf8"))
    # Parseable ISO timestamp within a couple of seconds of "now".
    stamped = datetime.fromisoformat(saved["last_run_at"])
    assert abs((datetime.now(tz=UTC) - stamped).total_seconds()) < 5


def test_mark_user_uses_iso_string():
    state = {"rate_limit": {}}
    mark_user(state, "abc")
    assert "abc" in state["rate_limit"]
    # Parseable
    datetime.fromisoformat(state["rate_limit"]["abc"])


def test_malformed_timestamp_treated_as_not_limited():
    """Corruption mustn't lock the bot out forever — treat bad data as 'not recent'."""
    state = {"rate_limit": {"42": "not-a-timestamp"}}
    assert is_rate_limited(state, 42) is False


def test_save_state_is_deterministic(tmp_path: Path):
    """Keys sorted so git diffs stay clean on the state branch."""
    path = tmp_path / "state.json"
    state = {
        "rate_limit": {"z": "t", "a": "t"},
        "telegram_offset": 10,
        "last_run_at": None,
    }
    save_state(state, path)
    content = path.read_text("utf8")
    assert content.index('"last_run_at"') < content.index('"rate_limit"')
    assert content.index('"rate_limit"') < content.index('"telegram_offset"')
    # Using `with pytest.raises` is unnecessary here; this is a sanity assert.
    assert '"a":' in content
