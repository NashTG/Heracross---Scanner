"""Tests for analyze() using synthetic datasets."""
import pytest
from polymarket_core import analyze, closing_outcome
from tests.conftest import make_market, synthetic_dataset, TS_APR17_ET_MIDNIGHT


def _make_outcome_markets(n_total=30, n_wins=24, btc_delta_min1=100.0, side="UP"):
    """
    Build markets where BTC is +btc_delta_min1 at minute 1 and side wins n_wins times.
    Produces an outcome insight: '+$100 at min 1 → BUY UP' should surface at ≥70% conf.
    """
    markets = []
    for i in range(n_total):
        ts = TS_APR17_ET_MIDNIGHT + i * 300
        slug = f"btc-updown-5m-{ts}"
        win = i < n_wins
        if side == "UP":
            # UP wins: up closes at 1.0, down closes at 0.0
            up_prices = [0.5, 0.7, 0.9, 1.0, 1.0] if win else [0.5, 0.4, 0.3, 0.1, 0.0]
            dn_prices = [0.5, 0.3, 0.1, 0.0, 0.0] if win else [0.5, 0.6, 0.7, 0.9, 1.0]
        else:
            up_prices = [0.5, 0.4, 0.3, 0.1, 0.0] if win else [0.5, 0.7, 0.9, 1.0, 1.0]
            dn_prices = [0.5, 0.6, 0.7, 0.9, 1.0] if win else [0.5, 0.3, 0.1, 0.0, 0.0]
        # BTC delta at all snapshots: delta_min1 from the start
        btc_deltas = [btc_delta_min1] * 5
        markets.append(make_market(
            slug=slug, ts=ts,
            up_prices=up_prices, down_prices=dn_prices, btc_deltas=btc_deltas,
        ))
    return markets


def _make_volatility_markets(n_total=30, n_profitable=24, side="UP", entry_cap_c=20.0):
    """Build markets where the side starts cheap and often rallies."""
    markets = []
    for i in range(n_total):
        ts = TS_APR17_ET_MIDNIGHT + i * 300
        slug = f"btc-updown-5m-{ts}"
        profitable = i < n_profitable
        # entry at minute 1 (index 0) = 15c, rallies to 70c or stays low
        if side == "UP":
            if profitable:
                up_prices = [0.15, 0.30, 0.50, 0.70, 0.70]
            else:
                up_prices = [0.15, 0.10, 0.05, 0.05, 0.05]
            dn_prices = [0.85, 0.70, 0.50, 0.30, 0.30]
        markets.append(make_market(
            slug=slug, ts=ts, up_prices=up_prices, down_prices=dn_prices,
        ))
    return markets


class TestAnalyze:

    def test_returns_none_when_no_usable(self):
        markets = [make_market(error="timeout")]
        assert analyze(markets) is None

    def test_counts_up_and_down_closes(self):
        # 20 UP closes, 10 DOWN closes
        up_wins = [make_market(
            slug=f"s-{i}", ts=TS_APR17_ET_MIDNIGHT + i * 300,
            up_prices=[0.9], down_prices=[0.1],
        ) for i in range(20)]
        dn_wins = [make_market(
            slug=f"d-{i}", ts=TS_APR17_ET_MIDNIGHT + (i + 20) * 300,
            up_prices=[0.1], down_prices=[0.9],
        ) for i in range(10)]
        result = analyze(up_wins + dn_wins)
        assert result["up_closes"] == 20
        assert result["down_closes"] == 10

    def test_up_pct_correct(self):
        up_wins = [make_market(slug=f"u-{i}", ts=TS_APR17_ET_MIDNIGHT + i * 300,
                               up_prices=[0.9], down_prices=[0.1]) for i in range(7)]
        dn_wins = [make_market(slug=f"d-{i}", ts=TS_APR17_ET_MIDNIGHT + (i+7) * 300,
                               up_prices=[0.1], down_prices=[0.9]) for i in range(3)]
        result = analyze(up_wins + dn_wins)
        assert abs(result["up_pct"] - 70.0) < 1e-9

    def test_tod_segments_always_six(self):
        markets = synthetic_dataset(n=48)
        result = analyze(markets)
        assert len(result["tod_segments"]) == 6

    def test_tod_segments_cover_24h(self):
        markets = synthetic_dataset(n=48)
        result = analyze(markets)
        ranges = [s["range"] for s in result["tod_segments"]]
        assert "00:00–04:00 ET" in ranges
        assert "20:00–24:00 ET" in ranges

    def test_tod_segment_counts_sum_to_usable(self):
        markets = synthetic_dataset(n=48)
        result = analyze(markets)
        total_in_segments = sum(s["markets"] for s in result["tod_segments"])
        assert total_in_segments == result["usable"]

    def test_outcome_insight_surfaces_for_planted_pattern(self):
        # 30 markets, BTC +$100 at min 1, UP wins 24/30 = 80% → insight must appear
        markets = _make_outcome_markets(n_total=30, n_wins=24, btc_delta_min1=100.0)
        result = analyze(markets)
        assert len(result["outcome_insights"]) > 0
        titles = [i["title"] for i in result["outcome_insights"]]
        assert any("+$100" in t and "UP" in t for t in titles)

    def test_outcome_insight_not_below_70pct(self):
        # 30 markets, UP wins only 60% → no insight should surface
        markets = _make_outcome_markets(n_total=30, n_wins=18, btc_delta_min1=100.0)
        result = analyze(markets)
        # With only 60% confidence, no outcome insight should appear
        for ins in result["outcome_insights"]:
            assert ins["confidence"] >= 0.7

    def test_profit_insight_surfaces_for_cheap_entry_rally(self):
        # 30 markets, UP starts at 15c, rallies 80% of the time
        markets = _make_volatility_markets(n_total=30, n_profitable=24, side="UP", entry_cap_c=20.0)
        result = analyze(markets)
        assert len(result["profit_insights"]) > 0

    def test_outcome_insights_sorted_by_confidence_desc(self):
        markets = _make_outcome_markets(n_total=30, n_wins=24)
        result = analyze(markets)
        confs = [i["confidence"] for i in result["outcome_insights"]]
        assert confs == sorted(confs, reverse=True)

    def test_insights_capped_at_eight(self):
        # Large dataset — results should be capped at 8
        markets = _make_outcome_markets(n_total=50, n_wins=45)
        result = analyze(markets)
        assert len(result["outcome_insights"]) <= 8
        assert len(result["profit_insights"]) <= 8

    def test_no_usable_insights_from_empty_pattern(self):
        # 20 markets with ~50% BTC deltas — no reliable threshold triggers
        markets = []
        for i in range(20):
            ts = TS_APR17_ET_MIDNIGHT + i * 300
            # alternating UP/DOWN with small BTC delta (well below any threshold)
            up_prices = [0.9] if i % 2 == 0 else [0.1]
            dn_prices = [0.1] if i % 2 == 0 else [0.9]
            btc_deltas = [5.0] * 5  # below 25 threshold
            markets.append(make_market(
                slug=f"s-{i}", ts=ts,
                up_prices=up_prices, down_prices=dn_prices, btc_deltas=btc_deltas,
            ))
        result = analyze(markets)
        # No outcome insight should surface (delta < 25 threshold everywhere)
        assert result is not None
        assert len(result["outcome_insights"]) == 0

    def test_correlation_computed_with_sufficient_data(self):
        # Correlation requires stdev(x) > 0 AND stdev(y) > 0 at minute 1.
        # Vary both BTC delta (x) and UP price (y) at index 0.
        markets = []
        for i in range(20):
            ts = TS_APR17_ET_MIDNIGHT + i * 300
            delta = 100.0 if i % 2 == 0 else -100.0
            # High delta → high up price; low delta → low up price
            up0 = 0.8 if i % 2 == 0 else 0.2
            dn0 = 1.0 - up0
            markets.append(make_market(
                slug=f"c-{i}", ts=ts,
                up_prices=[up0, up0, up0, up0, up0],
                down_prices=[dn0, dn0, dn0, dn0, dn0],
                btc_deltas=[delta, delta, delta, delta, delta],
            ))
        result = analyze(markets)
        assert result["correlation"] is not None

    def test_correlation_none_with_insufficient_data(self):
        # Only 3 markets → fewer than 5 pairs → correlation should be None
        markets = synthetic_dataset(n=3)
        result = analyze(markets)
        assert result["correlation"] is None

    def test_result_has_required_keys(self):
        markets = synthetic_dataset(n=20)
        result = analyze(markets)
        for key in ("markets", "usable", "up_closes", "down_closes", "up_pct",
                    "avg_abs_move", "avg_up_range", "avg_down_range",
                    "correlation", "tod_segments", "outcome_insights", "profit_insights"):
            assert key in result, f"Missing key: {key}"
