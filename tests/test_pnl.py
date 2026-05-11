"""Tests for calc_net_pnl fee math."""
import pytest
from polymarket_core import calc_net_pnl


class TestCalcNetPnl:
    """
    Formula: exit_cents * 0.99 - entry_cents * 1.082
    Polymarket 7.2% taker + Polygon 1% on buy and sell.
    """

    def test_win_at_50_entry(self):
        result = calc_net_pnl(50.0, 100.0)
        expected = 100.0 * 0.99 - 50.0 * 1.082
        assert abs(result - expected) < 1e-9

    def test_win_at_50_is_positive(self):
        assert calc_net_pnl(50.0, 100.0) > 0

    def test_loss_at_50_entry(self):
        result = calc_net_pnl(50.0, 0.0)
        expected = 0.0 * 0.99 - 50.0 * 1.082
        assert abs(result - expected) < 1e-9

    def test_loss_at_50_is_negative(self):
        assert calc_net_pnl(50.0, 0.0) < 0

    def test_win_exact_value(self):
        # 100*0.99 - 50*1.082 = 99 - 54.1 = 44.9
        assert abs(calc_net_pnl(50.0, 100.0) - 44.9) < 1e-9

    def test_loss_exact_value(self):
        # 0 - 50*1.082 = -54.1
        assert abs(calc_net_pnl(50.0, 0.0) - (-54.1)) < 1e-9

    def test_zero_entry_zero_exit(self):
        assert calc_net_pnl(0.0, 0.0) == 0.0

    def test_breakeven_entry(self):
        # Solve: exit*0.99 = entry*1.082 → entry = 100*0.99/1.082 ≈ 91.497...
        # So buying at ~91.5c and winning (exit=100c) breaks even.
        breakeven = 100.0 * 0.99 / 1.082
        result = calc_net_pnl(breakeven, 100.0)
        assert abs(result) < 1e-6

    def test_buying_cheap_wins_big(self):
        # Entry 20c, exit 100c
        result = calc_net_pnl(20.0, 100.0)
        assert result > 0
        assert abs(result - (100.0 * 0.99 - 20.0 * 1.082)) < 1e-9

    def test_buying_expensive_win_still_positive(self):
        # Entry 85c, exit 100c — should still be profitable
        result = calc_net_pnl(85.0, 100.0)
        assert result > 0

    def test_buying_too_expensive_win_negative(self):
        # Entry 95c, exit 100c — over breakeven, net loss despite win
        result = calc_net_pnl(95.0, 100.0)
        assert result < 0

    def test_partial_exit(self):
        # Selling at 70c (not 100c)
        result = calc_net_pnl(20.0, 70.0)
        expected = 70.0 * 0.99 - 20.0 * 1.082
        assert abs(result - expected) < 1e-9

    @pytest.mark.parametrize("entry,exit_", [
        (1.0, 100.0),
        (10.0, 100.0),
        (30.0, 100.0),
        (50.0, 100.0),
        (70.0, 100.0),
    ])
    def test_win_always_uses_formula(self, entry, exit_):
        expected = exit_ * 0.99 - entry * 1.082
        assert abs(calc_net_pnl(entry, exit_) - expected) < 1e-9
