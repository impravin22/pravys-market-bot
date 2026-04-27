"""Snapshot → StockFundamentals enrichment tests."""

from __future__ import annotations

from datetime import UTC, datetime

from core.canslim import StockFundamentals
from core.data.screener_in import ScreenerSnapshot, enrich_fundamentals_with_snapshot


def _snap(**overrides) -> ScreenerSnapshot:
    base = {
        "symbol": "X.NS",
        "market_cap": 1.0e6,
        "current_price": 100.0,
        "pe_ratio": 20.0,
        "pb_ratio": 1.5,
        "book_value": 66.7,
        "dividend_yield_pct": 0.5,
        "pays_dividend": True,
        "roe_pct": 14.0,
        "roe_5y_avg_pct": 14.0,
        "roce_pct": 15.0,
        "debt_to_equity": 0.4,
        "face_value": 10.0,
        "fetched_at": datetime.now(tz=UTC),
    }
    base.update(overrides)
    return ScreenerSnapshot(**base)


def test_enrich_replaces_missing_ratio_fields():
    base = StockFundamentals(symbol="X.NS", last_close=100.0)
    snap = _snap()
    out = enrich_fundamentals_with_snapshot(base, snap)
    assert out.pe_ratio == 20.0
    assert out.pb_ratio == 1.5
    assert out.debt_to_equity == 0.4
    assert out.roe_5y_avg_pct == 14.0
    assert out.dividend_yield_pct == 0.5
    assert out.pays_dividend is True


def test_enrich_does_not_clobber_existing_values():
    base = StockFundamentals(symbol="X.NS", pe_ratio=10.0, debt_to_equity=0.1, pays_dividend=False)
    snap = _snap()
    out = enrich_fundamentals_with_snapshot(base, snap)
    # Existing values preserved; only None fields backfilled.
    assert out.pe_ratio == 10.0
    assert out.debt_to_equity == 0.1
    assert out.pays_dividend is False


def test_enrich_skips_none_snapshot_fields():
    base = StockFundamentals(symbol="X.NS")
    snap = _snap(pe_ratio=None, pb_ratio=None, debt_to_equity=None)
    out = enrich_fundamentals_with_snapshot(base, snap)
    assert out.pe_ratio is None
    assert out.pb_ratio is None
    assert out.debt_to_equity is None


def test_enrich_returns_base_unchanged_when_snapshot_none():
    base = StockFundamentals(symbol="X.NS", last_close=100.0)
    out = enrich_fundamentals_with_snapshot(base, None)
    assert out is base


def test_enrich_derives_earnings_positive_recent_when_pe_present_and_positive():
    """If P/E is a positive number, EPS must be positive — useful for Schloss."""
    base = StockFundamentals(symbol="X.NS")
    snap = _snap(pe_ratio=15.0)
    out = enrich_fundamentals_with_snapshot(base, snap)
    assert out.earnings_positive_recent is True


def test_enrich_skips_earnings_positive_when_pe_missing():
    base = StockFundamentals(symbol="X.NS")
    snap = _snap(pe_ratio=None)
    out = enrich_fundamentals_with_snapshot(base, snap)
    assert out.earnings_positive_recent is None
