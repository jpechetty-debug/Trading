"""
Institutional Portfolio Simulation (V7.0)
Scales the selectively validated Alpha Engine to the full Nifty 200 universe.
"""

import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
import os
import pandas as pd
from pathlib import Path

# Ensure project root is in path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(project_root)

from src.backtest.portfolio_engine import PortfolioEngine

def run_simulation():
    # 1. Define Universe
    # Load Nifty 200 list (assuming csv or hardcoded top 200)
    # For this verification run, we'll use a mix of known liquid tickers
    # or load from existing list if available
    
    universe_path = Path("data/nifty_200.csv")
    if universe_path.exists():
        df = pd.read_csv(universe_path)
        tickers = df['Symbol'].tolist()
        tickers = [t + ".NS" if not t.endswith(".NS") else t for t in tickers]
    else:
        # Fallback to key sectors leaders
        tickers = [
            "RELIANCE.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS", "TCS.NS",
            "ADANIENT.NS", "SBIN.NS", "BHARTIARTL.NS", "ITC.NS", "LICI.NS",
            "SUNPHARMA.NS", "M&M.NS", "TATAMOTORS.NS", "MARUTI.NS", "ULTRACEMCO.NS",
            "POWERGRID.NS", "NTPC.NS", "ONGC.NS", "BAJFINANCE.NS", "TITAN.NS"
        ]
        print(f"⚠️ Nifty 200 list not found. Using Top {len(tickers)} Blue Chips.")

    print(f"🚀 Initializing V7.0 Institutional Engine for {len(tickers)} Tickers...")
    
    # Initialize Engine (Strict 6.0, No Research Mode)
    engine = PortfolioEngine(
        tickers=tickers,
        start_date="2024-01-01",
        initial_equity=1000000.0, # 10 Lakh Capital
        kill_threshold=6.0,
        
    )
    
    # Run
    results_df = engine.run()
    
    # Save Report
    if not results_df.empty:
        results_df.to_csv("research/data/v7_simulation_results.csv", index=False, encoding="utf-8")
        print("✅ Results saved to research/data/v7_simulation_results.csv")

if __name__ == "__main__":
    run_simulation()
