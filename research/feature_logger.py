"""
Feature Logger for Alpha Research Phase 2.
Runs a loose simulation (Kill Score >= 4.0) to capture many trades.
collects their entry features and exit PnL (Net R) to training data.
"""

import sys
import os
import pandas as pd
from pathlib import Path

# Ensure project root is in path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(project_root)

from src.backtest_engine import BacktestEngine
from data.midcap_universe import MIDCAP_TICKERS

def collect_data():
    print(f"🔬 FEATURE COLLECTION (Phase 4)")
    print(f"   Universe: {len(MIDCAP_TICKERS)} Midcap Tickers")
    print("   Mode: Research (Kill Threshold = 0.0, No Constraints)")
    
    # 1. Initialize Engine
    # Note: We use a loose threshold of 0.0 to capture ALL signals for analysis
    engine = BacktestEngine(start_date="2023-01-01", kill_threshold=0.0)
    
    all_trades = []
    
    # 2. Loop Universe
    for ticker in MIDCAP_TICKERS:
        try:
            print(f"   Mining {ticker}...", end="\r")
            df = engine.run(ticker)
            if not df.empty:
                all_trades.append(df)
        except Exception as e:
            print(f"   ❌ Error {ticker}: {e}")

    if not all_trades:
        print("\n❌ No trades found across universe.")
        return

    # 3. Concatenate & Save
    final_df = pd.concat(all_trades, ignore_index=True)
    
    output_dir = Path("research/data")
    output_dir.mkdir(exist_ok=True, parents=True)
    
    output_path = output_dir / "midcap_features.csv"
    final_df.to_csv(output_path, index=False, encoding="utf-8")
    
    print(f"\n✅ MINING COMPLETE. Saved {len(final_df)} samples to {output_path}")
    
    # Preview
    print("\nSample Data:")
    cols = ['Net_R', 'kill_score', 'rs_score', 'rs_slope', 'vol_ratio', 'dist_ema', 'candle_range_atr']
    available_cols = [c for c in cols if c in final_df.columns]
    print(final_df[available_cols].head().to_string(index=False))

if __name__ == "__main__":
    collect_data()
