from datetime import datetime
from zoneinfo import ZoneInfo

from core.canslim import MarketRegime, StockFundamentals, score
from core.digest_builder import (
    DailyMover,
    IndexSnapshot,
    build_evening_recap,
    build_morning_pulse,
    build_on_demand_top5,
    build_weekly_recap,
    build_weekly_top3,
)
from core.nse_data import Quote


def _now() -> datetime:
    return datetime(2026, 4, 14, 3, 0, tzinfo=ZoneInfo("UTC"))  # 08:30 IST, 11:00 TPE


def _uptrend() -> MarketRegime:
    return MarketRegime(
        nifty_above_50dma=True,
        nifty_above_200dma=True,
        nifty_5d_trend_up=True,
        phase="confirmed-uptrend",
    )


def _sample_score() -> object:
    return score(
        StockFundamentals(
            symbol="RELIANCE.NS",
            last_close=950.0,
            high_52w=1000.0,
            avg_vol_50d=1_000_000,
            last_volume=2_000_000,
            quarterly_eps_yoy_pct=45.0,
            annual_eps_3y_cagr_pct=28.0,
            rs_rating=92.0,
            fii_dii_5d_net_positive=True,
        ),
        _uptrend(),
    )


def test_morning_pulse_contains_all_sections():
    text = build_morning_pulse(
        now=_now(),
        market_tz="Asia/Kolkata",
        regime=_uptrend(),
        indices=[IndexSnapshot("Nifty 50", 22450.0, 1.2)],
        commodities=[Quote("GC=F", "Gold (USD/oz)", 2400.0, 2390.0)],
        top_scores=[_sample_score()],
        global_cues_commentary="US futures firm overnight.",
    )
    assert "Morning Pulse" in text
    assert "Market Direction" in text
    assert "Confirmed Uptrend" in text
    assert "Index Snapshot" in text
    assert "Nifty 50" in text
    assert "Commodities" in text
    assert "Gold" in text
    assert "RELIANCE.NS" in text
    assert "Global cues" in text
    assert "Educational signals" in text


def test_morning_pulse_renders_uptrend_under_pressure():
    regime = MarketRegime(
        nifty_above_50dma=False,
        nifty_above_200dma=True,
        nifty_5d_trend_up=False,
        phase="uptrend-under-pressure",
    )
    text = build_morning_pulse(
        now=_now(),
        market_tz="Asia/Kolkata",
        regime=regime,
        indices=[],
        commodities=[],
        top_scores=[],
    )
    assert "Uptrend Under Pressure" in text
    assert "below 50-DMA" in text
    assert "5d flat/down" in text


def test_morning_pulse_renders_downtrend_phase():
    regime = MarketRegime(
        nifty_above_50dma=False,
        nifty_above_200dma=False,
        nifty_5d_trend_up=False,
        phase="downtrend",
    )
    text = build_morning_pulse(
        now=_now(),
        market_tz="Asia/Kolkata",
        regime=regime,
        indices=[],
        commodities=[],
        top_scores=[],
    )
    assert "Downtrend" in text
    assert "reduce exposure" in text


def test_evening_recap_includes_gainers_losers_watchlist_narrative():
    text = build_evening_recap(
        now=_now(),
        market_tz="Asia/Kolkata",
        indices=[IndexSnapshot("Nifty 50", 22610.0, 0.7)],
        commodities=[Quote("INR=X", "USD/INR", 83.15, 83.20)],
        top_gainers=[DailyMover("TITAN.NS", 4.2, 2.4, "flat-base breakout confirmed")],
        top_losers=[DailyMover("ITC.NS", -2.1, 1.8)],
        watchlist_actions=["RELIANCE hit buy pivot at ₹2,850"],
        narrative="Breadth positive; rate-sensitives led.",
    )
    assert "Evening Recap" in text
    assert "TITAN.NS" in text
    assert "flat-base breakout" in text
    assert "ITC.NS" in text
    assert "RELIANCE hit buy pivot" in text
    assert "Breadth positive" in text


def test_weekly_recap_includes_week_labels_gainers_losers_narrative_and_risk_footer():
    text = build_weekly_recap(
        now=_now(),
        market_tz="Asia/Kolkata",
        indices=[IndexSnapshot("Nifty 50", 22780.0, 2.4)],
        commodities=[Quote("GC=F", "Gold (USD/oz)", 2450.0, 2400.0)],
        top_gainers=[DailyMover("TATAMOTORS.NS", 9.8, 2.1, "base breakout held")],
        top_losers=[DailyMover("PAYTM.NS", -7.3, 1.4)],
        narrative="IT leadership faded; defensives bid.",
    )
    assert "Week in Review" in text
    assert "Week ending" in text
    assert "How the market did this week" in text
    assert "TATAMOTORS.NS" in text
    assert "base breakout held" in text
    assert "PAYTM.NS" in text
    assert "IT leadership faded" in text
    # Week-span digest must always footer the risk rules.
    assert "Cut losses at" in text
    assert "Educational signals" in text


def test_weekly_recap_handles_empty_movers_without_crashing():
    text = build_weekly_recap(
        now=_now(),
        market_tz="Asia/Kolkata",
        indices=[],
        commodities=[],
        top_gainers=[],
        top_losers=[],
    )
    assert "Week in Review" in text
    assert "(index snapshot unavailable)" in text
    assert "(data unavailable)" in text
    # No narrative section when empty.
    assert "Gemini's week take" not in text


def test_weekly_recap_escapes_html_in_symbols_and_narrative():
    text = build_weekly_recap(
        now=_now(),
        market_tz="Asia/Kolkata",
        indices=[IndexSnapshot("<b>Nifty</b>", 100.0, 1.0)],
        commodities=[],
        top_gainers=[DailyMover("<script>", 5.0, 1.0, "<img src=x>")],
        top_losers=[],
        narrative="<script>alert(1)</script>",
    )
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "&lt;img src=x&gt;" in text


def test_weekly_top3_renders_full_letter_breakdown_and_risk_footer():
    text = build_weekly_top3(
        now=_now(),
        market_tz="Asia/Kolkata",
        picks=[(_sample_score(), "Strong earnings momentum; watch for pivot at ₹1,000.")],
    )
    assert "Weekly Top 3" in text
    assert "RELIANCE.NS — CAN SLIM 7/7" in text
    assert "✅ C:" in text
    assert "Strong earnings momentum" in text
    # Playbook risk rules must always footer the weekly digest.
    assert "Cut losses at" in text
    assert "25/8 plan" in text
    assert "8 weeks" in text


def test_on_demand_top5_shows_pull_time_and_picks():
    text = build_on_demand_top5(
        now=_now(),
        market_tz="Asia/Kolkata",
        top_scores=[_sample_score()],
        commentary="Momentum skewing toward IT and private banks.",
    )
    assert "Top 5 CAN SLIM picks" in text
    assert "Pulled at" in text
    assert "RELIANCE.NS" in text
    assert "Momentum skewing" in text


def test_html_escaping_applied_to_symbols_and_notes():
    malicious = StockFundamentals(
        symbol="<script>",
        last_close=100.0,
        high_52w=110.0,
        avg_vol_50d=1,
        last_volume=2,
        quarterly_eps_yoy_pct=40.0,
        annual_eps_3y_cagr_pct=25.0,
        rs_rating=90.0,
        fii_dii_5d_net_positive=True,
    )
    text = build_morning_pulse(
        now=_now(),
        market_tz="Asia/Kolkata",
        regime=_uptrend(),
        indices=[IndexSnapshot("<b>nifty</b>", 100.0, 1.0)],
        commodities=[],
        top_scores=[score(malicious, _uptrend())],
    )
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "&lt;b&gt;nifty&lt;/b&gt;" in text
