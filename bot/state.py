"""Persistent state for the GitHub Actions chatbot cron.

The cron job runs every 5 min on ephemeral runners, so any state that needs
to survive between runs is written to a JSON file on a dedicated git branch
(``chatbot-state``). The workflow commits the file back to that branch; it
is never merged into ``main``.

Stored fields:

- ``telegram_offset`` — the next ``update_id`` to fetch from ``getUpdates``
  (Telegram purges updates once acknowledged via the offset).
- ``rate_limit`` — ``{user_id: iso_timestamp}`` of the last message a user
  sent, so we can enforce 1-per-30-second throttling.
- ``last_run_at`` — ISO timestamp of the most recent successful poll.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

DEFAULT_STATE_PATH = Path("state/chatbot_state.json")
RATE_LIMIT_SECONDS = 30
MAX_RATE_LIMIT_ENTRIES = 200


def _empty() -> dict[str, Any]:
    return {
        "telegram_offset": 0,
        "rate_limit": {},
        "last_run_at": None,
    }


def load_state(path: Path = DEFAULT_STATE_PATH) -> dict[str, Any]:
    if not path.exists():
        return _empty()
    try:
        raw = path.read_text("utf8")
        parsed = json.loads(raw) if raw.strip() else {}
    except (OSError, json.JSONDecodeError):
        return _empty()
    base = _empty()
    base.update({k: v for k, v in parsed.items() if k in base})
    if not isinstance(base["rate_limit"], dict):
        base["rate_limit"] = {}
    return base


def save_state(state: dict[str, Any], path: Path = DEFAULT_STATE_PATH) -> None:
    state = dict(state)
    state["last_run_at"] = datetime.now(tz=UTC).isoformat()
    # Cap the rate_limit dict so it doesn't grow unbounded.
    rl = state.get("rate_limit") or {}
    if isinstance(rl, dict) and len(rl) > MAX_RATE_LIMIT_ENTRIES:
        # Keep the most recent N entries by timestamp.
        ordered = sorted(rl.items(), key=lambda kv: str(kv[1]), reverse=True)
        state["rate_limit"] = dict(ordered[:MAX_RATE_LIMIT_ENTRIES])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), "utf8")


def is_rate_limited(
    state: dict[str, Any],
    user_id: int | str,
    *,
    seconds: int = RATE_LIMIT_SECONDS,
) -> bool:
    rl = state.get("rate_limit") or {}
    last = rl.get(str(user_id))
    if not last:
        return False
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return False
    return datetime.now(tz=UTC) - last_dt < timedelta(seconds=seconds)


def mark_user(state: dict[str, Any], user_id: int | str) -> None:
    rl = state.setdefault("rate_limit", {})
    rl[str(user_id)] = datetime.now(tz=UTC).isoformat()
