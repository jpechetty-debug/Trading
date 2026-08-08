import pytest
from unittest.mock import Mock, patch
import pandas as pd
from src.live_scanner import LiveScanner
from src.data.websocket_feed import Candle
import src.config as config

def test_on_candle_close_forwards_full_result_dict():
    """
    Ensures that LiveScanner.on_candle_close passes the full `result` dict 
    from BrainAV5 directly to ExecutionModel.generate_orders().
    This prevents regressions where a truncated dict was built and 
    kill_score was defaulted to 6.0.
    """
    scanner = LiveScanner(symbols=["RELIANCE.NS"])
    
    # Mock candle_builder.get_dataframe to return a dummy dataframe
    scanner.candle_builder.get_dataframe = Mock(return_value=pd.DataFrame())
    
    # Define a full result dict that BrainA would produce
    expected_result = {
        'symbol': 'RELIANCE.NS',
        'close': 2500.0,
        'atr': 25.0,
        'direction': 'LONG',
        'kill_score': 8.5,  # Real conviction score, not defaulted to 6.0
        'support': 2400.0,
        'resistance': 2600.0,
        'swing_low': 2450.0,
        'reasons': ['Strong momentum', 'Sector rotation']
    }
    
    # Mock brain.analyze_slice to return our expected_result
    scanner.brain.analyze_slice = Mock(return_value=expected_result)
    
    # Mock execution model to capture what gets passed to generate_orders
    scanner.exec_model.generate_orders = Mock(return_value={
        'entry': 2500.0,
        'stop': 2450.0,
        'target': 2600.0,
        'shares': 100
    })
    
    # Mock DB and AlertManager to prevent actual side effects during testing
    scanner.alerts.send_signal_alert = Mock(return_value=["telegram"])
    
    # We need to mock get_db as well, which is imported in live_scanner
    with patch('src.live_scanner.get_db') as mock_get_db:
        mock_db_instance = Mock()
        mock_get_db.return_value = mock_db_instance
        
        # Trigger the candle close event
        dummy_candle = Candle(symbol="RELIANCE.NS", timestamp=1234567890, open=2490, high=2510, low=2480, close=2500, volume=1000)
        scanner.on_candle_close("RELIANCE.NS", dummy_candle)
        
        # Assert generate_orders was called exactly once
        scanner.exec_model.generate_orders.assert_called_once()
        
        # Extract the argument passed to generate_orders
        call_args = scanner.exec_model.generate_orders.call_args[0]
        passed_result = call_args[0]
        
        # Assert the passed dict is EXACTLY the expected result dict
        assert passed_result == expected_result
        assert passed_result['kill_score'] == 8.5
        assert passed_result['support'] == 2400.0
        assert passed_result['resistance'] == 2600.0
        assert 'reasons' in passed_result
