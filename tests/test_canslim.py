from core.canslim import (
    MarketRegime,
    StockFundamentals,
    rank_universe,
    score,
)


def _strong() -> StockFundamentals:
    return StockFundamentals(
        symbol="STRONG.NS",
        last_close=950.0,
        high_52w=1000.0,
        low_52w=600.0,
        avg_vol_50d=1_000_000,
        last_volume=2_000_000,
        quarterly_eps_yoy_pct=45.0,
        annual_eps_3y_cagr_pct=28.0,
        rs_rating=92.0,
        fii_dii_5d_net_positive=True,
    )


def _weak() -> StockFundamentals:
    return StockFundamentals(
        symbol="WEAK.NS",
        last_close=50.0,
        high_52w=200.0,
        low_52w=40.0,
        avg_vol_50d=1_000_000,
        last_volume=500_000,
        quarterly_eps_yoy_pct=-10.0,
        annual_eps_3y_cagr_pct=5.0,
        rs_rating=25.0,
        fii_dii_5d_net_positive=False,
    )


def _uptrend() -> MarketRegime:
    return MarketRegime(
        nifty_above_50dma=True,
        nifty_above_200dma=True,
        nifty_5d_trend_up=True,
        phase="confirmed-uptrend",
    )


def _downtrend() -> MarketRegime:
    return MarketRegime(
        nifty_above_50dma=False,
        nifty_above_200dma=False,
        nifty_5d_trend_up=False,
        phase="downtrend",
    )


def test_strong_stock_in_uptrend_scores_7_of_7():
    s = score(_strong(), _uptrend())
    assert s.binary_score == 7
    assert s.passed_codes == ["C", "A", "N", "S", "L", "I", "M"]
    assert s.continuous_score > 0


def test_weak_stock_scores_0_in_downtrend():
    s = score(_weak(), _downtrend())
    assert s.binary_score == 0
    assert s.failed_codes == ["C", "A", "N", "S", "L", "I", "M"]


def test_missing_data_neither_passes_nor_fails():
    f = StockFundamentals(symbol="HOLE.NS")  # all Nones
    s = score(f, _uptrend())
    # Only M can evaluate (uses regime); everything else is None.
    assert s.binary_score == 1  # M passes
    for code in ("C", "A", "N", "S", "L", "I"):
        assert s.letters[code].passes is None


def test_rank_universe_filters_and_orders_by_binary_then_continuous():
    mid = StockFundamentals(
        symbol="MID.NS",
        last_close=905.0,
        high_52w=1000.0,
        avg_vol_50d=1_000_000,
        last_volume=1_800_000,
        quarterly_eps_yoy_pct=30.0,
        annual_eps_3y_cagr_pct=22.0,
        rs_rating=82.0,
        fii_dii_5d_net_positive=True,
    )
    ranked = rank_universe([_strong(), mid, _weak()], _uptrend(), min_binary=5)
    assert [r.symbol for r in ranked] == ["STRONG.NS", "MID.NS"]
    assert ranked[0].binary_score >= ranked[1].binary_score


def test_boundary_quarterly_eps_at_25_percent_passes():
    f = _strong()
    f = StockFundamentals(
        symbol=f.symbol,
        last_close=f.last_close,
        high_52w=f.high_52w,
        low_52w=f.low_52w,
        avg_vol_50d=f.avg_vol_50d,
        last_volume=f.last_volume,
        quarterly_eps_yoy_pct=25.0,  # exactly at threshold
        annual_eps_3y_cagr_pct=f.annual_eps_3y_cagr_pct,
        rs_rating=f.rs_rating,
        fii_dii_5d_net_positive=f.fii_dii_5d_net_positive,
    )
    s = score(f, _uptrend())
    assert s.letters["C"].passes is True


def test_market_regime_m_fails_if_any_dma_below():
    regime = MarketRegime(
        nifty_above_50dma=True,
        nifty_above_200dma=False,
        nifty_5d_trend_up=True,
        phase="rally-attempt",
    )
    s = score(_strong(), regime)
    assert s.letters["M"].passes is False


def test_classify_phase_covers_all_four_playbook_states():
    from core.canslim import classify_phase

    assert (
        classify_phase(above_50dma=True, above_200dma=True, five_day_up=True) == "confirmed-uptrend"
    )
    assert (
        classify_phase(above_50dma=False, above_200dma=True, five_day_up=True)
        == "uptrend-under-pressure"
    )
    assert (
        classify_phase(above_50dma=False, above_200dma=False, five_day_up=True) == "rally-attempt"
    )
    assert classify_phase(above_50dma=False, above_200dma=False, five_day_up=False) == "downtrend"
