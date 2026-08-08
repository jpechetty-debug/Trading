"""
test_brain_a.py — BrainAV5 tests.

Prior to this file, brain_a_v5.py (the signal/scoring engine — the
largest and most critical module in the codebase) had zero dedicated
test coverage. That gap is exactly why several real bugs (RS score
computed from timeframe-mismatched data in scanner.py, calculate_rs_slope
comparing misaligned candles, calculate_technicals mutating the caller's
DataFrame) survived undetected: the existing suite only exercised other
modules with hand-built dicts that happened to already contain the right
fields.
"""
import numpy as np
import pandas as pd
import pytest

from src.brain_a_v5 import BrainAV5


def _make_ohlcv(index, start_price=100.0, drift=0.0, seed=0, n=None):
    """Build a synthetic OHLCV frame with a mild trend + noise."""
    rng = np.random.default_rng(seed)
    n = n or len(index)
    steps = drift + rng.normal(0, 0.3, size=n)
    close = start_price + np.cumsum(steps)
    high = close + rng.uniform(0.1, 0.5, size=n)
    low = close - rng.uniform(0.1, 0.5, size=n)
    open_ = close + rng.normal(0, 0.1, size=n)
    volume = rng.uniform(50_000, 150_000, size=n)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=index,
    )


@pytest.fixture
def brain():
    return BrainAV5()


@pytest.fixture
def aligned_frames():
    """Stock and Nifty on the exact same 5-min timestamps (the normal case)."""
    idx = pd.date_range("2024-01-01 09:15", periods=250, freq="5min")
    stock_df = _make_ohlcv(idx, start_price=100.0, drift=0.05, seed=1)
    nifty_df = _make_ohlcv(idx, start_price=20000.0, drift=0.0, seed=2)
    return stock_df, nifty_df


class TestCalculateTechnicals:
    def test_does_not_mutate_callers_dataframe(self, brain):
        """Regression test: calculate_technicals used to bolt 14 new
        columns onto the caller's original object even though it returned
        a *different* (NaN-dropped) DataFrame, contradicting its own
        docstring. It must now leave the input untouched."""
        idx = pd.date_range("2024-01-01 09:15", periods=60, freq="5min")
        df = _make_ohlcv(idx, seed=3)
        original_cols = list(df.columns)

        brain.calculate_technicals(df)

        assert list(df.columns) == original_cols, \
            "calculate_technicals must not add columns to the caller's DataFrame"

    def test_adds_expected_indicator_columns(self, brain):
        idx = pd.date_range("2024-01-01 09:15", periods=250, freq="5min")
        df = _make_ohlcv(idx, seed=4)
        out = brain.calculate_technicals(df)

        for col in ["EMA_20", "EMA_50", "ATR", "VWAP", "Resistance", "Support",
                    "Swing_High", "Swing_Low", "Vol_MA_20"]:
            assert col in out.columns
        assert not out.empty
        assert not out[["EMA_20", "ATR"]].isna().any().any()


class TestRelativeStrength:
    def test_returns_zero_when_nifty_missing(self, brain, aligned_frames):
        stock_df, _ = aligned_frames
        assert brain.calculate_relative_strength(stock_df, None) == 0.0
        assert brain.calculate_rs_slope(stock_df, None) == 0.0

    def test_outperformance_produces_positive_score(self, brain):
        idx = pd.date_range("2024-01-01 09:15", periods=60, freq="5min")
        # Stock drifts up strongly; Nifty is flat -> stock should show
        # positive relative strength.
        stock_df = _make_ohlcv(idx, start_price=100.0, drift=0.3, seed=5)
        nifty_df = _make_ohlcv(idx, start_price=20000.0, drift=0.0, seed=6)

        rs = brain.calculate_relative_strength(stock_df, nifty_df, lookback=12)
        assert rs > 0

    def test_timeframe_mismatch_yields_no_signal(self, brain):
        """Documents the failure mode fixed in scanner.py: joining 5-min
        stock bars against DAILY nifty bars leaves ~0 overlapping
        timestamps, so RS silently degrades to 0.0 rather than raising.
        This is why the bug was easy to miss in production — it fails
        quietly, not loudly."""
        stock_idx = pd.date_range("2024-01-01 09:15", periods=60, freq="5min")
        nifty_idx = pd.date_range("2023-10-01", periods=60, freq="1D")

        stock_df = _make_ohlcv(stock_idx, start_price=100.0, drift=0.3, seed=7)
        nifty_df = _make_ohlcv(nifty_idx, start_price=20000.0, drift=0.0, seed=8)

        rs = brain.calculate_relative_strength(stock_df, nifty_df, lookback=12)
        assert rs == 0.0

    def test_slope_is_finite_on_aligned_data(self, brain, aligned_frames):
        stock_df, nifty_df = aligned_frames
        slope = brain.calculate_rs_slope(stock_df, nifty_df, lookback=12)
        assert np.isfinite(slope)

    def test_slope_survives_missing_bars_in_nifty(self, brain):
        """Regression test for the positional-slicing alignment bug:
        calculate_rs_slope used to slice stock_df.iloc[:-i] and
        nifty_df.iloc[:-i] independently and assume they lined up
        position-for-position. If Nifty is missing bars that the stock has
        (a realistic occurrence — partial data, gaps), the old code would
        silently compare mismatched timestamps. The fixed version aligns
        by timestamp first, so it should still return a finite, sane
        value instead of drifting off garbage inputs."""
        idx = pd.date_range("2024-01-01 09:15", periods=100, freq="5min")
        stock_df = _make_ohlcv(idx, start_price=100.0, drift=0.05, seed=9)

        nifty_full = _make_ohlcv(idx, start_price=20000.0, drift=0.0, seed=10)
        # Drop 5 bars from the middle of Nifty's data only.
        drop_idx = idx[40:45]
        nifty_df = nifty_full.drop(index=drop_idx)

        slope = brain.calculate_rs_slope(stock_df, nifty_df, lookback=12)
        assert np.isfinite(slope)


class TestAnalyzeSlice:
    def test_pure_function_no_mutation_of_inputs(self, brain, aligned_frames):
        stock_df, nifty_df = aligned_frames
        stock_before = stock_df.copy()
        nifty_before = nifty_df.copy()

        brain.analyze_slice("TEST.NS", stock_df, nifty_df, "Bullish")

        pd.testing.assert_frame_equal(stock_df, stock_before)
        pd.testing.assert_frame_equal(nifty_df, nifty_before)

    def test_returns_none_on_insufficient_data(self, brain):
        idx = pd.date_range("2024-01-01 09:15", periods=10, freq="5min")
        stock_df = _make_ohlcv(idx, seed=11)
        nifty_df = _make_ohlcv(idx, start_price=20000.0, seed=12)

        result = brain.analyze_slice("TEST.NS", stock_df, nifty_df, "Neutral")
        assert result is None

    def test_kill_score_bounded_zero_to_ten(self, brain, aligned_frames):
        stock_df, nifty_df = aligned_frames
        result = brain.analyze_slice("TEST.NS", stock_df, nifty_df, "Bullish")
        if result is not None:
            assert 0 <= result["kill_score"] <= 10
            assert result["direction"] in ("LONG", "SHORT")
