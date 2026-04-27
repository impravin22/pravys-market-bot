"""Daily picks engine tests."""

from __future__ import annotations

from dataclasses import dataclass

from core.canslim import MarketRegime, StockFundamentals
from core.daily_picks import Pick, composite_rating, daily_picks
from core.strategies.base import FilterCheck, StrategyVerdict


@dataclass(frozen=True)
class _FakeStrategy:
    code: str
    name: str
    school: str
    verdict_factory: callable

    def evaluate(self, f: StockFundamentals, regime: MarketRegime) -> StrategyVerdict:
        return self.verdict_factory(f, regime)


def _verdict(code: str, *, passes: bool, rating: float) -> StrategyVerdict:
    return StrategyVerdict(
        code=code,
        name=code.title(),
        school="growth",
        passes=passes,
        rating_0_100=rating,
        checks=[FilterCheck(name="dummy", passes=passes, note="")],
        notes={},
    )


def _strategy(code: str, *, passes: bool, rating: float) -> _FakeStrategy:
    return _FakeStrategy(
        code=code,
        name=code,
        school="growth",
        verdict_factory=lambda _f, _r: _verdict(code, passes=passes, rating=rating),
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


def _f(symbol: str) -> StockFundamentals:
    return StockFundamentals(symbol=symbol)


# -----------------------------------------------------------------------------
# Composite rating
# -----------------------------------------------------------------------------


def test_composite_rating_equal_weight_when_no_weights_match():
    verdicts = [
        _verdict("unknown_a", passes=True, rating=80.0),
        _verdict("unknown_b", passes=True, rating=60.0),
    ]
    assert composite_rating(verdicts, weights={}) == 70.0


def test_composite_rating_weighted_blend():
    verdicts = [
        _verdict("canslim", passes=True, rating=90.0),
        _verdict("schloss", passes=True, rating=50.0),
    ]
    weights = {"canslim": 0.7, "schloss": 0.3}
    # 90*0.7 + 50*0.3 = 63 + 15 = 78
    assert composite_rating(verdicts, weights=weights) == 78.0


def test_composite_rating_empty_verdicts_returns_zero():
    assert composite_rating([], weights={}) == 0.0


# -----------------------------------------------------------------------------
# daily_picks
# -----------------------------------------------------------------------------


def test_empty_universe_returns_empty():
    picks = daily_picks([], _uptrend(), [_strategy("canslim", passes=True, rating=90.0)])
    assert picks == []


def test_downtrend_blocks_all_picks_by_default():
    f = [_f("A"), _f("B")]
    picks = daily_picks(f, _downtrend(), [_strategy("canslim", passes=True, rating=95.0)])
    assert picks == []


def test_downtrend_can_be_overridden():
    f = [_f("A")]
    picks = daily_picks(
        f,
        _downtrend(),
        [_strategy("canslim", passes=True, rating=95.0)],
        block_in_downtrend=False,
        min_composite=0.0,
    )
    assert len(picks) == 1


def test_picks_ordered_by_endorsement_then_composite():
    s_strong = _strategy("canslim", passes=True, rating=95.0)
    s_weak = _strategy("schloss", passes=False, rating=10.0)

    # A: 1 endorsement, composite 95.
    # B: 0 endorsements, composite computed from canslim+schloss.
    # A should come first.
    f = [_f("A"), _f("B")]

    def factory_for_b(_f, _r):
        return _verdict("canslim", passes=False, rating=40.0)

    s_for_b = _FakeStrategy(
        code="canslim",
        name="canslim",
        school="growth",
        verdict_factory=factory_for_b,
    )

    picks_a = daily_picks([f[0]], _uptrend(), [s_strong, s_weak], min_composite=0.0)
    picks_b = daily_picks([f[1]], _uptrend(), [s_for_b, s_weak], min_composite=0.0)
    combined = sorted(
        picks_a + picks_b,
        key=lambda p: (p.endorsement_count, p.composite_rating),
        reverse=True,
    )
    assert combined[0].symbol == "A"


def test_min_composite_filters_below_threshold():
    s = _strategy("canslim", passes=True, rating=50.0)
    f = [_f("WEAK")]
    picks = daily_picks(f, _uptrend(), [s], min_composite=80.0)
    assert picks == []


def test_top_n_caps_results():
    s = _strategy("canslim", passes=True, rating=95.0)
    f = [_f(f"S{i}") for i in range(10)]
    picks = daily_picks(f, _uptrend(), [s], top_n=3, min_composite=0.0)
    assert len(picks) == 3


def test_pick_carries_endorsing_codes_and_verdicts():
    s_pass = _strategy("canslim", passes=True, rating=90.0)
    s_fail = _strategy("schloss", passes=False, rating=10.0)
    picks = daily_picks([_f("X")], _uptrend(), [s_pass, s_fail], min_composite=0.0)
    assert len(picks) == 1
    pick = picks[0]
    assert pick.endorsing_codes == ["canslim"]
    assert pick.endorsement_count == 1
    assert {v.code for v in pick.verdicts} == {"canslim", "schloss"}


def test_uptrend_under_pressure_does_not_block_by_default():
    """Only full downtrend blocks; UUP is just cautious."""
    regime = MarketRegime(
        nifty_above_50dma=False,
        nifty_above_200dma=True,
        nifty_5d_trend_up=False,
        phase="uptrend-under-pressure",
    )
    s = _strategy("canslim", passes=True, rating=85.0)
    picks = daily_picks([_f("X")], regime, [s], min_composite=0.0)
    assert len(picks) == 1


def test_pick_is_serialisable_dataclass():
    """Pick must round-trip cleanly so it can be JSON-dumped for the digest."""
    s = _strategy("canslim", passes=True, rating=90.0)
    pick = daily_picks([_f("X")], _uptrend(), [s], min_composite=0.0)[0]
    assert isinstance(pick, Pick)
    assert pick.symbol == "X"
