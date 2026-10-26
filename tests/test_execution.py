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
    def test_short_cost_uses_sell_at_entry_and_buy_at_exit(self):
        model = NSECostModel()
        expected = (model.calculate_leg_cost(100, 100, 'SELL', adv=1_000_000)
                    + model.calculate_leg_cost(80, 100, 'BUY', adv=1_000_000))
        assert model.round_trip_cost(100, 80, 100, adv=1_000_000, direction='SHORT') == pytest.approx(expected)

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


@pytest.mark.parametrize('direction,levels', [('LONG', {'resistance': 50}), ('SHORT', {'support': 200})])
def test_wrong_side_targets_are_rejected(direction, levels):
    order = ExecutionModel().generate_orders({'close': 100.0, 'atr': 5.0, 'direction': direction, **levels})
    assert not order['valid_rr']
    assert order['shares'] == 0


@pytest.mark.parametrize('values', [{'atr': 0}, {'atr': -1}, {'close': float('nan')},
                                  {'atr': float('inf')}, {'direction': 'NEUTRAL'},
                                  {'portfolio_vol': float('nan')}, {'current_dd': -0.1}])
def test_invalid_sizing_inputs_do_not_open_orders(values):
    signal = {'close': 100.0, 'atr': 5.0, 'direction': 'LONG'}
    kwargs = {k: v for k, v in values.items() if k in {'portfolio_vol', 'current_dd'}}
    signal.update({k: v for k, v in values.items() if k not in kwargs})
    order = ExecutionModel().generate_orders(signal, **kwargs)
    assert not order['valid_rr']
    assert order['shares'] == 0


def test_share_count_respects_risk_at_rounded_execution_prices():
    model = ExecutionModel(base_risk_inr=1)
    order = model.generate_orders({'close': 1.0, 'atr': 0.011, 'direction': 'LONG', 'resistance': 10})
    risk = model.calculate_dynamic_risk(6, 0.12 / 252**0.5, 0)
    assert order['valid_rr']
    assert order['shares'] * (order['entry'] - order['stop']) <= risk
