"""
test_execution.py — ExecutionModel and NSECostModel tests.
"""
import pytest
from src.execution.model import ExecutionModel
from src.execution.costs import NSECostModel


class TestExecutionModel:
    """Tests for signal-to-order conversion and Risk/Reward validation."""

    def test_rr_veto_rejects_poor_trades(self):
        model = ExecutionModel()
        signal = {
            'close': 100.0, 'atr': 5.0, 'direction': 'LONG',
            'swing_high': 102.0, 'swing_low': 92.0,
            'resistance': 103.0, 'support': 85.0
        }
        orders = model.generate_orders(signal)
        assert not orders['valid_rr']

    def test_valid_trade_passes_rr(self):
        model = ExecutionModel()
        signal = {
            'close': 100.0, 'atr': 5.0, 'direction': 'LONG',
            'swing_high': 105.0, 'swing_low': 93.0,
            'resistance': 130.0, 'support': 85.0
        }
        orders = model.generate_orders(signal)
        assert orders['valid_rr']
        assert orders['shares'] > 0
        assert orders['entry'] > 0
        assert orders['stop'] < orders['entry']
        assert orders['target'] > orders['entry']

    def test_short_order_structure(self):
        model = ExecutionModel()
        signal = {
            'close': 500.0, 'atr': 10.0, 'direction': 'SHORT',
            'swing_high': 510.0, 'swing_low': 490.0,
            'resistance': 520.0, 'support': 460.0
        }
        orders = model.generate_orders(signal)
        assert orders['stop'] > orders['entry']
        assert orders['target'] < orders['entry']


class TestNSECostModel:
    """Tests for NSE transaction cost calculations."""

    def test_stt_sell_side_only(self):
        model = NSECostModel(instrument_type="EQUITY_INTRADAY")
        buy = model.calculate_leg_cost(100.0, 100, "BUY", adv=5_000_000)
        sell = model.calculate_leg_cost(100.0, 100, "SELL", adv=5_000_000)
        assert sell > buy, "STT applies only on sell for intraday"

    def test_round_trip_positive(self):
        model = NSECostModel()
        cost = model.round_trip_cost(100.0, 110.0, 100, adv=1_000_000)
        assert cost > 0, "Round-trip cost must be positive"
