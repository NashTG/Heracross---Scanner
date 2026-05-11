"""Tests for closing_outcome, _get_usable, _range_cents."""
import pytest
from polymarket_core import closing_outcome
from polymarket_core import _get_usable, _range_cents
from tests.conftest import make_market, make_snapshot_series


class TestClosingOutcome:

    def test_up_wins(self):
        m = make_market(up_prices=[0.4, 0.6, 0.8], down_prices=[0.6, 0.4, 0.2])
        assert closing_outcome(m) == "UP"

    def test_down_wins(self):
        m = make_market(up_prices=[0.6, 0.4, 0.2], down_prices=[0.4, 0.6, 0.8])
        assert closing_outcome(m) == "DOWN"

    def test_tied_returns_down(self):
        # up[-1]["p"] == down[-1]["p"] → neither is strictly greater → DOWN
        m = make_market(up_prices=[0.5], down_prices=[0.5])
        assert closing_outcome(m) == "DOWN"

    def test_missing_up_returns_none(self):
        m = make_market()
        m["up_snapshots"] = []
        assert closing_outcome(m) is None

    def test_missing_down_returns_none(self):
        m = make_market()
        m["down_snapshots"] = []
        assert closing_outcome(m) is None

    def test_both_missing_returns_none(self):
        m = make_market()
        m["up_snapshots"] = []
        m["down_snapshots"] = []
        assert closing_outcome(m) is None

    def test_single_snapshot_up(self):
        m = make_market(up_prices=[0.9], down_prices=[0.1])
        assert closing_outcome(m) == "UP"

    def test_single_snapshot_down(self):
        m = make_market(up_prices=[0.1], down_prices=[0.9])
        assert closing_outcome(m) == "DOWN"


class TestGetUsable:

    def test_good_market_included(self):
        m = make_market()
        result = _get_usable([m])
        assert len(result) == 1

    def test_errored_market_excluded(self):
        m = make_market(error="No Gamma event")
        result = _get_usable([m])
        assert len(result) == 0

    def test_empty_up_snapshots_excluded(self):
        m = make_market()
        m["up_snapshots"] = []
        result = _get_usable([m])
        assert len(result) == 0

    def test_empty_down_snapshots_excluded(self):
        m = make_market()
        m["down_snapshots"] = []
        result = _get_usable([m])
        assert len(result) == 0

    def test_no_closing_outcome_excluded(self):
        m = make_market()
        m["up_snapshots"] = []
        m["down_snapshots"] = []
        result = _get_usable([m])
        assert len(result) == 0

    def test_mixed_list_filters_correctly(self):
        good = make_market()
        bad1 = make_market(error="timeout")
        bad2 = make_market()
        bad2["up_snapshots"] = []
        result = _get_usable([good, bad1, bad2])
        assert len(result) == 1
        assert result[0] is good

    def test_empty_input(self):
        assert _get_usable([]) == []


class TestRangeCents:

    def test_empty_returns_zero(self):
        assert _range_cents([]) == 0

    def test_single_point_returns_zero(self):
        assert _range_cents([{"p": 0.5, "t": 0}]) == 0

    def test_two_points_spread(self):
        points = [{"p": 0.3, "t": 0}, {"p": 0.7, "t": 1}]
        assert abs(_range_cents(points) - 40.0) < 1e-9

    def test_max_minus_min(self):
        # Range is max - min regardless of order
        points = [
            {"p": 0.5, "t": 0},
            {"p": 0.9, "t": 1},
            {"p": 0.1, "t": 2},
            {"p": 0.6, "t": 3},
        ]
        # max = 0.9*100=90, min = 0.1*100=10, range = 80
        assert abs(_range_cents(points) - 80.0) < 1e-9

    def test_all_same_price_zero_range(self):
        points = [{"p": 0.5, "t": i} for i in range(5)]
        assert _range_cents(points) == 0
