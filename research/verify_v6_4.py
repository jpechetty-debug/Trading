import pandas as pd
import sys
import os

# Ensure src is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.backtest_engine import BacktestEngine
from src.config import RISK_PER_TRADE

def run_verification():
    print("🚀 Starting Phase 6.4 Verification...")
    print(f"Risk per trade: ₹{RISK_PER_TRADE}")
    
    # We'll test with a few tickers: 
    # 1. HDFCBANK.NS (Liquid, should pass liquidity)
    # 2. TCS.NS (Liquid)
    # 3. A "test" ticker if we had one that's illiquid. 
    # For now, let's use the standard list.
    
    tickers = ["HDFCBANK.NS", "RELIANCE.NS", "TCS.NS"]
    
    engine = BacktestEngine(start_date="2025-01-01")
    
    final_results = []
    
    for ticker in tickers:
        print(f"\n--- Testing {ticker} ---")
        results = engine.run(ticker)
        
        if not results.empty:
            print(f"✅ {ticker} Results: {len(results)} trades")
            print(f"Average Net R: {results['Net_R'].mean():.2f}")
            # Check for features
            if 'resistance' in results.columns:
                print("✅ Resistance/Support data found in results.")
            final_results.append(results)
        else:
            print(f"ℹ️ {ticker}: No trades generated or all vetoed.")

    if final_results:
        all_trades = pd.concat(final_results)
        print("\n📈 Aggregate Phase 6.4 Stats:")
        print(f"Total Trades: {len(all_trades)}")
        if not all_trades.empty:
            print(f"Win Rate (Net R > 0): {len(all_trades[all_trades['Net_R'] > 0]) / len(all_trades) * 100:.1f}%")
            print(f"Total Net R: {all_trades['Net_R'].sum():.2f}")

if __name__ == "__main__":
    run_verification()
