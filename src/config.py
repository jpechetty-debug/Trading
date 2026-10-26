# src/config.py
import math
from numbers import Real, Integral

# --- System Configuration ---
SYSTEM_VERSION = "8.0"
RISK_PER_TRADE = 10000  # INR (Base risk for sizing)
MAX_TRADE_RISK_PCT = 0.01       # Stop-distance risk ceiling per trade / current equity
MAX_PORTFOLIO_RISK_PCT = 0.05   # Aggregate initial stop-distance risk / current equity
MAX_POSITION_NOTIONAL_PCT = 0.20 # Entry notional plus fee reserve / current equity
TARGET_PORTFOLIO_VOL = 0.12 # 12% Annualized
DATA_MODE = "HISTORICAL"  # "HISTORICAL" or "LIVE"
CANDLE_INTERVAL_SECONDS = 300  # 5 minutes

# --- Strategy Parameters (Brain A) ---
RS_LOOKBACK = 12
RS_SWEET_SPOT_LOW = 0.1
RS_SWEET_SPOT_HIGH = 0.45 # Tuned from 0.41
# Extension penalty activates above the sweet spot ceiling
RS_EXTENSION_THRESHOLD = 0.45
RS_SLOPE_THRESHOLD = 0.05

# --- Execution Parameters ---
STOP_LOSS_MULTIPLIER = 1.5
TARGET_MULTIPLIER = 4.0
SLIPPAGE_PCT = 0.0005  # 0.05%

# --- V7.0 Advanced Filters ---
BREADTH_THRESHOLD = 0.35       # Veto longs if <35% stocks above 50-EMA
MIN_BREADTH_COVERAGE = 0.8      # Fraction of requested symbols with current usable bars
GAP_VETO_MULT = 1.5            # Veto if overnight gap > 1.5x ATR
CONVICTION_MIN_MULT = 0.75     # Min scalar for dynamic sizing
CONVICTION_MAX_MULT = 1.25     # Max scalar for dynamic sizing
MAX_DD_THRESHOLD = 0.15        # Portfolio max drawdown for scaling

# --- Position Lifecycle ---
TRAILING_STOP_ACTIVATION_R = 2.0   # Activate trailing stop at +2R profit
TRAILING_STOP_LOCK_R = 1.0         # Lock profit at entry + 1R once trailing activates
MAX_HOLD_BARS = 50                 # ~4 hours at 5min candles, auto-exit stale positions
EOD_FLATTEN_HOUR = 15              # IST hour to force-flatten before the regular close
EOD_FLATTEN_MINUTE = 15            # IST minute to force-flatten

# --- Scoring Weights ---
SCORE_STRUCTURE = 2
SCORE_SWEET_SPOT = 4
SCORE_PATTERNS = 2
SCORE_CONTEXT = 1
PENALTY_EXTENSION = -3
PENALTY_LIQUIDITY = -5
KILL_SCORE_THRESHOLD = 6.0 # V6.5 Institutional Standard
ALERT_KILL_SCORE_THRESHOLD = 7.0  # Headless polling alerts; separate from scan threshold

# --- Data Parameters ---
MIN_AVG_5M_VOLUME = 500_000     # Shares per five-minute bar, averaged over 20 bars
MIN_AVG_5M_TURNOVER = 20_000_000 # INR per five-minute bar: ₹2 crore, not daily ADV


# --- Config Validation ---
def validate_config():
    """Fail explicitly, including under python -O and for NaN/infinity."""
    def require(condition, message):
        if not condition:
            raise ValueError(message)

    numeric = (
        'RISK_PER_TRADE', 'TARGET_PORTFOLIO_VOL', 'MAX_DD_THRESHOLD',
        'MAX_TRADE_RISK_PCT', 'MAX_PORTFOLIO_RISK_PCT', 'BREADTH_THRESHOLD', 'MIN_BREADTH_COVERAGE',
        'KILL_SCORE_THRESHOLD', 'CONVICTION_MIN_MULT', 'CONVICTION_MAX_MULT',
        'STOP_LOSS_MULTIPLIER', 'TARGET_MULTIPLIER', 'SLIPPAGE_PCT', 'GAP_VETO_MULT',
        'RS_SWEET_SPOT_LOW', 'RS_SWEET_SPOT_HIGH', 'TRAILING_STOP_ACTIVATION_R',
        'TRAILING_STOP_LOCK_R', 'MIN_AVG_5M_VOLUME', 'MIN_AVG_5M_TURNOVER',
        'ALERT_KILL_SCORE_THRESHOLD', 'MAX_POSITION_NOTIONAL_PCT',
    )
    for name in numeric:
        value = globals()[name]
        require(isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value),
                f'{name} must be a finite number')
    for name in ('MAX_DD_THRESHOLD', 'BREADTH_THRESHOLD', 'MAX_TRADE_RISK_PCT', 'MAX_PORTFOLIO_RISK_PCT'):
        require(0 < globals()[name] < 1, f'{name} must be in (0, 1)')
    require(MAX_TRADE_RISK_PCT <= MAX_PORTFOLIO_RISK_PCT, 'Trade risk cap exceeds portfolio risk cap')
    require(0 < MIN_BREADTH_COVERAGE <= 1, 'MIN_BREADTH_COVERAGE must be in (0, 1]')
    require(0 < MAX_POSITION_NOTIONAL_PCT <= 1, 'MAX_POSITION_NOTIONAL_PCT must be in (0, 1]')
    require(0 <= KILL_SCORE_THRESHOLD < 10, 'KILL_SCORE_THRESHOLD must be in [0, 10)')
    require(KILL_SCORE_THRESHOLD <= ALERT_KILL_SCORE_THRESHOLD <= 10,
            'Alert threshold must be between scan threshold and 10')
    require(MIN_AVG_5M_VOLUME >= 0 and MIN_AVG_5M_TURNOVER >= 0,
            'Five-minute liquidity floors must be nonnegative')
    require(0 < CONVICTION_MIN_MULT <= CONVICTION_MAX_MULT, 'Invalid conviction bounds')
    for name in ('RISK_PER_TRADE', 'TARGET_PORTFOLIO_VOL', 'STOP_LOSS_MULTIPLIER',
                 'TARGET_MULTIPLIER', 'GAP_VETO_MULT'):
        require(globals()[name] > 0, f'{name} must be positive')
    require(0 <= SLIPPAGE_PCT < 1, 'SLIPPAGE_PCT must be in [0, 1)')
    require(RS_SWEET_SPOT_LOW < RS_SWEET_SPOT_HIGH, 'Invalid RS sweet spot bounds')
    require(0 <= TRAILING_STOP_LOCK_R < TRAILING_STOP_ACTIVATION_R, 'Invalid trailing stop thresholds')
    for name in ('RS_LOOKBACK', 'MAX_HOLD_BARS', 'CANDLE_INTERVAL_SECONDS'):
        value = globals()[name]
        require(isinstance(value, Integral) and not isinstance(value, bool) and value > 0,
                f'{name} must be a positive integer')
    require(CANDLE_INTERVAL_SECONDS == 300, 'This strategy requires 300-second candles')
    require(isinstance(EOD_FLATTEN_HOUR, Integral) and isinstance(EOD_FLATTEN_MINUTE, Integral)
            and 0 <= EOD_FLATTEN_MINUTE < 60
            and (9, 15) < (EOD_FLATTEN_HOUR, EOD_FLATTEN_MINUTE) <= (15, 30),
            'EOD flatten time must be within the trading session')


# Run validation on import
validate_config()
