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
