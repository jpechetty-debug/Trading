from enum import Enum

class Regime(Enum):
    LOW_VOL_BULL = "LowVolBull"
    HIGH_VOL_BEAR = "HighVolBear"
    # Extensible: Add SIDEWAYS, etc.

class BrainAOutput:
    def __init__(self, base_signal: float, regime: Regime, veto: bool = False, veto_reason: str = None):
        self.base_signal = base_signal  # Bayesian prob 0-1
        self.regime = regime
        self.veto = veto
        self.veto_reason = veto_reason

class Action(Enum):
    NO_TRADE = 0
    ENTER_SMALL = 1
    ENTER_FULL = 2
    REDUCE = 3
    EXIT = 4

class BrainBOutput:
    def __init__(self, action: Action, size: float, reasoning: str, entry_price: float = 0.0, stop_loss: float = 0.0, target_price: float = 0.0, sentiment_score: float = 5.0, sentiment_summary: str = "Neutral"):
        self.action = action
        self.size = size  # % allocation, 0-2.0
        self.reasoning = reasoning
        self.entry_price = entry_price
        self.stop_loss = stop_loss
        self.target_price = target_price
        self.sentiment_score = sentiment_score # 0.0 (Bearish) to 10.0 (Bullish)
        self.sentiment_summary = sentiment_summary
