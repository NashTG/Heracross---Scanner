"""Tests for slug_from_ts and generate_range_timestamps."""
import re
import pytest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from polymarket_core import slug_from_ts, generate_range_timestamps, et_midnight_ts

ET_TZ = ZoneInfo("America/New_York")

# ---------------------------------------------------------------------------
# slug_from_ts
# ---------------------------------------------------------------------------

class TestSlugFromTs:
    # 2026-04-17 12:00 ET — divisible by 5m/15m/1h/4h
    BASE_TS = 1744905600

    @pytest.mark.parametrize("asset,expected_prefix", [
        ("BTC", "btc"),
        ("ETH", "eth"),
        ("SOL", "sol"),
        ("XRP", "xrp"),
    ])
    def test_5m_format(self, asset, expected_prefix):
        slug = slug_from_ts(self.BASE_TS, asset, "5m")
        assert slug == f"{expected_prefix}-updown-5m-{self.BASE_TS}"

    @pytest.mark.parametrize("asset,expected_prefix", [
        ("BTC", "btc"),
        ("ETH", "eth"),
        ("SOL", "sol"),
        ("XRP", "xrp"),
    ])
    def test_15m_format(self, asset, expected_prefix):
        slug = slug_from_ts(self.BASE_TS, asset, "15m")
        assert slug == f"{expected_prefix}-updown-15m-{self.BASE_TS}"

    @pytest.mark.parametrize("asset,expected_prefix", [
        ("BTC", "btc"),
        ("ETH", "eth"),
        ("SOL", "sol"),
        ("XRP", "xrp"),
    ])
    def test_4h_format(self, asset, expected_prefix):
        slug = slug_from_ts(self.BASE_TS, asset, "4h")
        assert slug == f"{expected_prefix}-updown-4h-{self.BASE_TS}"

    @pytest.mark.parametrize("asset,full_name", [
        ("BTC", "bitcoin"),
        ("ETH", "ethereum"),
        ("SOL", "solana"),
        ("XRP", "xrp"),
    ])
    def test_1h_format_contains_full_name(self, asset, full_name):
        slug = slug_from_ts(self.BASE_TS, asset, "1h")
        assert slug.startswith(full_name)
        # must contain hour + am/pm + "-et"
        assert slug.endswith("-et")
        # lowercase month
        dt = datetime.fromtimestamp(self.BASE_TS, tz=timezone.utc).astimezone(ET_TZ)
        assert dt.strftime("%B").lower() in slug

    def test_1h_hour_representation(self):
        # BASE_TS is 12:00 ET → hour "12pm"
        slug = slug_from_ts(self.BASE_TS, "BTC", "1h")
        assert "12pm" in slug

    def test_1h_am_hour(self):
        # 2026-04-17 09:00 ET
        ts_9am = et_midnight_ts("2026-04-17") + 9 * 3600
        slug = slug_from_ts(ts_9am, "BTC", "1h")
        assert "9am" in slug

    @pytest.mark.parametrize("asset,full_name", [
        ("BTC", "bitcoin"),
        ("ETH", "ethereum"),
        ("SOL", "solana"),
        ("XRP", "xrp"),
    ])
    def test_1d_format(self, asset, full_name):
        # 1d slug uses end date (start_ts + 86400 → next day)
        ts = et_midnight_ts("2026-04-17") + 12 * 3600  # noon ET
        slug = slug_from_ts(ts, asset, "1d")
        assert slug.startswith(f"{full_name}-up-or-down-on-")

    def test_1d_includes_year(self):
        ts = et_midnight_ts("2026-04-17") + 12 * 3600
        slug = slug_from_ts(ts, "BTC", "1d")
        assert "2026" in slug

    def test_1h_leading_zero_stripped(self):
        # hour 9 should be "9am" not "09am"
        ts_9am = et_midnight_ts("2026-04-17") + 9 * 3600
        slug = slug_from_ts(ts_9am, "BTC", "1h")
        assert "09" not in slug
        assert "9am" in slug


class TestGenerateRangeTimestamps:

    def test_5m_single_day_count(self):
        # One day of 5m windows = 86400 / 300 = 288
        ts = generate_range_timestamps("2026-04-17", "2026-04-17", "5m")
        assert len(ts) == 288

    def test_15m_single_day_count(self):
        ts = generate_range_timestamps("2026-04-17", "2026-04-17", "15m")
        assert len(ts) == 96

    def test_1h_single_day_count(self):
        ts = generate_range_timestamps("2026-04-17", "2026-04-17", "1h")
        assert len(ts) == 24

    def test_4h_single_day_count(self):
        ts = generate_range_timestamps("2026-04-17", "2026-04-17", "4h")
        assert len(ts) == 6

    def test_1d_single_day_count(self):
        ts = generate_range_timestamps("2026-04-17", "2026-04-17", "1d")
        assert len(ts) == 1

    def test_5m_multi_day_count(self):
        ts = generate_range_timestamps("2026-04-17", "2026-04-18", "5m")
        assert len(ts) == 288 * 2

    def test_5m_sorted(self):
        ts = generate_range_timestamps("2026-04-17", "2026-04-17", "5m")
        assert ts == sorted(ts)

    def test_5m_unique(self):
        ts = generate_range_timestamps("2026-04-17", "2026-04-17", "5m")
        assert len(ts) == len(set(ts))

    def test_5m_aligned_to_et_midnight(self):
        ts = generate_range_timestamps("2026-04-17", "2026-04-17", "5m")
        midnight = et_midnight_ts("2026-04-17")
        assert ts[0] == midnight

    def test_4h_aligned_to_utc_epoch(self):
        ts = generate_range_timestamps("2026-04-17", "2026-04-17", "4h")
        for t in ts:
            assert t % 14400 == 0, f"{t} not divisible by 14400"

    def test_1h_aligned_to_et_hours(self):
        ts = generate_range_timestamps("2026-04-17", "2026-04-17", "1h")
        midnight = et_midnight_ts("2026-04-17")
        for i, t in enumerate(ts):
            assert t == midnight + i * 3600

    def test_5m_spacing(self):
        ts = generate_range_timestamps("2026-04-17", "2026-04-17", "5m")
        diffs = set(ts[i+1] - ts[i] for i in range(len(ts)-1))
        assert diffs == {300}
