"""Persistent state for the GitHub Actions chatbot cron.

The cron job runs every 5 min on ephemeral runners, so any state that needs
to survive between runs is written to a JSON file on a dedicated git branch
(``chatbot-state``). The workflow commits the file back to that branch; it
is never merged into ``main``.

Persisted fields (committed to the public repo — must stay PII-free):

- ``telegram_offset`` — the next ``update_id`` to fetch from ``getUpdates``.
- ``last_run_at`` — ISO timestamp of the most recent successful poll.

Deliberately **not** persisted:

- Rate-limit map keyed by Telegram user_id. Keeping it in-process only is
  acceptable because the cron interval (5 min) already exceeds the 30 s
  rate-limit window — the next run starts a clean map. The cost is that two
  requests from the same user inside one run (realistically only possible
  if Telegram had queued them) will both get through, which is fine for a
  market bot. A corrupt file path could otherwise leak user_ids into
  permanent public git history.

If ``load_state`` encounters a corrupt file it logs at ERROR level and
renames the file aside for forensics rather than silently resetting the
offset — that would trigger a mass-replay of every update Telegram has
retained (up to ~24 h).
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

DEFAULT_STATE_PATH = Path("state/chatbot_state.json")
RATE_LIMIT_SECONDS = 30

logger = logging.getLogger(__name__)


def _empty() -> dict[str, Any]:
    # Fields persisted to the public `chatbot-state` branch.
    # Keep this PII-free.
    return {
        "telegram_offset": 0,
        "last_run_at": None,
    }


def load_state(path: Path = DEFAULT_STATE_PATH) -> dict[str, Any]:
    """Load persisted bot state from disk.

    Returns an empty state when the file does not exist yet. On corruption,
    logs at ERROR, renames the file aside with a timestamped suffix, and
    then returns empty — the operator can see exactly which run wiped the
    offset.
    """
    if not path.exists():
        return _empty()
    try:
        raw = path.read_text("utf8")
    except OSError as exc:
        logger.error("cannot read chatbot state at %s: %s", path, exc)
        return _empty()
    if not raw.strip():
        logger.warning("chatbot state file is empty; starting fresh")
        return _empty()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        backup = path.with_name(f"{path.stem}.corrupt-{int(time.time())}.json")
        try:
            path.rename(backup)
        except OSError:
            logger.exception("failed to preserve corrupt state backup")
        logger.error(
            "chatbot state JSON corrupt at %s (backup=%s): %s — resetting offset may replay updates",
            path,
            backup,
            exc,
        )
        return _empty()
    base = _empty()
    if isinstance(parsed, dict):
        for k in base:
            if k in parsed:
                base[k] = parsed[k]
    return base


def save_state(state: dict[str, Any], path: Path = DEFAULT_STATE_PATH) -> None:
    """Persist state atomically via temp-file + os.replace.

    Only the allowlisted fields in ``_empty()`` are written — anything else
    is dropped on the floor, including an accidentally in-memory
    ``rate_limit`` map, so we cannot regress into committing PII.
    """
    base = _empty()
    for k in base:
        if k in state:
            base[k] = state[k]
    base["last_run_at"] = datetime.now(tz=UTC).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(base, indent=2, sort_keys=True), "utf8")
    tmp.replace(path)


class RateLimiter:
    """In-process-only rate limiter. Does not persist across cron runs.

    A 30-second per-user budget is short enough that a single cron tick is
    the only meaningful enforcement window. Across-run bursts are rare and
    already implicitly bounded by the 5-minute cron interval.
    """

    def __init__(self, seconds: int = RATE_LIMIT_SECONDS):
        self._seconds = seconds
        self._last: dict[str, datetime] = {}

    def is_limited(self, user_id: int | str) -> bool:
        last = self._last.get(str(user_id))
        if last is None:
            return False
        return datetime.now(tz=UTC) - last < timedelta(seconds=self._seconds)

    def mark(self, user_id: int | str) -> None:
        self._last[str(user_id)] = datetime.now(tz=UTC)

    def unmark(self, user_id: int | str) -> None:
        """Used when a reply send fails — user hasn't actually been served."""
        self._last.pop(str(user_id), None)
