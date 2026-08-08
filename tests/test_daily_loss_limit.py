import pytest
import pandas as pd
import numpy as np
from unittest.mock import MagicMock

from src.backtest.portfolio_engine import PortfolioEngine
from src.data.store import DataStore
import src.config as config

def _make_ohlcv(index, start_price, drift=0.0, vol=0.4, seed=0):
    rng = np.random.default_rng(seed)
    steps = drift + rng.normal(0, vol, size=len(index))
    close = start_price + np.cumsum(steps)
    close = np.maximum(close, 1.0)
    high = close + rng.uniform(0.1, 0.6, size=len(index))
    low = close - rng.uniform(0.1, 0.6, size=len(index))
    low = np.minimum(low, close - 0.01)
    open_ = close + rng.normal(0, 0.15, size=len(index))
    volume = rng.uniform(500_000, 1_500_000, size=len(index))
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=index,
    )

@pytest.fixture
def synthetic_universe():
    idx = pd.date_range("2024-01-01 09:15", periods=300, freq="5min")
    return {
        "^NSEI": _make_ohlcv(idx, start_price=20000.0, drift=0.05, vol=15.0, seed=1),
        "STOCKA.NS": _make_ohlcv(idx, start_price=100.0, drift=0.08, vol=0.5, seed=2),
    }

def test_daily_loss_limit_skips_new_trades(monkeypatch, synthetic_universe):
    def fake_load_ticker(self, ticker):
        return synthetic_universe.get(ticker)

    monkeypatch.setattr(DataStore, "load_ticker", fake_load_ticker)

    engine = PortfolioEngine(
        tickers=["STOCKA.NS"],
        start_date="2024-01-01",
        initial_equity=100_000.0,
        kill_threshold=1.0, 
    )
    
    # We will mock check_daily_loss_limit to always return False to simulate hitting the limit
    def fake_check_daily_loss(current_equity, sod_equity):
        return False, "DAILY_LOSS_LIMIT_REACHED"

    monkeypatch.setattr(engine.constraints, "check_daily_loss_limit", fake_check_daily_loss)
    
    # We will also mock can_open_trade to record if it was called
    engine.constraints.can_open_trade_called = False
    original_can_open_trade = engine.constraints.can_open_trade
    def fake_can_open_trade(*args, **kwargs):
        engine.constraints.can_open_trade_called = True
        return original_can_open_trade(*args, **kwargs)
    
    monkeypatch.setattr(engine.constraints, "can_open_trade", fake_can_open_trade)
    
    engine.run()

    # Since daily loss limit is hit every day, no trades should have been executed.
    assert len(engine.portfolio.open_positions) == 0
    assert len(engine.portfolio.closed_trades) == 0
    # And can_open_trade should never have been evaluated for new signals
    assert engine.constraints.can_open_trade_called == False
