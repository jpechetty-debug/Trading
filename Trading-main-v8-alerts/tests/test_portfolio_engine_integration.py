"""
test_portfolio_engine_integration.py — End-to-end smoke test for the
full backtest loop.

Prior to this file, nothing exercised PortfolioEngine.run() as a whole:
every existing test constructs isolated components (Position, Execution
Model, Constraints, ...) with hand-built dicts. That's valuable for unit
coverage but it's exactly why integration-level bugs (Nifty timeframe
mismatch in the live scanner, the RS_Score column never actually being
attached to backtest bars) went unnoticed — no test ever ran the pieces
together against real-shaped data.

This test stubs out DataStore.load_ticker with synthetic in-memory OHLCV
data (no network access, no dependency on yfinance/Yahoo actually being
reachable) and runs a full PortfolioEngine simulation over it, asserting
the loop completes without exceptions and that the RS_Score column
(added as part of the structural-exit fix) is actually present on the
loaded bars.
"""
import numpy as np
import pandas as pd
import pytest

from src.backtest.portfolio_engine import PortfolioEngine
from src.data.store import DataStore


def _make_ohlcv(index, start_price, drift=0.0, vol=0.4, seed=0):
    rng = np.random.default_rng(seed)
    steps = drift + rng.normal(0, vol, size=len(index))
    close = start_price + np.cumsum(steps)
    close = np.maximum(close, 1.0)  # keep prices positive
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
    # Enough bars to clear PortfolioEngine.warmup (150) with room to spare.
    idx = pd.date_range("2024-01-01 09:15", periods=600, freq="5min")
    return {
        "^NSEI": _make_ohlcv(idx, start_price=20000.0, drift=0.05, vol=15.0, seed=1),
        "STOCKA.NS": _make_ohlcv(idx, start_price=100.0, drift=0.08, vol=0.5, seed=2),
        "STOCKB.NS": _make_ohlcv(idx, start_price=250.0, drift=-0.05, vol=1.0, seed=3),
    }


def test_full_backtest_loop_runs_without_error(monkeypatch, synthetic_universe):
    def fake_load_ticker(self, ticker):
        return synthetic_universe.get(ticker)

    monkeypatch.setattr(DataStore, "load_ticker", fake_load_ticker)

    engine = PortfolioEngine(
        tickers=["STOCKA.NS", "STOCKB.NS"],
        start_date="2024-01-01",
        initial_equity=100_000.0,
        kill_threshold=6.0,
    )

    results = engine.run()

    # The main assertion is simply that the whole pipeline (data loading,
    # regime detection, breadth, RS scoring, sizing, constraints, exits,
    # cost model, reporting) runs to completion on realistic-shaped data
    # without raising.
    assert isinstance(results, pd.DataFrame)

    # Confirm the RS_Score wiring fix actually took effect end-to-end.
    for ticker in ["STOCKA.NS", "STOCKB.NS"]:
        assert "RS_Score" in engine.data_map[ticker].columns

    # If any trades closed, sanity-check the shape of the report.
    if not results.empty:
        assert {"ticker", "PnL", "Net_R", "Reason", "Held"}.issubset(results.columns)
        # No trade should have been opened with zero shares (the sizing
        # bugfix in ExecutionModel.generate_orders).
        for pos in engine.portfolio.closed_trades:
            assert pos.shares > 0
