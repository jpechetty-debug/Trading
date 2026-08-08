"""
Unit Tests for Core Trading Logic V7.0.
Tests Kill Score, Position lifecycle (stop, gap, target, trailing, EOD, max hold),
Execution model, Cost model, and Correlation filter.

NOTE: These tests were originally written for V5 Position.update(open, high, low, ts)
and have been updated to use the V7.0 bar-dict API: Position.update(bar, ts).
"""
from datetime import datetime

from src.execution.model import ExecutionModel
from src.execution.costs import NSECostModel
from src.backtest.position import Position
from src.backtest.constraints import PortfolioConstraints


def _bar(open_p, high, low, close=None, atr=5.0, ema_20=None, rs=0.5):
    """Helper: build a bar dict compatible with Position.update()."""
    if close is None:
        close = (high + low) / 2
    return {
        'Open': open_p, 'High': high, 'Low': low, 'Close': close,
        'ATR': atr, 'EMA_20': ema_20 or close, 'RS_Score': rs
    }


# === EXECUTION MODEL TESTS ===

def test_execution_model_rr_veto():
    """Trades with RR < 2.0 must be vetoed."""
    model = ExecutionModel()
    signal = {
        'close': 100.0, 'atr': 5.0, 'direction': 'LONG',
        'swing_high': 102.0, 'swing_low': 92.0,
        'resistance': 103.0, 'support': 85.0
    }
    orders = model.generate_orders(signal)
    assert not orders['valid_rr'], f"Expected RR veto but got valid_rr=True (RR={orders['risk_reward']})"


def test_execution_model_valid_trade():
    """Trades with good structure should pass RR check."""
    model = ExecutionModel()
    signal = {
        'close': 100.0, 'atr': 5.0, 'direction': 'LONG',
        'swing_high': 105.0, 'swing_low': 93.0,
        'resistance': 130.0, 'support': 85.0
    }
    orders = model.generate_orders(signal)
    assert orders['valid_rr'], f"Expected valid trade but got RR={orders['risk_reward']}"
    assert orders['shares'] > 0, "Shares must be > 0"


# === POSITION LIFECYCLE TESTS ===

def test_position_stop_hit():
    """Position must close when price hits stop."""
    pos = Position("TEST_001", "RELIANCE.NS", "LONG", 100.0, 92.0, 120.0, 5.0, 100, features={'atr_val': 5})
    pos.entry_time = datetime(2024, 1, 1, 10, 0)

    # Bar above stop — should stay open
    pos.update(_bar(100.5, 102.0, 98.0, close=101.0), datetime(2024, 1, 1, 10, 5))
    assert pos.open, "Position should still be open"

    # Bar breaches stop (low <= 92.0)
    pos.update(_bar(97.0, 97.5, 91.0, close=91.5), datetime(2024, 1, 1, 10, 10))
    assert not pos.open, "Position should be closed (stop hit)"
    assert pos.exit_reason == "STOP"
    assert pos.exit_price == 92.0


def test_position_gap_stop():
    """Gap-down below stop must fill at open price (worse fill)."""
    pos = Position("TEST_002", "TCS.NS", "LONG", 100.0, 95.0, 115.0, 5.0, 50, features={'atr_val': 5})
    pos.entry_time = datetime(2024, 1, 1, 10, 0)

    # Open gaps below stop
    pos.update(_bar(90.0, 93.0, 88.0, close=91.0), datetime(2024, 1, 2, 9, 15))
    assert not pos.open, "Position should be closed (gap stop)"
    assert pos.exit_reason == "STOP_GAP"
    assert pos.exit_price == 90.0, f"Gap stop should fill at open, got {pos.exit_price}"


def test_position_target_hit():
    """Position must close when price hits target."""
    pos = Position("TEST_003", "INFY.NS", "LONG", 100.0, 92.0, 120.0, 5.0, 100, features={'atr_val': 5})
    pos.entry_time = datetime(2024, 1, 1, 10, 0)

    pos.update(_bar(101.0, 121.0, 100.0, close=118.0), datetime(2024, 1, 1, 14, 0))
    assert not pos.open, "Position should be closed (target hit)"
    assert pos.exit_reason == "TARGET"
    assert pos.exit_price == 120.0


# === TRAILING STOP TESTS ===

def test_trailing_stop_activation():
    """Trailing stop activates at +2R and locks 1R profit."""
    pos = Position("TEST_TSL", "WIPRO.NS", "LONG", 100.0, 90.0, 130.0, 5.0, 100, features={'atr_val': 5})
    pos.entry_time = datetime(2024, 1, 1, 10, 0)

    # Price at +1.5R — trailing should NOT activate yet
    pos.update(_bar(105.0, 107.5, 104.0, close=106.0), datetime(2024, 1, 1, 10, 5))
    assert pos.open, "Should still be open at +1.5R"
    assert not pos.trailing_activated, "Trailing should not activate below +2R"
    assert pos.stop == 90.0, f"Stop should remain at 90, got {pos.stop}"

    # Price pushes to +2.2R — trailing should activate
    pos.update(_bar(110.0, 111.0, 109.0, close=110.0), datetime(2024, 1, 1, 10, 10))
    assert pos.trailing_activated, "Trailing should activate at +2R"
    assert pos.stop == 105.0, f"Stop should ratchet to entry+1R=105, got {pos.stop}"

    # Price falls back — low touches trailing stop, should exit as STOP
    pos.update(_bar(106.0, 106.0, 104.5, close=104.5), datetime(2024, 1, 1, 10, 15))
    assert not pos.open, "Should close at trailing stop"
    assert pos.exit_reason == "STOP"
    assert pos.exit_price == 105.0, f"Should exit at trailing stop 105, got {pos.exit_price}"


def test_trailing_stop_short():
    """Trailing stop works symmetrically for SHORT positions."""
    pos = Position("TEST_TSL_S", "SBIN.NS", "SHORT", 500.0, 515.0, 470.0, 10.0, 50, features={'atr_val': 10})
    pos.entry_time = datetime(2024, 1, 1, 10, 0)

    # Price drops to +2.5R — trailing should activate
    pos.update(_bar(478.0, 480.0, 475.0, close=477.0, ema_20=500.0), datetime(2024, 1, 1, 10, 10))
    assert pos.trailing_activated, "SHORT trailing should activate"
    assert pos.stop == 490.0, f"SHORT stop should ratchet to entry-1R=490, got {pos.stop}"


# === EOD FLATTEN TEST ===

def test_eod_flatten():
    """Positions must auto-close at 15:15 IST."""
    pos = Position("TEST_EOD", "HDFCBANK.NS", "LONG", 1500.0, 1480.0, 1560.0, 10.0, 50, features={'atr_val': 10})
    pos.entry_time = datetime(2024, 1, 1, 10, 0)

    pos.update(_bar(1510.0, 1515.0, 1505.0, close=1512.0), datetime(2024, 1, 1, 14, 0))
    assert pos.open, "Should be open before 15:15"

    pos.update(_bar(1520.0, 1525.0, 1515.0, close=1522.0), datetime(2024, 1, 1, 15, 0))
    assert pos.open, "Should be open at 15:00"

    pos.update(_bar(1518.0, 1522.0, 1516.0, close=1520.0), datetime(2024, 1, 1, 15, 15))
    assert not pos.open, "Should close at 15:15 (EOD flatten)"
    assert pos.exit_reason == "EOD_FLATTEN"


# === MAX HOLD PERIOD TEST ===

def test_max_hold_period():
    """Positions must auto-exit after MAX_HOLD_BARS bars."""
    import src.config as config

    pos = Position("TEST_MAX", "AXISBANK.NS", "LONG", 1000.0, 980.0, 1060.0, 10.0, 50, features={'atr_val': 10})
    pos.entry_time = datetime(2024, 1, 1, 9, 15)

    # Simulate bars that don't trigger stop/target/EOD
    for i in range(config.MAX_HOLD_BARS - 1):
        pos.update(_bar(1001.0, 1005.0, 998.0, close=1002.0), datetime(2024, 1, 1, 10, i))
        if not pos.open:
            break

    assert pos.open, f"Should still be open after {config.MAX_HOLD_BARS - 1} bars"

    # One more bar should trigger MAX_HOLD
    pos.update(_bar(1002.0, 1004.0, 999.0, close=1001.0), datetime(2024, 1, 1, 13, 0))
    assert not pos.open, "Should close at max hold limit"
    assert pos.exit_reason == "MAX_HOLD"


# === COST MODEL & NET R TESTS ===

def test_position_net_r_with_costs():
    """Net R must account for transaction costs."""
    cost_model = NSECostModel()
    pos = Position("TEST_004", "HDFC.NS", "LONG", 100.0, 92.0, 120.0, 5.0, 100, features={'atr_val': 5, 'vol_ma_20': 1_000_000})
    pos.entry_time = datetime(2024, 1, 1, 10, 0)

    pos.update(_bar(101.0, 121.0, 100.0, close=118.0), datetime(2024, 1, 1, 14, 0))
    net_r = pos.calculate_net_r(cost_model)
    gross_r = (120.0 - 100.0) * 100 / (abs(100.0 - 92.0) * 100)
    assert net_r < gross_r, f"Net R ({net_r}) should be less than gross R ({gross_r}) due to costs"
    assert net_r > 0, f"Net R should be positive for a winning trade, got {net_r}"


def test_cost_model_stt():
    """STT should only apply on SELL side for intraday."""
    model = NSECostModel(instrument_type="EQUITY_INTRADAY")
    buy_cost = model.calculate_leg_cost(100.0, 100, "BUY", adv=5_000_000)
    sell_cost = model.calculate_leg_cost(100.0, 100, "SELL", adv=5_000_000)
    assert sell_cost > buy_cost, "Sell side should cost more (STT applies only on sell for intraday)"


def test_short_position_lifecycle():
    """SHORT positions should work symmetrically."""
    pos = Position("TEST_005", "SBIN.NS", "SHORT", 500.0, 515.0, 470.0, 10.0, 50, features={'atr_val': 10})
    pos.entry_time = datetime(2024, 1, 1, 10, 0)

    pos.update(_bar(498.0, 502.0, 469.0, close=471.0, ema_20=500.0), datetime(2024, 1, 1, 11, 0))
    assert not pos.open, "SHORT should close at target"
    assert pos.exit_reason == "TARGET"
    assert pos.exit_price == 470.0


# === CORRELATION FILTER TEST ===

def test_correlation_filter():
    """Correlation filter should block highly correlated positions."""
    constraints = PortfolioConstraints()

    corr = constraints._sector_correlation_proxy("HDFCBANK.NS", "ICICIBANK.NS")
    assert corr >= 0.75, f"Same-sector should have high correlation, got {corr}"

    corr_diff = constraints._sector_correlation_proxy("HDFCBANK.NS", "TCS.NS")
    assert corr_diff < 0.75, f"Different-sector should have low correlation, got {corr_diff}"
