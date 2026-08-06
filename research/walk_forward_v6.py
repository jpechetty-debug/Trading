import pandas as pd
import sys
import os
from datetime import timedelta

# Ensure src is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.backtest_engine import BacktestEngine
from src.data.universe import NIFTY_200

class WalkForwardValidator:
    def __init__(self, tickers, window_days=15, step_days=5):
        self.tickers = tickers
        self.window_days = window_days
        self.step_days = step_days

    def run_validation(self):
        print(f"🔬 Starting Walk-Forward Validation (Rolling OOS)")
        print(f"Tickers: {self.tickers}")
        print(f"Window: {self.window_days} days | Step: {self.step_days} days")
        print("-" * 60)

        # We assume ~59 days of history is available in storage
        # Start date will be most recent - 59 days
        end_date = pd.Timestamp.now().normalize()
        start_date = end_date - timedelta(days=59)

        current_test_start = start_date + timedelta(days=self.window_days)
        
        all_oos_results = []
        
        while current_test_start < end_date:
            test_end = current_test_start + timedelta(days=self.step_days)
            if test_end > end_date: test_end = end_date
            
            print(f"\n📅 TESTING WINDOW: {current_test_start.date()} to {test_end.date()}")
            
            for ticker in self.tickers:
                # We initialize engine with current_test_start
                # The engine will use history BEFORE this for warmup
                engine = BacktestEngine(start_date=current_test_start.strftime("%Y-%m-%d"), research_mode=True)
                
                # We need to cap the engine's end date (not supported in BacktestEngine.run directly)
                # But we can pass it via data manipulation or just run and filter
                results_df = engine.run(ticker)
                
                if not results_df.empty:
                    # Filter for trades that EXITED within the test window
                    oos_trades = results_df[results_df['exit_time'] <= test_end]
                    if not oos_trades.empty:
                        all_oos_results.append(oos_trades)
                        print(f"   {ticker}: {len(oos_trades)} OOS trades found.")

            current_test_start += timedelta(days=self.step_days)

        if all_oos_results:
            final_df = pd.concat(all_oos_results)
            print("\n" + "=" * 60)
            print("WALK-FORWARD OOS RESULTS SUMMARY")
            print("=" * 60)
            total_r = final_df['Net_R'].sum()
            win_rate = (len(final_df[final_df['Net_R'] > 0]) / len(final_df)) * 100
            expectancy = final_df['Net_R'].mean()
            
            print(f"Total OOS Trades: {len(final_df)}")
            print(f"Total Net R:      {total_r:.2f}")
            print(f"Win Rate:          {win_rate:.1f}%")
            print(f"Expectancy:       {expectancy:.2f} R")
            print("=" * 60)
            
            final_df.to_csv("research/walk_forward_results.csv", index=False)
            print("Full OOS log saved to research/walk_forward_results.csv")
        else:
            print("\n⚠️ No OOS trades generated during validation period.")
            pd.DataFrame().to_csv("research/walk_forward_results.csv", index=False)

if __name__ == "__main__":
    # Test on a few tickers to keep it reasonably fast
    validator = WalkForwardValidator(tickers=["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS"])
    validator.run_validation()
