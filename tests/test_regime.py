"""
test_regime.py — RegimeDetector tests.
"""
import pytest
import pandas as pd
import numpy as np
from src.regime import RegimeDetector


class TestRegimeDetector:
    def test_bullish_above_primary(self):
        rd = RegimeDetector()
        result = rd.detect(nifty_close=22000, nifty_ema_primary=21500, nifty_ema_gatekeeper=21800)
        assert result["regime"] == "Bullish"
        assert result["longs_authorized"]

    def test_bearish_below_primary(self):
        rd = RegimeDetector()
        result = rd.detect(nifty_close=21000, nifty_ema_primary=21500, nifty_ema_gatekeeper=21200)
        assert result["regime"] == "Bearish"

    def test_longs_vetoed_below_gatekeeper(self):
        rd = RegimeDetector()
        result = rd.detect(nifty_close=22000, nifty_ema_primary=21500, nifty_ema_gatekeeper=22500)
        assert result["regime"] == "Bullish"  # Above primary
        assert not result["longs_authorized"]  # But below gatekeeper

    def test_detect_from_slice_short_data(self):
        rd = RegimeDetector()
        short_df = pd.DataFrame({'Close': [100.0] * 10})
        result = rd.detect_from_slice(short_df)
        assert result["regime"] == "Neutral"

    def test_detect_from_slice_bullish(self):
        rd = RegimeDetector(primary_period=5, gatekeeper_period=3)
        # Strongly trending up
        prices = list(range(90, 120))
        df = pd.DataFrame({'Close': [float(p) for p in prices]})
        result = rd.detect_from_slice(df)
        assert result["regime"] == "Bullish"
        assert result["longs_authorized"]


class TestGetBreadthScore:
    """
    Regression tests for the validity-mask fix in get_breadth_score.

    Before the fix, pd.DataFrame union-alignment inserted NaN for tickers
    whose index didn't cover a given timestamp.  A NaN comparison evaluates
    to False, so absent tickers were silently counted as "not above EMA"
    and deflated the breadth reading.  These tests pin the correct behaviour.
    """

    def _make_ticker_df(self, timestamps, closes, emas):
        """Build a minimal ticker DataFrame with Close and EMA_50 columns."""
        return pd.DataFrame(
            {"Close": closes, "EMA_50": emas},
            index=pd.DatetimeIndex(timestamps),
        )

    def test_missing_bar_excluded_not_counted_as_below(self):
        """
        Ticker B is missing the middle bar (09:20).  At that timestamp only
        Ticker A has data, and A is above its EMA → breadth should be 1.0.
        With the old (unfixed) code it would be 0.5 because B's NaN row was
        treated as False (below EMA).
        """
        ts = pd.to_datetime(["2024-01-02 09:15", "2024-01-02 09:20", "2024-01-02 09:25"])

        # Ticker A: always above its EMA, present at all three bars
        df_a = self._make_ticker_df(ts, closes=[110, 112, 114], emas=[100, 100, 100])
        # Ticker B: always below its EMA, but MISSING the 09:20 bar
        df_b = self._make_ticker_df(
            [ts[0], ts[2]], closes=[90, 90], emas=[100, 100]
        )

        universe = {"TICKER_A": df_a, "TICKER_B": df_b}
        rd = RegimeDetector()

        # At 09:20 only A has data and A is bullish → breadth must be 1.0
        score = rd.get_breadth_score(universe, ts[1])
        assert score == pytest.approx(1.0), (
            f"Expected 1.0 (only A present, A is above EMA) but got {score}. "
            "Ticker B's missing bar was likely counted as 'below EMA'."
        )

    def test_aligned_universe_returns_correct_fraction(self):
        """
        When all tickers have data at a timestamp, the breadth fraction
        should be computed exactly over all tickers (no validity-mask edge
        case, just a sanity check for the happy path).
        """
        ts = pd.to_datetime(["2024-01-02 09:15", "2024-01-02 09:20"])

        # A above EMA, B below EMA — both present at both bars
        df_a = self._make_ticker_df(ts, closes=[110, 112], emas=[100, 100])
        df_b = self._make_ticker_df(ts, closes=[90, 88], emas=[100, 100])

        universe = {"TICKER_A": df_a, "TICKER_B": df_b}
        rd = RegimeDetector()

        score = rd.get_breadth_score(universe, ts[0])
        assert score == pytest.approx(0.5), (
            f"Expected 0.5 (1 of 2 tickers above EMA) but got {score}."
        )

    def test_empty_universe_returns_neutral(self):
        """An empty universe dict should return the neutral fallback 0.5."""
        rd = RegimeDetector()
        score = rd.get_breadth_score({}, pd.Timestamp("2024-01-02 09:15"))
        assert score == pytest.approx(0.5)

    def test_timestamp_not_in_universe_returns_neutral(self):
        """A timestamp not present in any ticker's index returns 0.5."""
        ts = pd.to_datetime(["2024-01-02 09:15"])
        df_a = self._make_ticker_df(ts, closes=[110], emas=[100])
        universe = {"TICKER_A": df_a}
        rd = RegimeDetector()

        unknown_ts = pd.Timestamp("2024-01-03 09:15")
        score = rd.get_breadth_score(universe, unknown_ts)
        assert score == pytest.approx(0.5)
