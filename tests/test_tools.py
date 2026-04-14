from pathlib import Path

from bot.tools import WatchlistTools, _to_yahoo


def test_to_yahoo_normalises_bare_symbol():
    assert _to_yahoo("reliance") == "RELIANCE.NS"
    assert _to_yahoo("TCS") == "TCS.NS"
    assert _to_yahoo("RELIANCE.NS") == "RELIANCE.NS"
    assert _to_yahoo(" infy ") == "INFY.NS"


def test_watchlist_tools_round_trip(tmp_path: Path):
    store = tmp_path / "watchlist.json"
    wl = WatchlistTools(store_path=store, user_id="42")

    out = wl.add("reliance")
    assert out["ok"] is True
    assert out["added"] == "RELIANCE.NS"
    assert "RELIANCE.NS" in out["items"]

    listing = wl.list_items()
    assert listing["items"] == ["RELIANCE.NS"]

    removed = wl.remove("RELIANCE.NS")
    assert removed["items"] == []
