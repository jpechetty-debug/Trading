"""
Conservative parameter grid for WFO.
Total combinations: 3×3×3×3 = 81 configs
Runtime estimate: ~6-8 hours for Nifty 50
"""

PARAM_GRID = {
    # Relative Strength calculation window
    "rs_lookback": [12, 16],
    
    # Stop loss as multiple of ATR
    "atr_stop_mult": [1.5],
    
    # Target as multiple of ATR
    "atr_target_mult": [2.5, 3.0],
    
    # Minimum Kill Score to trade
    "kill_threshold": [4.0]
}

# Derived constraints
PARAM_CONSTRAINTS = {
    # Target must be > 1.5x stop (minimum 1.5:1 R:R)
    "min_rr_ratio": 1.5
}

def validate_params(params):
    """Ensure target/stop ratio is acceptable."""
    rr_ratio = params['atr_target_mult'] / params['atr_stop_mult']
    return rr_ratio >= PARAM_CONSTRAINTS['min_rr_ratio']
