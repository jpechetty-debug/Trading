"""
test_backtest_engine.py — Regression tests for BacktestEngine (the
single-ticker wrapper used by research/walk_forward_v6.py and
research/wfo/run_wfo.py).

Two real bugs were found by direct reproduction against this class:

1. `run()` referenced an undefined name `engine` when building the
   enriched trade report — a guaranteed NameError on any call that
   closed at least one trade (i.e. the normal, expected case).

2. `run()` reused a single PortfolioEngine instance across every
   `run(ticker)` call, swapping only `.tickers`. Since PortfolioEngine
   never resets `equity`, `portfolio.closed_trades`, `equity_history`,
   or `peak_equity` between calls, the 2nd+ ticker's backtest silently
   inherited the 1st ticker's ending equity/drawdown state, and the
   final report mixed closed trades from every prior ticker together.
   Confirmed directly: seeding fake ticker-A state before running
   ticker B, ticker B's report still contained ticker A's trade and
   ticker B's own run started from ticker A's equity, not
   initial_equity.

Neither bug was ever caught by the existing suite because nothing
exercised `BacktestEngine.run()` end to end, nor called it more than
once. These tests do both.
"""
import numpy as np
import pandas as pd
import pytest

from src.backtest_engine import BacktestEngine
from src.data.store import DataStore


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
    idx = pd.date_range("2024-01-01 09:15", periods=600, freq="5min")
    return {
        "^NSEI": _make_ohlcv(idx, start_price=20000.0, drift=0.05, vol=15.0, seed=1),
        # Strong, low-noise uptrend so the engine reliably opens and
        # closes at least one LONG trade — needed to actually exercise
        # the enriched-report code path where the NameError lived.
        "STOCKA.NS": _make_ohlcv(idx, start_price=100.0, drift=0.6, vol=0.3, seed=2),
        "STOCKB.NS": _make_ohlcv(idx, start_price=100.0, drift=0.6, vol=0.3, seed=9),
    }


def test_run_does_not_crash_when_trades_close(monkeypatch, synthetic_universe):
    """Regression test for the `engine` NameError."""
    monkeypatch.setattr(DataStore, "load_ticker",
                         lambda self, ticker: synthetic_universe.get(ticker))

    engine = BacktestEngine(start_date="2024-01-01", kill_threshold=1.0)
    df = engine.run("STOCKA.NS")  # must not raise

    assert isinstance(df, pd.DataFrame)
    if not df.empty:
        assert "Net_R" in df.columns


def test_sequential_runs_do_not_leak_state(monkeypatch, synthetic_universe):
    """Regression test for cross-ticker equity/trade contamination."""
    monkeypatch.setattr(DataStore, "load_ticker",
                         lambda self, ticker: synthetic_universe.get(ticker))

    engine = BacktestEngine(start_date="2024-01-01", kill_threshold=1.0)

    df_a = engine.run("STOCKA.NS")
    df_b = engine.run("STOCKB.NS")

    # Each call must report only its own ticker's trades.
    if not df_a.empty:
        assert set(df_a["ticker"]) == {"STOCKA.NS"}
    if not df_b.empty:
        assert set(df_b["ticker"]) == {"STOCKB.NS"}
        # None of ticker A's trades should show up in ticker B's report.
        assert "STOCKA.NS" not in set(df_b["ticker"])


def test_research_param_tuning_persists_across_runs(monkeypatch, synthetic_universe):
    """The one piece of shared state that SHOULD persist across calls:
    research scripts tune engine.brain / engine.exec_model once (see
    research/wfo/run_wfo.py) and expect every subsequent run(ticker) to
    use the tuned values."""
    monkeypatch.setattr(DataStore, "load_ticker",
                         lambda self, ticker: synthetic_universe.get(ticker))

    engine = BacktestEngine(start_date="2024-01-01", kill_threshold=1.0)
    engine.brain.rs_lookback = 42
    engine.exec_model.stop_mult = 3.3

    engine.run("STOCKA.NS")

    assert engine.brain.rs_lookback == 42
    assert engine.exec_model.stop_mult == 3.3
