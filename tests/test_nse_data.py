from datetime import date

from core.nse_data import is_trading_day, is_weekend, today_in_market


def test_today_in_market_returns_date():
    d = today_in_market()
    assert isinstance(d, date)


def test_is_weekend_detects_saturday_sunday():
    assert is_weekend(date(2026, 4, 11))  # Saturday
    assert is_weekend(date(2026, 4, 12))  # Sunday
    assert not is_weekend(date(2026, 4, 13))  # Monday


def test_is_trading_day_with_holidays_set():
    holidays = {date(2026, 3, 6)}  # Holi example
    assert is_trading_day(date(2026, 4, 13), holidays=holidays)
    assert not is_trading_day(date(2026, 3, 6), holidays=holidays)
    assert not is_trading_day(date(2026, 4, 11), holidays=holidays)  # Saturday


def test_is_trading_day_without_holidays_only_filters_weekends():
    assert is_trading_day(date(2026, 4, 14), holidays=None)
    assert not is_trading_day(date(2026, 4, 12), holidays=None)  # Sunday
