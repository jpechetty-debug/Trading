import pandas as pd
import sys
import os

# Ensure src is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.backtest_engine import BacktestEngine
from src.data.universe import NIFTY_200

def run_sensitivity_analysis():
    # Test group
    tickers = ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS"]
    thresholds = [5.0, 5.5, 6.0, 6.5]
    
    summary_results = []
    
    print("🚀 Starting Sensitivity Analysis: Net R vs Kill Score Threshold")
    print("-" * 60)
    
    for threshold in thresholds:
        print(f"\nTesting Threshold: {threshold}")
        total_net_r = 0
        total_trades = 0
        wins = 0
        
        for ticker in tickers:
            engine = BacktestEngine(start_date="2024-01-01", kill_threshold=threshold)
            results_df = engine.run(ticker)
            
            if not results_df.empty:
                total_net_r += results_df['Net_R'].sum()
                total_trades += len(results_df)
                wins += len(results_df[results_df['Net_R'] > 0])
        
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        avg_r = (total_net_r / total_trades) if total_trades > 0 else 0
        
        summary_results.append({
            "Threshold": threshold,
            "Total Net R": round(total_net_r, 2),
            "Trade Count": total_trades,
            "Win Rate (%)": round(win_rate, 2),
            "Expectancy (R)": round(avg_r, 2)
        })
        
    df = pd.DataFrame(summary_results)
    print("\n" + "=" * 60)
    print("FINAL SENSITIVITY REPORT")
    print("=" * 60)
    print(df.to_string(index=False))
    print("=" * 60)
    
    # Save to file
    df.to_csv("research/sensitivity_report.csv", index=False)
    print("Report saved to research/sensitivity_report.csv")

if __name__ == "__main__":
    run_sensitivity_analysis()
