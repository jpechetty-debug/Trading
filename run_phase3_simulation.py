"""
Phase 3 Simulation Runner: High-Beta Adaptive Strategy
"""

import sys
import os
import pandas as pd
from pathlib import Path

# Ensure project root is in path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(project_root)

from src.backtest.portfolio_engine import PortfolioEngine
from data.midcap_universe import MIDCAP_TICKERS

def run_phase3():
    print(f"🌪️ Phase 3: High-Beta Adaptive Simulation")
    print(f"   Universe: {len(MIDCAP_TICKERS)} Midcap/Momentum Tickers")
    print(f"   Strategy: Adaptive (Bull=4.0, Bear/Neut=6.0)")
    
    # Initialize Engine in Adaptive Mode
    engine = PortfolioEngine(
        tickers=MIDCAP_TICKERS,
        start_date="2024-01-01",
        initial_equity=1000000.0, # 10 Lakhs
        kill_threshold=6.0,       # Base threshold (overridden by adaptive_mode)
        adaptive_mode=True        # ✅ ENABLE ADAPTIVE LOGIC
    )
    
    # Run
    results_df = engine.run()
    
    # Save Report
    if not results_df.empty:
        output_path = "research/data/phase3_results.csv"
        results_df.to_csv(output_path, index=False, encoding="utf-8")
        print(f"✅ Results saved to {output_path}")
        
        # Quick Stats
        win_rate = len(results_df[results_df['Net_R'] > 0]) / len(results_df) * 100
        print(f"📊 Win Rate: {win_rate:.1f}%")
        print(f"💰 Avg Net R: {results_df['Net_R'].mean():.2f}")

if __name__ == "__main__":
    run_phase3()
