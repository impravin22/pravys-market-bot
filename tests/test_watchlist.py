import asyncio
from pathlib import Path

import pytest

from core import watchlist


@pytest.fixture
def tmp_store(tmp_path: Path) -> Path:
    return tmp_path / "wl.json"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_empty_watchlist_returns_empty_list(tmp_store):
    out = asyncio.run(watchlist.get_watchlist(tmp_store, user_id=42))
    assert out == []


def test_add_symbols_normalises_to_yahoo_format(tmp_store):
    out = asyncio.run(watchlist.add_symbols(tmp_store, user_id=42, symbols=["reliance", "INFY"]))
    assert out == ["RELIANCE.NS", "INFY.NS"]


def test_add_symbols_is_idempotent(tmp_store):
    asyncio.run(watchlist.add_symbols(tmp_store, user_id=42, symbols=["RELIANCE"]))
    out = asyncio.run(watchlist.add_symbols(tmp_store, user_id=42, symbols=["RELIANCE", "INFY"]))
    assert out == ["RELIANCE.NS", "INFY.NS"]


def test_remove_symbol(tmp_store):
    asyncio.run(watchlist.add_symbols(tmp_store, user_id=42, symbols=["RELIANCE", "INFY"]))
    remaining = asyncio.run(watchlist.remove_symbol(tmp_store, user_id=42, symbol="RELIANCE"))
    assert remaining == ["INFY.NS"]


def test_different_users_keep_separate_lists(tmp_store):
    asyncio.run(watchlist.add_symbols(tmp_store, user_id=1, symbols=["RELIANCE"]))
    asyncio.run(watchlist.add_symbols(tmp_store, user_id=2, symbols=["TCS"]))
    assert asyncio.run(watchlist.get_watchlist(tmp_store, user_id=1)) == ["RELIANCE.NS"]
    assert asyncio.run(watchlist.get_watchlist(tmp_store, user_id=2)) == ["TCS.NS"]


def test_unsafe_keys_are_stripped_on_load(tmp_store):
    tmp_store.parent.mkdir(parents=True, exist_ok=True)
    tmp_store.write_text(
        '{"__proto__": {"symbols":["EVIL"]}, "42": {"symbols":["RELIANCE.NS"]}}', "utf8"
    )
    out = asyncio.run(watchlist.load(tmp_store))
    assert "__proto__" not in out
    assert "42" in out


def test_add_rejects_garbage_symbols(tmp_store):
    out = asyncio.run(
        watchlist.add_symbols(tmp_store, user_id=1, symbols=["../etc/passwd", "VALID"])
    )
    assert out == ["VALID.NS"]
