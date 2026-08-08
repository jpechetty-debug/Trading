# src/config.py

# --- System Configuration ---
SYSTEM_VERSION = "7.0"
RISK_PER_TRADE = 10000  # INR (Base risk for sizing)
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
GAP_VETO_MULT = 1.5            # Veto if overnight gap > 1.5x ATR
CONVICTION_MIN_MULT = 0.75     # Min scalar for dynamic sizing
CONVICTION_MAX_MULT = 1.25     # Max scalar for dynamic sizing
MAX_DD_THRESHOLD = 0.15        # Portfolio max drawdown for scaling

# --- Position Lifecycle ---
TRAILING_STOP_ACTIVATION_R = 2.0   # Activate trailing stop at +2R profit
TRAILING_STOP_LOCK_R = 1.0         # Lock profit at entry + 1R once trailing activates
MAX_HOLD_BARS = 50                 # ~4 hours at 5min candles, auto-exit stale positions
EOD_FLATTEN_HOUR = 15              # IST hour to force-flatten (15:15 market close)
EOD_FLATTEN_MINUTE = 15            # IST minute to force-flatten

# --- Scoring Weights ---
SCORE_STRUCTURE = 2
SCORE_SWEET_SPOT = 4
SCORE_PATTERNS = 2
SCORE_CONTEXT = 1
PENALTY_EXTENSION = -3
PENALTY_LIQUIDITY = -5
KILL_SCORE_THRESHOLD = 6.0 # V6.5 Institutional Standard

# --- Data Parameters ---
MIN_LIQUIDITY_VOLUME = 10000
ADV_SHARE_FLOOR = 500_000 # Institutional Grade
ADV_TURNOVER_FLOOR = 20_000_000 # ₹20cr floor
MAX_SPREAD_PAISE = 0.10 # 10 paise spread limit


# --- Config Validation ---
def validate_config():
    """
    Assert critical invariants at startup.
    Prevents silent division-by-zero and out-of-range runtime errors.
    """
    assert 0 < MAX_DD_THRESHOLD < 1.0, f"MAX_DD_THRESHOLD must be in (0, 1), got {MAX_DD_THRESHOLD}"
    assert KILL_SCORE_THRESHOLD < 10.0, f"KILL_SCORE_THRESHOLD must be < 10.0, got {KILL_SCORE_THRESHOLD}"
    assert 0 < CONVICTION_MIN_MULT <= CONVICTION_MAX_MULT, \
        f"Conviction bounds invalid: [{CONVICTION_MIN_MULT}, {CONVICTION_MAX_MULT}]"
    assert RISK_PER_TRADE > 0, f"RISK_PER_TRADE must be positive, got {RISK_PER_TRADE}"
    assert TARGET_PORTFOLIO_VOL > 0, f"TARGET_PORTFOLIO_VOL must be positive, got {TARGET_PORTFOLIO_VOL}"
    assert STOP_LOSS_MULTIPLIER > 0, f"STOP_LOSS_MULTIPLIER must be positive, got {STOP_LOSS_MULTIPLIER}"
    assert TARGET_MULTIPLIER > 0, f"TARGET_MULTIPLIER must be positive, got {TARGET_MULTIPLIER}"
    assert 0 < BREADTH_THRESHOLD < 1.0, f"BREADTH_THRESHOLD must be in (0, 1), got {BREADTH_THRESHOLD}"
    assert RS_SWEET_SPOT_LOW < RS_SWEET_SPOT_HIGH, \
        f"RS sweet spot range invalid: [{RS_SWEET_SPOT_LOW}, {RS_SWEET_SPOT_HIGH}]"


# Run validation on import
validate_config()
