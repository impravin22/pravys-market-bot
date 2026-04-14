"""Per-user watchlist stored as JSON on a mounted volume.

Used by the Fly.io bot service (Phase 2). Included in core so the shared
``/today TICKER`` command has a consistent home when we move it to the bot.

Format::

    {
        "<telegram_user_id>": {
            "symbols": ["RELIANCE.NS", "INFY.NS"],
            "updated_at": "2026-04-14T10:05:00Z"
        }
    }

Concurrency: a per-path asyncio.Lock serialises writes. Reads are cheap.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

VALID_SYMBOL_RE = r"^[A-Z0-9._&-]{1,20}$"

_WRITE_LOCKS: dict[str, asyncio.Lock] = {}


def _lock_for(path: Path) -> asyncio.Lock:
    key = str(path.resolve())
    if key not in _WRITE_LOCKS:
        _WRITE_LOCKS[key] = asyncio.Lock()
    return _WRITE_LOCKS[key]


def _to_yahoo(symbol: str) -> str:
    """Normalise user input 'RELIANCE' or 'RELIANCE.NS' → 'RELIANCE.NS'."""
    s = symbol.strip().upper()
    return s if "." in s else f"{s}.NS"


async def load(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    raw = await asyncio.to_thread(path.read_text, "utf8")
    parsed = json.loads(raw) if raw else {}
    clean: dict[str, dict] = {}
    for k, v in parsed.items():
        if k in ("__proto__", "prototype", "constructor"):
            continue
        clean[k] = v
    return clean


async def save(path: Path, data: dict[str, dict]) -> None:
    async with _lock_for(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
        await asyncio.to_thread(tmp.write_text, json.dumps(data, indent=2), "utf8")
        await asyncio.to_thread(os.replace, str(tmp), str(path))


async def get_watchlist(path: Path, user_id: str | int) -> list[str]:
    data = await load(path)
    entry = data.get(str(user_id))
    return list(entry.get("symbols", [])) if entry else []


async def add_symbols(path: Path, user_id: str | int, symbols: list[str]) -> list[str]:
    import re

    normalised = []
    for raw in symbols:
        s = _to_yahoo(raw)
        if not re.match(VALID_SYMBOL_RE, s.replace(".NS", "")):
            continue
        normalised.append(s)

    async with _lock_for(path):
        data = await load(path)
        entry = data.setdefault(str(user_id), {"symbols": [], "updated_at": ""})
        existing = list(entry.get("symbols", []))
        for s in normalised:
            if s not in existing:
                existing.append(s)
        entry["symbols"] = existing
        entry["updated_at"] = datetime.now(tz=UTC).isoformat()
        data[str(user_id)] = entry
        await _write_unlocked(path, data)
    return list(existing)


async def remove_symbol(path: Path, user_id: str | int, symbol: str) -> list[str]:
    target = _to_yahoo(symbol)
    async with _lock_for(path):
        data = await load(path)
        entry = data.get(str(user_id))
        if not entry:
            return []
        remaining = [s for s in entry.get("symbols", []) if s != target]
        entry["symbols"] = remaining
        entry["updated_at"] = datetime.now(tz=UTC).isoformat()
        data[str(user_id)] = entry
        await _write_unlocked(path, data)
    return remaining


async def _write_unlocked(path: Path, data: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    await asyncio.to_thread(tmp.write_text, json.dumps(data, indent=2), "utf8")
    await asyncio.to_thread(os.replace, str(tmp), str(path))
