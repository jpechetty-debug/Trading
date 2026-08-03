from enum import Enum
from typing import TypedDict, Literal


class Regime(TypedDict):
    trend: Literal["UP", "SIDEWAYS", "DOWN"]
    volatility: Literal["LOW", "MEDIUM", "HIGH"]


class BrainAOutput(TypedDict):
    probability: float
    regime: Regime
    veto: bool
    reason: str


class Action(Enum):
    NO_TRADE = 0
    ENTER_SMALL = 1
    ENTER_FULL = 2
    REDUCE = 3
    EXIT = 4


class BrainBOutput(TypedDict):
    action: int
    rationale: str
    risk_adjustment: Literal["increase", "decrease", "neutral"]
    confidence: float
