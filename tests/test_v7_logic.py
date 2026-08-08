import pytest
import pandas as pd
import numpy as np
from datetime import datetime
from src.execution.model import ExecutionModel
from src.regime import RegimeDetector
from src.backtest.position import Position
from src.backtest.portfolio_engine import PortfolioEngine
import src.config as config

class TestV7DynamicSizing:
    """Tests for Conviction, Drawdown, and Volatility scalars."""

    def test_conviction_scalar(self):
        model = ExecutionModel(base_risk_inr=10000)
        # Score 6.0 (Min conviction) -> 0.75x = 7500
        risk_6 = model.calculate_dynamic_risk(kill_score=6.0, portfolio_vol=0.12/(252**0.5), current_dd=0.0)
        assert risk_6 == 7500.0
        
        # Score 10.0 (Max conviction) -> 1.25x = 12500
        risk_10 = model.calculate_dynamic_risk(kill_score=10.0, portfolio_vol=0.12/(252**0.5), current_dd=0.0)
        assert risk_10 == 12500.0

    def test_drawdown_scalar(self):
        model = ExecutionModel(base_risk_inr=10000)
        # Score 8.0 (1.0x)
        # Max DD 15% -> 50% risk reduction (0.5x scalar)
        risk_full_dd = model.calculate_dynamic_risk(kill_score=8.0, portfolio_vol=0.12/(252**0.5), current_dd=0.15)
        assert risk_full_dd == 5000.0
        
        risk_half_dd = model.calculate_dynamic_risk(kill_score=8.0, portfolio_vol=0.12/(252**0.5), current_dd=0.075)
        # 1.0 - (0.075/0.15)*0.5 = 0.75
        assert risk_half_dd == 7500.0

    def test_volatility_scalar(self):
        model = ExecutionModel(base_risk_inr=10000)
        # Targeted 12%, current realized 24% -> 0.5x scalar
        risk_high_vol = model.calculate_dynamic_risk(kill_score=8.0, portfolio_vol=0.24/(252**0.5), current_dd=0.0)
        assert risk_high_vol == 5000.0
        
        # Targeted 12%, current 6% -> Cap at 1.5x scalar
        risk_low_vol = model.calculate_dynamic_risk(kill_score=8.0, portfolio_vol=0.06/(252**0.5), current_dd=0.0)
        assert risk_low_vol == 15000.0

class TestV7BreadthFilter:
    """Tests for Market Breadth detection."""

    def test_breadth_score_calc(self):
        detector = RegimeDetector()
        universe = {
            "S1": pd.DataFrame({'Close': [110, 120], 'EMA_50': [100, 100]}, index=[datetime(2024,1,1), datetime(2024,1,2)]),
            "S2": pd.DataFrame({'Close': [90, 80], 'EMA_50': [100, 100]}, index=[datetime(2024,1,1), datetime(2024,1,2)]),
            "S3": pd.DataFrame({'Close': [105, 110], 'EMA_50': [100, 100]}, index=[datetime(2024,1,1), datetime(2024,1,2)]),
        }
        score = detector.get_breadth_score(universe, datetime(2024,1,1))
        # S1, S3 above EMA_50 -> 2/3 = 0.66
        assert round(score, 2) == 0.67

    def test_breadth_veto(self):
        detector = RegimeDetector()
        # Breadth 20% < 35% threshold
        state = detector.detect(nifty_close=22000, nifty_ema_primary=21000, nifty_ema_gatekeeper=21500, breadth_score=0.20)
        assert state['regime'] == "Bullish"
        assert not state['longs_authorized'], "Should be vetoed by breadth"

class TestV7StructuralExits:
    """Tests for RS deterioration, EMA break, and Vol collapse."""

    def test_structural_exit_rs(self):
        pos = Position("T", "A.NS", "LONG", 100, 90, 120, 5, 100, features={'atr_val': 5})
        pos.entry_time = datetime(2024,1,1,10,0)
        
        # Bar with low RS
        bar = {'Close': 105, 'RS_Score': 0.04, 'EMA_20': 100, 'ATR': 5, 'Open': 105, 'High': 106, 'Low': 104}
        pos.update(bar, datetime(2024,1,1,10,5))
        assert not pos.open
        assert pos.exit_reason == "RS_DETERIORATION"

    def test_structural_exit_structure_break(self):
        pos = Position("T", "A.NS", "LONG", 100, 90, 120, 5, 100, features={'atr_val': 5})
        # Price 98 < EMA 100
        bar = {'Close': 98, 'RS_Score': 0.5, 'EMA_20': 100, 'ATR': 5, 'Open': 99, 'High': 100, 'Low': 97}
        pos.update(bar, datetime(2024,1,1,10,5))
        assert not pos.open
        assert pos.exit_reason == "STRUCTURE_BREAK"

    def test_structural_exit_vol_collapse(self):
        pos = Position("T", "A.NS", "LONG", 100, 90, 120, 5, 100, features={'atr_val': 5})
        # Current ATR 2 < 0.5 * Entry ATR 5 (2.5)
        bar = {'Close': 105, 'RS_Score': 0.5, 'EMA_20': 100, 'ATR': 2, 'Open': 105, 'High': 106, 'Low': 104}
        pos.update(bar, datetime(2024,1,1,10,5))
        assert not pos.open
        assert pos.exit_reason == "VOL_COLLAPSE"

class TestV7GapRisk:
    """Tests for overnight gap veto."""

    def test_gap_risk_veto(self):
        # We need a partial engine setup to test _check_gap_risk
        engine = PortfolioEngine(tickers=["A.NS"])
        df = pd.DataFrame({
            'Close': [100.0, 110.0],
            'Open': [101.0, 120.0], # Gap between 110 and 120
            'High': [102.0, 121.0],
            'Low': [99.0, 119.0],
            'ATR': [5.0, 5.0]
        }, index=[datetime(2024,1,1), datetime(2024,1,2)])
        engine.data_map["A.NS"] = df
        
        # Gap = abs(120 - 110) = 10. 10 / 5 ATR = 2.0x. 2.0x > 1.5x threshold.
        is_vetoed = engine._check_gap_risk("A.NS", datetime(2024,1,2), df.loc[datetime(2024,1,2)])
        assert is_vetoed
