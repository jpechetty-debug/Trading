"""
Unit Tests for Core Trading Logic V6.5.
Tests Kill Score, Position lifecycle (stop, gap, target, trailing, EOD, max hold),
Execution model, Cost model, and Correlation filter.
"""
import sys
import os
import pandas as pd
from datetime import datetime
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.execution.model import ExecutionModel
from src.execution.costs import NSECostModel
from src.backtest.position import Position
from src.backtest.constraints import PortfolioConstraints


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
    print("✅ RR Veto: Correctly rejects poor Risk/Reward trades")

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
    print(f"✅ Valid Trade: Entry={orders['entry']}, Stop={orders['stop']}, Target={orders['target']}, RR={orders['risk_reward']}")


# === POSITION LIFECYCLE TESTS ===

def test_position_stop_hit():
    """Position must close when price hits stop."""
    pos = Position("TEST_001", "RELIANCE.NS", "LONG", 100.0, 92.0, 120.0, 5.0, 100)
    pos.entry_time = datetime(2024, 1, 1, 10, 0)
    pos.update(100.5, 102.0, 98.0, datetime(2024, 1, 1, 10, 5))
    assert pos.open, "Position should still be open"
    pos.update(97.0, 97.5, 91.0, datetime(2024, 1, 1, 10, 10))
    assert not pos.open, "Position should be closed (stop hit)"
    assert pos.exit_reason == "STOP"
    assert pos.exit_price == 92.0
    print("✅ Stop Hit: Position closed at stop price correctly")

def test_position_gap_stop():
    """Gap-down below stop must fill at open price (worse fill)."""
    pos = Position("TEST_002", "TCS.NS", "LONG", 100.0, 95.0, 115.0, 5.0, 50)
    pos.entry_time = datetime(2024, 1, 1, 10, 0)
    pos.update(90.0, 93.0, 88.0, datetime(2024, 1, 2, 9, 15))
    assert not pos.open, "Position should be closed (gap stop)"
    assert pos.exit_reason == "STOP_GAP"
    assert pos.exit_price == 90.0, f"Gap stop should fill at open, got {pos.exit_price}"
    print("✅ Gap Stop: Correctly fills at open price, not stop price")

def test_position_target_hit():
    """Position must close when price hits target."""
    pos = Position("TEST_003", "INFY.NS", "LONG", 100.0, 92.0, 120.0, 5.0, 100)
    pos.entry_time = datetime(2024, 1, 1, 10, 0)
    pos.update(101.0, 121.0, 100.0, datetime(2024, 1, 1, 14, 0))
    assert not pos.open, "Position should be closed (target hit)"
    assert pos.exit_reason == "TARGET"
    assert pos.exit_price == 120.0
    print("✅ Target Hit: Position closed at target price correctly")


# === V6.5 NEW: TRAILING STOP TESTS ===

def test_trailing_stop_activation():
    """Trailing stop activates at +2R and locks 1R profit."""
    # Entry=100, Stop=90, Target=130, R-unit=5
    # +2R = 110, trailing should lock stop at 100+5=105
    pos = Position("TEST_TSL", "WIPRO.NS", "LONG", 100.0, 90.0, 130.0, 5.0, 100)
    pos.entry_time = datetime(2024, 1, 1, 10, 0)

    # Price moves to +1.5R — trailing should NOT activate yet
    pos.update(105.0, 107.5, 104.0, datetime(2024, 1, 1, 10, 5))
    assert pos.open, "Should still be open at +1.5R"
    assert not pos.trailing_activated, "Trailing should not activate below +2R"
    assert pos.stop == 90.0, f"Stop should remain at 90, got {pos.stop}"

    # Price pushes to +2.2R — trailing should activate
    pos.update(110.0, 111.0, 109.0, datetime(2024, 1, 1, 10, 10))
    assert pos.trailing_activated, "Trailing should activate at +2R"
    assert pos.stop == 105.0, f"Stop should ratchet to entry+1R=105, got {pos.stop}"

    # Price falls back to trailing stop — should exit at 105 (locked profit)
    pos.update(104.0, 106.0, 104.5, datetime(2024, 1, 1, 10, 15))
    assert not pos.open, "Should close at trailing stop"
    assert pos.exit_reason == "STOP"
    assert pos.exit_price == 105.0, f"Should exit at trailing stop 105, got {pos.exit_price}"
    print("✅ Trailing Stop: Activates at +2R, locks 1R profit, exits correctly")


def test_trailing_stop_short():
    """Trailing stop works symmetrically for SHORT positions."""
    # Entry=500, Stop=515, Target=470, R-unit=10
    # +2R down = 480, trailing should lock stop at 500-10=490
    pos = Position("TEST_TSL_S", "SBIN.NS", "SHORT", 500.0, 515.0, 470.0, 10.0, 50)
    pos.entry_time = datetime(2024, 1, 1, 10, 0)

    # Price drops to +2.5R — trailing should activate
    pos.update(478.0, 480.0, 475.0, datetime(2024, 1, 1, 10, 10))
    assert pos.trailing_activated, "SHORT trailing should activate"
    assert pos.stop == 490.0, f"SHORT stop should ratchet to entry-1R=490, got {pos.stop}"
    print("✅ Trailing Stop (SHORT): Activates symmetrically at +2R profit")


# === V6.5 NEW: EOD FLATTEN TEST ===

def test_eod_flatten():
    """Positions must auto-close at 15:15 IST."""
    pos = Position("TEST_EOD", "HDFCBANK.NS", "LONG", 1500.0, 1480.0, 1560.0, 10.0, 50)
    pos.entry_time = datetime(2024, 1, 1, 10, 0)

    # Timeline: 14:00 → 15:00 → 15:15 (flatten)
    pos.update(1510.0, 1515.0, 1505.0, datetime(2024, 1, 1, 14, 0))
    assert pos.open, "Should be open before 15:15"

    pos.update(1520.0, 1525.0, 1515.0, datetime(2024, 1, 1, 15, 0))
    assert pos.open, "Should be open at 15:00"

    pos.update(1518.0, 1522.0, 1516.0, datetime(2024, 1, 1, 15, 15))
    assert not pos.open, "Should close at 15:15 (EOD flatten)"
    assert pos.exit_reason == "EOD_FLATTEN"
    print(f"✅ EOD Flatten: Auto-closed at 15:15 IST @ {pos.exit_price}")


# === V6.5 NEW: MAX HOLD PERIOD TEST ===

def test_max_hold_period():
    """Positions must auto-exit after MAX_HOLD_BARS bars."""
    import src.config as config
    
    pos = Position("TEST_MAX", "AXISBANK.NS", "LONG", 1000.0, 980.0, 1060.0, 10.0, 50)
    pos.entry_time = datetime(2024, 1, 1, 9, 15)

    # Simulate bars that don't trigger stop/target/EOD
    for i in range(config.MAX_HOLD_BARS - 1):
        pos.update(1001.0, 1005.0, 998.0, datetime(2024, 1, 1, 10, i))
        if not pos.open:
            break

    assert pos.open, f"Should still be open after {config.MAX_HOLD_BARS - 1} bars"

    # One more bar should trigger MAX_HOLD
    pos.update(1002.0, 1004.0, 999.0, datetime(2024, 1, 1, 13, 0))
    assert not pos.open, "Should close at max hold limit"
    assert pos.exit_reason == "MAX_HOLD"
    print(f"✅ Max Hold: Auto-closed after {config.MAX_HOLD_BARS} bars @ {pos.exit_price}")


# === COST MODEL & NET R TESTS ===

def test_position_net_r_with_costs():
    """Net R must account for transaction costs."""
    cost_model = NSECostModel()
    pos = Position("TEST_004", "HDFC.NS", "LONG", 100.0, 92.0, 120.0, 5.0, 100, features={'vol_ma_20': 1_000_000})
    pos.entry_time = datetime(2024, 1, 1, 10, 0)
    pos.update(101.0, 121.0, 100.0, datetime(2024, 1, 1, 14, 0))
    net_r = pos.calculate_net_r(cost_model)
    gross_r = (120.0 - 100.0) * 100 / (abs(100.0 - 92.0) * 100)
    assert net_r < gross_r, f"Net R ({net_r}) should be less than gross R ({gross_r}) due to costs"
    assert net_r > 0, f"Net R should be positive for a winning trade, got {net_r}"
    print(f"✅ Net R with Costs: Gross={gross_r:.2f}R, Net={net_r:.2f}R (friction deducted)")

def test_cost_model_stt():
    """STT should only apply on SELL side for intraday."""
    model = NSECostModel(instrument_type="EQUITY_INTRADAY")
    buy_cost = model.calculate_leg_cost(100.0, 100, "BUY", adv=5_000_000)
    sell_cost = model.calculate_leg_cost(100.0, 100, "SELL", adv=5_000_000)
    assert sell_cost > buy_cost, "Sell side should cost more (STT applies only on sell for intraday)"
    print(f"✅ STT Logic: Buy cost={buy_cost:.2f}, Sell cost={sell_cost:.2f}")

def test_short_position_lifecycle():
    """SHORT positions should work symmetrically."""
    pos = Position("TEST_005", "SBIN.NS", "SHORT", 500.0, 515.0, 470.0, 10.0, 50)
    pos.entry_time = datetime(2024, 1, 1, 10, 0)
    pos.update(498.0, 502.0, 469.0, datetime(2024, 1, 1, 11, 0))
    assert not pos.open, "SHORT should close at target"
    assert pos.exit_reason == "TARGET"
    assert pos.exit_price == 470.0
    print("✅ Short Target: SHORT position closed correctly at target")


# === CORRELATION FILTER TEST ===

def test_correlation_filter():
    """Correlation filter should block highly correlated positions."""
    constraints = PortfolioConstraints()
    
    # Check sector-based fallback: same sector = high correlation
    corr = constraints._sector_correlation_proxy("HDFCBANK.NS", "ICICIBANK.NS")
    assert corr >= 0.75, f"Same-sector should have high correlation, got {corr}"
    
    corr_diff = constraints._sector_correlation_proxy("HDFCBANK.NS", "TCS.NS")
    assert corr_diff < 0.75, f"Different-sector should have low correlation, got {corr_diff}"
    print(f"✅ Correlation Filter: Same-sector={corr:.2f}, Cross-sector={corr_diff:.2f}")


if __name__ == "__main__":
    print("=" * 60)
    print("🧪 Running Core Logic Unit Tests (V6.5)")
    print("=" * 60)

    tests = [
        test_execution_model_rr_veto,
        test_execution_model_valid_trade,
        test_position_stop_hit,
        test_position_gap_stop,
        test_position_target_hit,
        test_trailing_stop_activation,
        test_trailing_stop_short,
        test_eod_flatten,
        test_max_hold_period,
        test_position_net_r_with_costs,
        test_cost_model_stt,
        test_short_position_lifecycle,
        test_correlation_filter,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"❌ {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"❌ {test.__name__}: CRASH - {e}")
            failed += 1

    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)}")
    print("=" * 60)
