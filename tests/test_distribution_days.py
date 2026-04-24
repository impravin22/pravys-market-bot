"""Tests for the rolling 25-day distribution day tracker."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import httpx
import pytest

from core.distribution_days import (
    KEY_PREFIX,
    TTL_SECONDS,
    DistributionDayTracker,
)


@pytest.fixture()
def tracker() -> DistributionDayTracker:
    return DistributionDayTracker(redis_url="https://example.upstash.io", redis_token="token")


def _ok(value: str | None) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"result": value}
    resp.raise_for_status = MagicMock()
    return resp


class TestIsTodayDistribution:
    def test_decline_with_volume_up_is_distribution(self) -> None:
        assert DistributionDayTracker.is_today_distribution(-0.85, 6.0) is True

    def test_decline_with_volume_down_is_not_distribution(self) -> None:
        assert DistributionDayTracker.is_today_distribution(-1.5, -10.0) is False

    def test_marginal_decline_below_threshold_is_not_distribution(self) -> None:
        assert DistributionDayTracker.is_today_distribution(-0.05, 10.0) is False

    def test_advance_is_not_distribution(self) -> None:
        assert DistributionDayTracker.is_today_distribution(1.2, 5.0) is False

    def test_threshold_inclusive(self) -> None:
        # Exactly -0.2% on higher volume should count.
        assert DistributionDayTracker.is_today_distribution(-0.2, 1.0) is True


class TestRecordToday:
    @patch("core.distribution_days.httpx.post")
    @patch("core.distribution_days.httpx.get")
    def test_writes_active_flag_when_distribution(
        self, mock_get: MagicMock, mock_post: MagicMock, tracker: DistributionDayTracker
    ) -> None:
        mock_post.return_value = _ok(None)
        # Lookback queries see only today's freshly written value.
        mock_get.return_value = _ok("1")

        result = tracker.record_today(
            today=date(2026, 4, 23), nifty_change_pct=-0.84, volume_change_pct=6.0
        )

        assert result.is_distribution_day is True
        assert result.nifty_change_pct == -0.84
        assert result.active_count >= 1
        # Verify the SET call used the right key + value.
        _, kwargs = mock_post.call_args
        assert "marketsmith:dd:2026-04-23" in mock_post.call_args[0][0]
        assert kwargs["params"] == {"EX": TTL_SECONDS}

    @patch("core.distribution_days.httpx.post")
    @patch("core.distribution_days.httpx.get")
    def test_writes_zero_when_not_distribution(
        self, mock_get: MagicMock, mock_post: MagicMock, tracker: DistributionDayTracker
    ) -> None:
        mock_post.return_value = _ok(None)
        mock_get.return_value = _ok("0")

        result = tracker.record_today(
            today=date(2026, 4, 23), nifty_change_pct=0.5, volume_change_pct=12.0
        )
        assert result.is_distribution_day is False

    @patch("core.distribution_days.httpx.post")
    @patch("core.distribution_days.httpx.get")
    def test_redis_failure_degrades_to_zero(
        self, mock_get: MagicMock, mock_post: MagicMock, tracker: DistributionDayTracker
    ) -> None:
        mock_post.side_effect = httpx.HTTPError("boom")
        mock_get.side_effect = httpx.HTTPError("boom")

        result = tracker.record_today(
            today=date(2026, 4, 23), nifty_change_pct=-1.0, volume_change_pct=5.0
        )
        # Even on Redis failure the local distribution detection still runs.
        assert result.is_distribution_day is True
        assert result.active_count == 0


class TestCountActive:
    @patch("core.distribution_days.httpx.get")
    def test_counts_only_active_flags_in_window(
        self, mock_get: MagicMock, tracker: DistributionDayTracker
    ) -> None:
        # Map specific dates to specific stored values.
        stored = {
            f"{KEY_PREFIX}2026-04-23": "1",
            f"{KEY_PREFIX}2026-04-22": "0",
            f"{KEY_PREFIX}2026-04-21": "1",
            f"{KEY_PREFIX}2026-04-17": "1",
        }

        def _fake(url: str, headers: dict[str, str], timeout: float) -> MagicMock:
            for key, val in stored.items():
                if key in url:
                    return _ok(val)
            return _ok(None)

        mock_get.side_effect = _fake
        count = tracker.count_active(today=date(2026, 4, 23), lookback_trading_days=10)
        assert count == 3

    @patch("core.distribution_days.httpx.get")
    def test_skips_weekends(self, mock_get: MagicMock, tracker: DistributionDayTracker) -> None:
        mock_get.return_value = _ok(None)
        # Friday → Thursday → Wednesday … weekends should be skipped silently.
        tracker.count_active(today=date(2026, 4, 24), lookback_trading_days=5)
        # Pull all called URLs and confirm Sat/Sun (Apr 18/19) were not queried.
        called_urls = [call.args[0] for call in mock_get.call_args_list]
        assert all("2026-04-18" not in u for u in called_urls)
        assert all("2026-04-19" not in u for u in called_urls)
