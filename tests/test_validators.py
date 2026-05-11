"""Tests for validate_date_range and validate_import_payload."""
import pytest
from polymarket_core import validate_date_range, validate_import_payload


class TestValidateDateRange:

    def test_valid_range_returns_tuple(self):
        result = validate_date_range("2026-04-17", "2026-04-18")
        assert result == ("2026-04-17", "2026-04-18")

    def test_same_start_and_end_ok(self):
        result = validate_date_range("2026-04-17", "2026-04-17")
        assert result == ("2026-04-17", "2026-04-17")

    def test_start_after_end_raises(self):
        with pytest.raises(ValueError, match="start_date must be on or before end_date"):
            validate_date_range("2026-04-18", "2026-04-17")

    def test_not_a_date_string_raises(self):
        with pytest.raises(ValueError, match="start_date must be YYYY-MM-DD"):
            validate_date_range("not-a-date", "2026-04-18")

    def test_wrong_format_raises(self):
        with pytest.raises(ValueError, match="start_date must be YYYY-MM-DD"):
            validate_date_range("17-04-2026", "18-04-2026")

    def test_invalid_month_raises(self):
        with pytest.raises(ValueError):
            validate_date_range("2026-13-01", "2026-13-02")

    def test_invalid_day_raises(self):
        with pytest.raises(ValueError):
            validate_date_range("2026-04-31", "2026-04-32")

    def test_none_raises(self):
        with pytest.raises((ValueError, AttributeError)):
            validate_date_range(None, "2026-04-18")

    def test_integer_raises(self):
        with pytest.raises((ValueError, AttributeError)):
            validate_date_range(20260417, "2026-04-18")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            validate_date_range("", "2026-04-18")

    def test_end_date_not_a_date_raises(self):
        with pytest.raises(ValueError, match="end_date must be YYYY-MM-DD"):
            validate_date_range("2026-04-17", "tomorrow")


class TestValidateImportPayload:

    def test_valid_minimal_list(self):
        data = [{"slug": "btc-updown-5m-12345"}]
        result = validate_import_payload(data)
        assert result == data

    def test_empty_list_ok(self):
        result = validate_import_payload([])
        assert result == []

    def test_full_market_dict_ok(self):
        data = [{
            "slug": "btc-updown-5m-12345",
            "up_snapshots": [{"p": 0.6, "t": 1}],
            "down_snapshots": [{"p": 0.4, "t": 1}],
            "btc_snapshots": [],
        }]
        result = validate_import_payload(data)
        assert result == data

    def test_non_list_raises(self):
        with pytest.raises(ValueError, match="must be a JSON array"):
            validate_import_payload({"slug": "abc"})

    def test_string_raises(self):
        with pytest.raises(ValueError, match="must be a JSON array"):
            validate_import_payload("not a list")

    def test_none_raises(self):
        with pytest.raises(ValueError, match="must be a JSON array"):
            validate_import_payload(None)

    def test_item_not_dict_raises(self):
        with pytest.raises(ValueError, match="Item 0 is not an object"):
            validate_import_payload(["just a string"])

    def test_missing_slug_raises(self):
        with pytest.raises(ValueError, match="Item 0 is missing a non-empty 'slug'"):
            validate_import_payload([{"up_snapshots": []}])

    def test_empty_slug_raises(self):
        with pytest.raises(ValueError, match="Item 0 is missing a non-empty 'slug'"):
            validate_import_payload([{"slug": ""}])

    def test_non_string_slug_raises(self):
        with pytest.raises(ValueError, match="Item 0 is missing a non-empty 'slug'"):
            validate_import_payload([{"slug": 12345}])

    def test_snapshots_not_list_raises(self):
        with pytest.raises(ValueError, match="'up_snapshots' must be a list"):
            validate_import_payload([{"slug": "abc", "up_snapshots": "not-a-list"}])

    def test_second_item_invalid_raises(self):
        data = [{"slug": "valid"}, {"no_slug": True}]
        with pytest.raises(ValueError, match="Item 1"):
            validate_import_payload(data)

    def test_returns_original_list(self):
        data = [{"slug": "s1"}, {"slug": "s2"}]
        result = validate_import_payload(data)
        assert result is data
