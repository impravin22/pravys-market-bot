"""Tests for the marketsmith_data snapshot builder."""

from __future__ import annotations

import pandas as pd
import pytest

from core.canslim import LetterResult, MarketRegime
from jobs.marketsmith_data import (
    IndexAction,
    _build_buy_watchlist,
    _movers,
    _serialise_index,
)


def _score(symbol: str, binary: int) -> object:
    # Build a minimal CanslimScore-like object compatible with _build_buy_watchlist.
    from core.canslim import CanslimScore

    letters = {c: LetterResult(c, c in "CL", 0.5, f"{c} note") for c in "CANSLIM"}
    return CanslimScore(symbol=symbol, letters=letters, binary_score=binary, continuous_score=1.0)


class TestBuildBuyWatchlist:
    def test_filters_below_min_binary(self) -> None:
        scores = [
            _score("FOO.NS", 6),
            _score("BAR.NS", 5),
            _score("BAZ.NS", 7),
        ]
        out = _build_buy_watchlist(scores, min_binary=6, top_n=8)
        symbols = [item["symbol"] for item in out]
        assert symbols == ["FOO", "BAZ"]

    def test_caps_at_top_n(self) -> None:
        scores = [_score(f"S{i}.NS", 7) for i in range(20)]
        out = _build_buy_watchlist(scores, min_binary=6, top_n=5)
        assert len(out) == 5

    def test_strips_ns_suffix(self) -> None:
        out = _build_buy_watchlist([_score("RELIANCE.NS", 7)])
        assert out[0]["symbol"] == "RELIANCE"


class TestSerialiseIndex:
    def test_renames_open_underscore_field(self) -> None:
        action = IndexAction(
            label="Nifty 50",
            last_close=24_173.05,
            open_=24_202.35,
            high=24_310.20,
            low=24_134.80,
            prev_close=24_378.10,
            change_pct=-0.84,
            volume=1_000_000,
            prev_volume=950_000,
            volume_change_pct=5.26,
            vs_21dma_pct=2.61,
            vs_50dma_pct=-0.66,
        )
        payload = _serialise_index(action)
        assert payload is not None
        assert "open" in payload
        assert "open_" not in payload
        assert payload["open"] == 24_202.35

    def test_handles_none(self) -> None:
        assert _serialise_index(None) is None


class TestMoversBreadth:
    def test_advances_and_declines_count_correctly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Patch fetch_history to deterministic responses.
        def fake_history(symbol: str, *, period: str = "5d", interval: str = "1d") -> object:
            from core.nse_data import StockHistory

            base = 100.0
            today = base + (5 if "GAIN" in symbol else -3 if "LOSS" in symbol else 0)
            df = pd.DataFrame(
                {
                    "Open": [base, today],
                    "High": [base, today],
                    "Low": [base, today],
                    "Close": [base, today],
                    "Volume": [1_000_000, 1_200_000],
                }
            )
            return StockHistory(symbol=symbol, history=df)

        monkeypatch.setattr("jobs.marketsmith_data.fetch_history", fake_history)
        movers, advances, declines = _movers(
            ["GAIN_A.NS", "GAIN_B.NS", "LOSS_A.NS", "FLAT.NS"], parallelism=2
        )
        assert advances == 2
        assert declines == 1
        # Flat doesn't count either side.
        assert {m.symbol for m in movers} == {"GAIN_A", "GAIN_B", "LOSS_A", "FLAT"}


def test_market_regime_uses_classify_phase() -> None:
    # Sanity that the upstream regime export still has the .phase field
    # we rely on inside the snapshot builder.
    regime = MarketRegime(
        nifty_above_50dma=True,
        nifty_above_200dma=True,
        nifty_5d_trend_up=True,
        phase="confirmed-uptrend",
    )
    assert regime.is_uptrend is True
