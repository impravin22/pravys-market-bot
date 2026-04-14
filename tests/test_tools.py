from pathlib import Path

import pandas as pd
import pytest

from bot.tools import (
    WatchlistTools,
    _to_yahoo,
    commodity_snapshot,
    explain_canslim_for,
    market_regime_now,
    price_action,
    top_canslim_picks,
)
from core.canslim import CanslimScore, LetterResult, MarketRegime
from core.nse_data import Quote, StockHistory
from core.screener import ScreenerResult


def _fake_score(symbol: str, binary: int = 6, continuous: float = 3.5) -> CanslimScore:
    letters = {c: LetterResult(c, True, 0.5, f"{c} note") for c in "CANSLIM"}
    # Fail the last letter for realism.
    letters["M"] = LetterResult("M", False, 0.0, "below 200-DMA")
    return CanslimScore(
        symbol=symbol,
        letters=letters,
        binary_score=binary,
        continuous_score=continuous,
    )


def _fake_regime(phase: str = "confirmed-uptrend") -> MarketRegime:
    return MarketRegime(
        nifty_above_50dma=True,
        nifty_above_200dma=True,
        nifty_5d_trend_up=True,
        phase=phase,
    )


def test_to_yahoo_normalises_bare_symbol():
    assert _to_yahoo("reliance") == "RELIANCE.NS"
    assert _to_yahoo("TCS") == "TCS.NS"
    assert _to_yahoo("RELIANCE.NS") == "RELIANCE.NS"
    assert _to_yahoo(" infy ") == "INFY.NS"


def test_top_canslim_picks_returns_shaped_payload(monkeypatch):
    def fake_run_screener(**kwargs):
        return ScreenerResult(
            regime=_fake_regime(),
            scored=[_fake_score("RELIANCE.NS", binary=7), _fake_score("INFY.NS", binary=6)],
            nifty_last_close=22450.0,
            universe_size=507,
            elapsed_seconds=172.3,
        )

    monkeypatch.setattr("bot.tools.run_screener", fake_run_screener)
    out = top_canslim_picks(limit=5, min_binary=5)
    assert "regime" in out and out["regime"]["phase"] == "confirmed-uptrend"
    assert out["universe_size"] == 507
    assert len(out["picks"]) == 2
    pick = out["picks"][0]
    assert pick["symbol"] == "RELIANCE.NS"
    assert pick["binary_score"] == 7
    assert set(pick["letters"].keys()) == set("CANSLIM")


def test_top_canslim_picks_clamps_limit(monkeypatch):
    def fake_run_screener(**kwargs):
        return ScreenerResult(
            regime=_fake_regime(),
            scored=[_fake_score(f"S{i}.NS") for i in range(15)],
            nifty_last_close=22450.0,
            universe_size=100,
            elapsed_seconds=10.0,
        )

    monkeypatch.setattr("bot.tools.run_screener", fake_run_screener)
    out = top_canslim_picks(limit=99, min_binary=5)
    assert len(out["picks"]) == 10  # clamped to max 10
    out = top_canslim_picks(limit=0, min_binary=5)
    assert len(out["picks"]) == 1  # clamped to min 1


def test_top_canslim_picks_reports_error(monkeypatch):
    monkeypatch.setattr("bot.tools.run_screener", lambda **k: None)
    out = top_canslim_picks()
    assert "error" in out


def test_explain_canslim_for_normalises_symbol(monkeypatch):
    calls: list[list[str]] = []

    def fake_run_screener(*, universe, min_binary):
        calls.append(universe)
        return ScreenerResult(
            regime=_fake_regime(),
            scored=[_fake_score(universe[0], binary=4)],
            nifty_last_close=22450.0,
            universe_size=1,
            elapsed_seconds=1.0,
        )

    monkeypatch.setattr("bot.tools.run_screener", fake_run_screener)
    out = explain_canslim_for("reliance")
    assert calls == [["RELIANCE.NS"]]
    assert out["canslim"]["symbol"] == "RELIANCE.NS"
    assert out["regime_phase"] == "confirmed-uptrend"


def test_explain_canslim_for_reports_missing(monkeypatch):
    monkeypatch.setattr("bot.tools.run_screener", lambda **k: None)
    assert "error" in explain_canslim_for("zzz")


def test_market_regime_now(monkeypatch):
    # Build a 1-year daily Close series to pass the 200 min-length gate.
    closes = pd.Series([100.0 + i for i in range(252)])
    df = pd.DataFrame(
        {"Open": closes, "High": closes, "Low": closes, "Close": closes, "Volume": 0.0}
    )
    hist = StockHistory(symbol="^NSEI", history=df)
    monkeypatch.setattr("core.nse_data.fetch_nifty", lambda period="1y": hist)
    out = market_regime_now()
    assert out["phase"] in (
        "confirmed-uptrend",
        "uptrend-under-pressure",
        "rally-attempt",
        "downtrend",
    )
    assert out["nifty_last_close"] == 351.0


def test_market_regime_now_reports_no_data(monkeypatch):
    monkeypatch.setattr("core.nse_data.fetch_nifty", lambda period="1y": None)
    assert "error" in market_regime_now()


def test_price_action_returns_period_stats(monkeypatch):
    closes = [100.0, 110.0, 120.0, 130.0]
    volumes = [1000, 2000, 3000, 4000]
    idx = pd.date_range("2026-01-01", periods=4, freq="D")
    df = pd.DataFrame(
        {"Open": closes, "High": closes, "Low": closes, "Close": closes, "Volume": volumes},
        index=idx,
    )
    hist = StockHistory(symbol="RELIANCE.NS", history=df)
    monkeypatch.setattr("bot.tools.fetch_history", lambda s, period="1mo": hist)
    out = price_action("RELIANCE", "1mo")
    assert out["symbol"] == "RELIANCE.NS"
    assert out["period_change_pct"] == 30.0  # 100 → 130
    assert out["period_high"] == 130.0
    assert out["avg_volume"] == 2500.0


def test_price_action_reports_empty(monkeypatch):
    monkeypatch.setattr("bot.tools.fetch_history", lambda s, period="1mo": None)
    assert "error" in price_action("RELIANCE")


def test_commodity_snapshot(monkeypatch):
    monkeypatch.setattr(
        "bot.tools.fetch_commodity_quotes",
        lambda: [
            Quote(symbol="GC=F", label="Gold (USD/oz)", last=2400.0, prev_close=2380.0),
            Quote(symbol="INR=X", label="USD/INR", last=85.0, prev_close=85.5),
        ],
    )
    out = commodity_snapshot()
    assert len(out["quotes"]) == 2
    assert out["quotes"][0]["label"] == "Gold (USD/oz)"
    assert out["quotes"][0]["change_pct"] == pytest.approx(0.84, abs=0.01)


def test_commodity_snapshot_reports_empty(monkeypatch):
    monkeypatch.setattr("bot.tools.fetch_commodity_quotes", lambda: [])
    assert "error" in commodity_snapshot()


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
