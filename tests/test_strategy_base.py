"""Strategy protocol + FilterCheck aggregation tests."""

from __future__ import annotations

from core.strategies.base import FilterCheck, StrategyVerdict, rating_from_checks


def test_rating_from_checks_empty_list_returns_zero_and_fail():
    passes, rating = rating_from_checks([])
    assert passes is False
    assert rating == 0.0


def test_rating_from_checks_all_pass_returns_full_score_and_pass():
    checks = [
        FilterCheck(name="a", passes=True, note=""),
        FilterCheck(name="b", passes=True, note=""),
    ]
    passes, rating = rating_from_checks(checks)
    assert passes is True
    assert rating == 100.0


def test_rating_from_checks_all_fail_returns_zero_and_fail():
    checks = [
        FilterCheck(name="a", passes=False, note=""),
        FilterCheck(name="b", passes=False, note=""),
    ]
    passes, rating = rating_from_checks(checks)
    assert passes is False
    assert rating == 0.0


def test_rating_from_checks_partial_pass_default_requires_all():
    checks = [
        FilterCheck(name="a", passes=True, note=""),
        FilterCheck(name="b", passes=False, note=""),
    ]
    passes, rating = rating_from_checks(checks)
    assert passes is False
    assert rating == 50.0


def test_rating_from_checks_with_require_threshold():
    checks = [
        FilterCheck(name="a", passes=True, note=""),
        FilterCheck(name="b", passes=True, note=""),
        FilterCheck(name="c", passes=False, note=""),
    ]
    passes, rating = rating_from_checks(checks, require=2)
    assert passes is True  # 2 of 3 passes the threshold
    assert rating == round(2 / 3 * 100, 1)


def test_rating_from_checks_weighted_components():
    checks = [
        FilterCheck(name="heavy", passes=True, note="", weight=3.0),
        FilterCheck(name="light", passes=False, note="", weight=1.0),
    ]
    passes, rating = rating_from_checks(checks, require=1)
    assert passes is True
    assert rating == 75.0  # 3 of 4 weight passing


def test_rating_from_checks_zero_weight_total_returns_zero():
    checks = [FilterCheck(name="a", passes=True, note="", weight=0.0)]
    passes, rating = rating_from_checks(checks)
    assert passes is False
    assert rating == 0.0


def test_strategy_verdict_passing_and_failing_partitions_checks():
    v = StrategyVerdict(
        code="demo",
        name="Demo",
        school="growth",
        passes=True,
        rating_0_100=80.0,
        checks=[
            FilterCheck(name="ok1", passes=True, note=""),
            FilterCheck(name="ok2", passes=True, note=""),
            FilterCheck(name="bad", passes=False, note=""),
        ],
        notes={},
    )
    assert v.passing_checks == ["ok1", "ok2"]
    assert v.failing_checks == ["bad"]
