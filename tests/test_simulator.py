"""Tests for simulate_strategy."""
import pytest
from polymarket_core import simulate_strategy, calc_net_pnl
from tests.conftest import make_market, TS_APR17_ET_MIDNIGHT


def _outcome_insight(side="UP", minute=1, threshold=100, asset="BTC"):
    return {
        "title": f"{asset} +${threshold} at min {minute} → BUY {side}",
        "confidence": 0.8,
        "support": 10,
        "avg_entry": 50.0,
        "avg_net_pnl": 10.0,
        "bottomline": "test",
        "samples": [],
    }


def _volatility_insight(side="UP", minute=1, entry_cap=20, asset="BTC"):
    return {
        "title": f"{side} ≤ {entry_cap}c at min {minute}, sell rally",
        "confidence": 0.7,
        "support": 10,
        "avg_entry": 15.0,
        "avg_net_pnl": 5.0,
        "bottomline": "test",
        "samples": [],
    }


def _make_outcome_markets(n=10, n_wins=7, btc_delta=150.0, side="UP"):
    """Markets where BTC delta > threshold and side wins n_wins/n times."""
    markets = []
    for i in range(n):
        ts = TS_APR17_ET_MIDNIGHT + i * 300
        win = i < n_wins
        if side == "UP":
            up_prices = [0.5, 0.9, 0.9, 0.9, 1.0] if win else [0.5, 0.4, 0.3, 0.1, 0.0]
            dn_prices = [0.5, 0.1, 0.1, 0.1, 0.0] if win else [0.5, 0.6, 0.7, 0.9, 1.0]
        markets.append(make_market(
            slug=f"s-{i}", ts=ts,
            up_prices=up_prices, down_prices=dn_prices,
            btc_deltas=[btc_delta] * 5,
        ))
    return markets


def _make_volatility_markets(n=10, n_profitable=7, side="UP", entry_c=15.0):
    """Markets where side starts cheap (entry_c) and rallies n_profitable/n times."""
    markets = []
    for i in range(n):
        ts = TS_APR17_ET_MIDNIGHT + i * 300
        profitable = i < n_profitable
        if side == "UP":
            if profitable:
                up_prices = [entry_c / 100, 0.50, 0.80, 0.90, 0.90]
            else:
                up_prices = [entry_c / 100, 0.05, 0.03, 0.02, 0.01]
            dn_prices = [1.0 - entry_c / 100, 0.50, 0.20, 0.10, 0.10]
        markets.append(make_market(
            slug=f"v-{i}", ts=ts,
            up_prices=up_prices, down_prices=dn_prices,
        ))
    return markets


class TestSimulateOutcomeStrategy:

    def test_wins_losses_count(self):
        markets = _make_outcome_markets(n=10, n_wins=7, btc_delta=150.0)
        ins = _outcome_insight(side="UP", minute=1, threshold=100)
        result = simulate_strategy(markets, ins, bet_size=100)
        assert result["wins"] == 7
        assert result["losses"] == 3
        assert result["total_trades"] == 10

    def test_hit_rate(self):
        markets = _make_outcome_markets(n=10, n_wins=7)
        ins = _outcome_insight()
        result = simulate_strategy(markets, ins, bet_size=100)
        assert abs(result["hit_rate"] - 70.0) < 1e-9

    def test_bet_size_scales_total_pnl(self):
        markets = _make_outcome_markets(n=10, n_wins=7)
        ins = _outcome_insight()
        r100 = simulate_strategy(markets, ins, bet_size=100)
        r200 = simulate_strategy(markets, ins, bet_size=200)
        if abs(r100["total_pnl"]) > 1e-9:
            ratio = r200["total_pnl"] / r100["total_pnl"]
            assert abs(ratio - 2.0) < 1e-6

    def test_threshold_filter_skips_non_qualifying(self):
        # Markets where BTC delta < threshold → all skipped
        markets = _make_outcome_markets(n=10, n_wins=10, btc_delta=10.0)  # delta=10 < threshold=100
        ins = _outcome_insight(threshold=100)
        result = simulate_strategy(markets, ins, bet_size=100)
        assert result["total_trades"] == 0

    def test_all_losses_negative_pnl(self):
        markets = _make_outcome_markets(n=10, n_wins=0)
        ins = _outcome_insight()
        result = simulate_strategy(markets, ins, bet_size=100)
        assert result["total_pnl"] < 0
        assert result["wins"] == 0

    def test_all_wins_positive_pnl(self):
        markets = _make_outcome_markets(n=10, n_wins=10)
        ins = _outcome_insight()
        result = simulate_strategy(markets, ins, bet_size=100)
        assert result["total_pnl"] > 0
        assert result["losses"] == 0

    def test_max_drawdown_nonnegative(self):
        markets = _make_outcome_markets(n=20, n_wins=7)
        ins = _outcome_insight()
        result = simulate_strategy(markets, ins, bet_size=100)
        assert result["max_drawdown"] >= 0

    def test_worst_loss_nonpositive(self):
        markets = _make_outcome_markets(n=10, n_wins=5)
        ins = _outcome_insight()
        result = simulate_strategy(markets, ins, bet_size=100)
        assert result["worst_loss"] <= 0

    def test_no_trades_returns_zero_stats(self):
        markets = _make_outcome_markets(n=10, n_wins=5, btc_delta=10.0)
        ins = _outcome_insight(threshold=100)
        result = simulate_strategy(markets, ins, bet_size=100)
        assert result["total_trades"] == 0
        assert result["hit_rate"] == 0
        assert result["total_pnl"] == 0.0

    def test_result_has_required_keys(self):
        markets = _make_outcome_markets(n=10, n_wins=7)
        ins = _outcome_insight()
        result = simulate_strategy(markets, ins, bet_size=100)
        for key in ("total_trades", "wins", "losses", "hit_rate",
                    "total_pnl", "total_pnl_pct", "max_drawdown",
                    "worst_loss", "avg_pnl", "final_bankroll"):
            assert key in result, f"Missing key: {key}"


class TestSimulateVolatilityStrategy:

    def test_wins_losses_count(self):
        markets = _make_volatility_markets(n=10, n_profitable=6)
        ins = _volatility_insight(side="UP", minute=1, entry_cap=20)
        result = simulate_strategy(markets, ins, bet_size=100)
        assert result["wins"] == 6
        assert result["losses"] == 4

    def test_entry_cap_filter(self):
        # All markets have UP starting at 50c — above 20c cap → all skipped
        markets = [make_market(
            slug=f"s-{i}", ts=TS_APR17_ET_MIDNIGHT + i * 300,
            up_prices=[0.50, 0.60, 0.70, 0.80, 0.90],
            down_prices=[0.50, 0.40, 0.30, 0.20, 0.10],
        ) for i in range(10)]
        ins = _volatility_insight(entry_cap=20)
        result = simulate_strategy(markets, ins, bet_size=100)
        assert result["total_trades"] == 0

    def test_bet_size_doubles_pnl(self):
        markets = _make_volatility_markets(n=10, n_profitable=8)
        ins = _volatility_insight()
        r1 = simulate_strategy(markets, ins, bet_size=50)
        r2 = simulate_strategy(markets, ins, bet_size=100)
        if abs(r1["total_pnl"]) > 1e-9:
            assert abs(r2["total_pnl"] / r1["total_pnl"] - 2.0) < 1e-6
