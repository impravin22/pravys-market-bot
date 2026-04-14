import pytest

from core.rs_rating import ReturnPoint, classify_rs, compute_12m_return, rank_by_return


def test_compute_12m_return_basic():
    assert compute_12m_return([100.0, 110.0, 120.0]) == pytest.approx(0.2)


def test_compute_12m_return_handles_none_and_nan():
    import math

    assert compute_12m_return([100.0, None, math.nan, 150.0]) == pytest.approx(0.5)


def test_compute_12m_return_returns_none_for_too_few_points():
    assert compute_12m_return([]) is None
    assert compute_12m_return([100.0]) is None


def test_compute_12m_return_rejects_zero_first_price():
    assert compute_12m_return([0.0, 50.0, 60.0]) is None


def test_rank_by_return_percentile_mapping():
    points = [
        ReturnPoint("A", 0.10),
        ReturnPoint("B", 0.30),
        ReturnPoint("C", 0.50),
        ReturnPoint("D", 0.70),
        ReturnPoint("E", 0.90),
    ]
    rs = rank_by_return(points)
    # Sorted ascending: A, B, C, D, E → percentiles 0, 25, 50, 75, 100
    assert rs["A"] == 0.0
    assert rs["B"] == 25.0
    assert rs["C"] == 50.0
    assert rs["D"] == 75.0
    assert rs["E"] == 100.0


def test_rank_by_return_skips_invalid_entries():
    points = [
        ReturnPoint("A", 0.10),
        ReturnPoint("B", None),
        ReturnPoint("C", 0.30),
    ]
    rs = rank_by_return(points)
    assert "B" not in rs
    assert rs["A"] == 0.0
    assert rs["C"] == 100.0


def test_rank_by_return_empty_universe():
    assert rank_by_return([]) == {}


def test_classify_rs_tiers():
    assert classify_rs(92) == "Elite leader"
    assert classify_rs(85) == "Leader"
    assert classify_rs(65) == "Neutral"
    assert classify_rs(45) == "Laggard"
    assert classify_rs(20) == "Weak"
