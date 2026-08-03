"""
test_position.py — Position lifecycle tests including
stop, gap, target, trailing stop, EOD flatten, and max hold.
"""
import pytest
from datetime import datetime
from src.backtest.position import Position
from src.execution.costs import NSECostModel
import src.config as config


class TestStopLoss:
    """Basic stop-loss fill tests."""

    def test_stop_hit(self):
        pos = Position("T", "A.NS", "LONG", 100.0, 92.0, 120.0, 5.0, 100)
        pos.entry_time = datetime(2024, 1, 1, 10, 0)
        # Bar not hitting stop
        pos.update({'Open': 100.5, 'High': 102.0, 'Low': 98.0, 'Close': 101.0}, datetime(2024, 1, 1, 10, 5))
        assert pos.open
        # Bar hitting stop
        pos.update({'Open': 97.0, 'High': 97.5, 'Low': 91.0, 'Close': 95.0}, datetime(2024, 1, 1, 10, 10))
        assert not pos.open
        assert pos.exit_reason == "STOP"
        assert pos.exit_price == 92.0

    def test_gap_stop_fills_at_open(self):
        pos = Position("T", "B.NS", "LONG", 100.0, 95.0, 115.0, 5.0, 50)
        pos.entry_time = datetime(2024, 1, 1, 10, 0)
        pos.update({'Open': 90.0, 'High': 93.0, 'Low': 88.0, 'Close': 91.0}, datetime(2024, 1, 2, 9, 15))
        assert pos.exit_reason == "STOP_GAP"
        assert pos.exit_price == 90.0

    def test_short_stop_hit(self):
        pos = Position("T", "C.NS", "SHORT", 500.0, 515.0, 470.0, 10.0, 50)
        pos.entry_time = datetime(2024, 1, 1, 10, 0)
        pos.update({'Open': 510.0, 'High': 516.0, 'Low': 508.0, 'Close': 514.0}, datetime(2024, 1, 1, 10, 5))
        assert not pos.open
        assert pos.exit_reason == "STOP"


class TestTarget:
    """Target fill tests."""

    def test_target_hit(self):
        pos = Position("T", "D.NS", "LONG", 100.0, 92.0, 120.0, 5.0, 100)
        pos.entry_time = datetime(2024, 1, 1, 10, 0)
        pos.update({'Open': 101.0, 'High': 121.0, 'Low': 100.0, 'Close': 120.0}, datetime(2024, 1, 1, 14, 0))
        assert pos.exit_reason == "TARGET"
        assert pos.exit_price == 120.0

    def test_short_target_hit(self):
        pos = Position("T", "E.NS", "SHORT", 500.0, 515.0, 470.0, 10.0, 50)
        pos.entry_time = datetime(2024, 1, 1, 10, 0)
        pos.update({'Open': 498.0, 'High': 502.0, 'Low': 469.0, 'Close': 470.0}, datetime(2024, 1, 1, 11, 0))
        assert pos.exit_reason == "TARGET"
        assert pos.exit_price == 470.0


class TestTrailingStop:
    """V6.5: Trailing stop lifecycle tests."""

    def test_trailing_activates_at_2r(self):
        pos = Position("T", "F.NS", "LONG", 100.0, 90.0, 130.0, 5.0, 100)
        pos.entry_time = datetime(2024, 1, 1, 10, 0)
        # Below activation threshold
        pos.update({'Open': 105.0, 'High': 107.5, 'Low': 104.0, 'Close': 106.0}, datetime(2024, 1, 1, 10, 5))
        assert not pos.trailing_activated
        assert pos.stop == 90.0
        # At +2R: activation
        pos.update({'Open': 110.0, 'High': 111.0, 'Low': 109.0, 'Close': 110.0}, datetime(2024, 1, 1, 10, 10))
        assert pos.trailing_activated
        assert pos.stop == 105.0  # Locked at entry + 1R

    def test_trailing_stop_triggers_exit(self):
        pos = Position("T", "G.NS", "LONG", 100.0, 90.0, 130.0, 5.0, 100)
        pos.entry_time = datetime(2024, 1, 1, 10, 0)
        pos.update({'Open': 110.0, 'High': 111.0, 'Low': 109.0, 'Close': 110.0}, datetime(2024, 1, 1, 10, 5))
        assert pos.trailing_activated
        # Open above trailing stop, low hits it
        pos.update({'Open': 106.0, 'High': 107.0, 'Low': 104.5, 'Close': 105.5}, datetime(2024, 1, 1, 10, 10))
        assert not pos.open
        assert pos.exit_reason == "STOP"
        assert pos.exit_price == 105.0

    def test_trailing_short(self):
        pos = Position("T", "H.NS", "SHORT", 500.0, 515.0, 470.0, 10.0, 50)
        pos.entry_time = datetime(2024, 1, 1, 10, 0)
        pos.update({'Open': 478.0, 'High': 480.0, 'Low': 475.0, 'Close': 478.0}, datetime(2024, 1, 1, 10, 5))
        assert pos.trailing_activated
        assert pos.stop == 490.0  # entry - 1R


class TestEODFlatten:
    """V6.5: End-of-Day flatten tests."""

    def test_flatten_at_1515(self):
        pos = Position("T", "I.NS", "LONG", 1500.0, 1480.0, 1560.0, 10.0, 50)
        pos.entry_time = datetime(2024, 1, 1, 10, 0)
        pos.update({'Open': 1510.0, 'High': 1515.0, 'Low': 1505.0, 'Close': 1512.0}, datetime(2024, 1, 1, 14, 0))
        assert pos.open
        pos.update({'Open': 1520.0, 'High': 1525.0, 'Low': 1515.0, 'Close': 1522.0}, datetime(2024, 1, 1, 15, 0))
        assert pos.open
        pos.update({'Open': 1518.0, 'High': 1522.0, 'Low': 1516.0, 'Close': 1520.0}, datetime(2024, 1, 1, 15, 15))
        assert pos.exit_reason == "EOD_FLATTEN"

    def test_no_flatten_before_time(self):
        pos = Position("T", "J.NS", "LONG", 100.0, 92.0, 120.0, 5.0, 100)
        pos.entry_time = datetime(2024, 1, 1, 10, 0)
        pos.update({'Open': 101.0, 'High': 103.0, 'Low': 99.0, 'Close': 102.0}, datetime(2024, 1, 1, 15, 10))
        assert pos.open, "Should NOT flatten before 15:15"


class TestMaxHold:
    """V6.5: Max holding period tests."""

    def test_exits_at_max_bars(self):
        pos = Position("T", "K.NS", "LONG", 1000.0, 980.0, 1060.0, 10.0, 50)
        pos.entry_time = datetime(2024, 1, 1, 9, 15)
        for i in range(config.MAX_HOLD_BARS - 1):
            pos.update({'Open': 1001.0, 'High': 1005.0, 'Low': 998.0, 'Close': 1002.0, 'EMA_20': 900}, datetime(2024, 1, 1, 10, i))
        assert pos.open
        pos.update({'Open': 1002.0, 'High': 1004.0, 'Low': 999.0, 'Close': 1003.0}, datetime(2024, 1, 1, 13, 0))
        assert pos.exit_reason == "MAX_HOLD"


class TestNetR:
    """Net R and PnL with transaction costs."""

    def test_net_r_less_than_gross(self):
        cost_model = NSECostModel()
        pos = Position("T", "L.NS", "LONG", 100.0, 92.0, 120.0, 5.0, 100,
                        features={'vol_ma_20': 1_000_000})
        pos.entry_time = datetime(2024, 1, 1, 10, 0)
        pos.update({'Open': 101.0, 'High': 121.0, 'Low': 100.0, 'Close': 120.0}, datetime(2024, 1, 1, 14, 0))
        net_r = pos.calculate_net_r(cost_model)
        gross_r = (120.0 - 100.0) / (100.0 - 92.0)  # 2.5
        assert 0 < net_r < gross_r

    def test_initial_stop_preserved_after_trailing(self):
        """R-multiple should use initial_stop, not trailing-adjusted stop."""
        cost_model = NSECostModel()
        pos = Position("T", "M.NS", "LONG", 100.0, 90.0, 130.0, 5.0, 100,
                        features={'vol_ma_20': 1_000_000})
        pos.entry_time = datetime(2024, 1, 1, 10, 0)
        # Activate trailing (stop ratchets 90 -> 105)
        pos.update({'Open': 110.0, 'High': 111.0, 'Low': 109.0, 'Close': 110.0}, datetime(2024, 1, 1, 10, 5))
        assert pos.stop == 105.0
        assert pos.initial_stop == 90.0, "initial_stop should be preserved"
        # Hit target
        pos.update({'Open': 128.0, 'High': 131.0, 'Low': 127.0, 'Close': 130.0}, datetime(2024, 1, 1, 10, 10))
        net_r = pos.calculate_net_r(cost_model)
        # R = pnl / (100 - 90)*100 = pnl / 1000
        assert net_r > 0
