# src/models.py
"""
Type-safe data models for Indian Stock AI V6.5.
Replaces raw dict passing with dataclasses for IDE support,
autocompletion, and compile-time safety.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Literal
from datetime import datetime


@dataclass
class Features:
    """Extracted feature data from Brain A, carried through the trade lifecycle."""
    # V7.0 Advanced
    breadth_score: float = 0.0
    rs_score: float = 0.0
    rs_slope: float = 0.0
    atr_val: float = 0.0
    vol_ratio: float = 0.0
    vol_ma_20: float = 0.0       # ADV for non-linear slippage model
    dist_ema: float = 0.0
    candle_range_atr: float = 0.0
    resistance: float = 0.0
    support: float = 0.0
    market_regime: str = "Neutral"

    def to_dict(self) -> dict:
        """Convert to dict for backward compatibility and DataFrame export."""
        return {
            "rs_score": self.rs_score,
            "rs_slope": self.rs_slope,
            "atr_val": self.atr_val,
            "vol_ratio": self.vol_ratio,
            "vol_ma_20": self.vol_ma_20,
            "dist_ema": self.dist_ema,
            "candle_range_atr": self.candle_range_atr,
            "resistance": self.resistance,
            "support": self.support,
            "market_regime": self.market_regime,
        }



@dataclass
class Order:
    """Output from ExecutionModel — sized and validated trade parameters."""
    entry: float
    stop: float
    target: float
    shares: int
    risk_reward: float
    valid_rr: bool

    def to_dict(self) -> dict:
        return {
            "entry": self.entry,
            "stop": self.stop,
            "target": self.target,
            "shares": self.shares,
            "risk_reward": self.risk_reward,
            "valid_rr": self.valid_rr,
        }


