from src.data.store import DataStore
from src.backtest_engine import BacktestEngine
import pandas as pd
from datetime import datetime, timedelta

# 1. Select 3 Distinct Profiles
TICKERS = [
    "RELIANCE.NS",   # The Stable Giant (Low Volatility)
    "ADANIENT.NS",   # The Wild Mover (High Beta)
    "TCS.NS"         # The Sector Proxy (IT Correlation)
]

def generate_tearsheet(results_df):
    if results_df.empty: return
    
    # Use Net_R now
    r_col = 'Net_R'
    
    # Ensure Net_R exists, fallback if not (legacy compat)
    if r_col not in results_df.columns:
        r_col = 'R' if 'R' in results_df.columns else None
        
    if not r_col:
        print("⚠️ No R-multiple column found.")
        return

    total_trades = len(results_df)
    win_rate = len(results_df[results_df[r_col] > 0]) / total_trades
    avg_r = results_df[r_col].mean()
    median_r = results_df[r_col].median() # ✅ Added Median
    
    # Expectancy Logic
    wins = results_df[results_df[r_col] > 0][r_col]
    losses = results_df[results_df[r_col] <= 0][r_col]
    avg_win = wins.mean() if not wins.empty else 0
    avg_loss = losses.mean() if not losses.empty else 0
    
    expectancy = (win_rate * avg_win) + ((1-win_rate) * avg_loss)
    
    print("\n" + "="*30)
    print("   RESEARCH-GRADE METRICS")
    print("="*30)
    print(f"Total Trades:      {total_trades}")
    print(f"Win Rate:          {win_rate*100:.1f}%")
    print(f"Expectancy (Net):  {expectancy:.2f} R / trade")
    print(f"Mean R:            {avg_r:.2f} R")
    print(f"Median R:          {median_r:.2f} R")
    
    # Profit Factor
    wins = results_df[results_df[r_col] > 0][r_col]
    losses = results_df[results_df[r_col] <= 0][r_col]
    
    total_wins = wins.sum()
    total_losses = abs(losses.sum())
    pf = total_wins / total_losses if total_losses > 0 else 999.0
    
    print(f"Profit Factor:     {pf:.2f}")
    
    print(f"Avg Win:           {avg_win:.2f} R")
    print(f"Avg Loss:          {avg_loss:.2f} R")
    print("="*30 + "\n")

def run_certification():
    store = DataStore()
    
    # Step 1: Ingest Data (Force Update)
    print("📥 Ingesting Certification Data...")
    store.update_ticker("^NSEI") # The Benchmark
    for t in TICKERS:
        store.update_ticker(t)
        
    # Step 2: Run Backtest
    all_results = []
    
    # Calculate 59 days ago to respect yfinance intraday limits
    start_date = (datetime.now() - timedelta(days=59)).strftime("%Y-%m-%d")
    print(f"\n⚠️ Note: Backtest start_date dynamically clamped to {start_date} due to yfinance 60-day 5m data limit.")
    
    engine = BacktestEngine(start_date=start_date)
    
    for t in TICKERS:
        print(f"\n🔬 Testing {t}...")
        df = engine.run(t)
        if not df.empty:
            all_results.append(df)
            
    # Step 3: Global Report
    if all_results:
        final_df = pd.concat(all_results)
        final_df.to_csv("certification_results.csv", index=False, encoding="utf-8")
        print("\n✅ CERTIFICATION COMPLETE. Results saved to 'certification_results.csv'")
        print("\n📊 Summary Stats:")
        if 'Net_R' in final_df.columns:
             generate_tearsheet(final_df)
             print(f"Total Net R: {final_df['Net_R'].sum()}")
        elif 'R' in final_df.columns:
             print(final_df['R'].describe())
             print(f"Total R: {final_df['R'].sum()}")
        else:
             print(final_df.groupby('ticker')['kill_score'].count())
    else:
        print("\n⚠️ Simulation Complete. No signals found matching criteria (Kill Score >= 6.0).")
        print("This is possible if current market conditions or data window do not match high-conviction setup.")

if __name__ == "__main__":
    run_certification()
