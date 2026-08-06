"""
Walk-Forward Optimization Engine
Prevents overfitting through temporal validation.
"""

import pandas as pd
import numpy as np
from itertools import product
from pathlib import Path
import sys
import os

# Ensure project root is in path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.append(project_root)

from src.backtest_engine import BacktestEngine
from research.wfo.parameter_grid import PARAM_GRID, validate_params

class WFOEngine:
    def __init__(self):
        self.train_start = "2023-01-01" # Shifted forward to have data
        self.train_end = "2024-06-30"   # ~1.5 Years Train
        self.val_start = "2024-07-01"
        self.val_end = "2024-12-31"     # 6 Months Validation (Data availability constraint?)
        
        # Ensure dates are compatible with typical yfinance history
        # If user has only 60d data (intraday), specific logic is needed.
        # However, DataStore might have fetched 59d.
        # WFO usually requires long history.
        # For now, we will use the available window in DataStore.
        
        # Acceptance criteria
        self.min_train_expectancy = 0.05
        self.min_val_expectancy = 0.05
        self.min_profit_factor = 1.2
        self.min_trades = 10
    
    def generate_configs(self):
        """Create all parameter combinations."""
        keys = PARAM_GRID.keys()
        values = PARAM_GRID.values()
        
        configs = []
        for combo in product(*values):
            params = dict(zip(keys, combo))
            
            # Apply constraints
            if validate_params(params):
                configs.append(params)
        
        print(f"✅ Generated {len(configs)} valid configurations")
        return configs
    
    def run_backtest_with_params(self, ticker, params, period):
        """Run backtest with specific parameters."""
        engine = BacktestEngine(start_date=period['start'])
        
        # Inject parameters
        engine.brain.rs_lookback = params['rs_lookback']
        engine.exec_model.stop_mult = params['atr_stop_mult']
        engine.exec_model.target_mult = params['atr_target_mult']
        engine.kill_threshold = params['kill_threshold']
        
        # Run simulation
        results_df = engine.run(ticker)
        
        if results_df.empty:
            return self.calculate_metrics(results_df)

        # Filter to period
        # Ensure timestamp column availability
        if 'exit_time' in results_df.columns:
            results_df['exit_time'] = pd.to_datetime(results_df['exit_time'])
            results_df = results_df[
                (results_df['exit_time'] >= period['start']) &
                (results_df['exit_time'] <= period['end'])
            ]
        
        return self.calculate_metrics(results_df)
    
    def calculate_metrics(self, results_df):
        """Extract key performance metrics."""
        if len(results_df) == 0:
            return {
                'total_trades': 0,
                'expectancy': 0.0,
                'profit_factor': 0.0,
                'win_rate': 0.0,
                'avg_win': 0.0,
                'avg_loss': 0.0,
                'median_r': 0.0
            }
        
        total_trades = len(results_df)
        r_col = 'Net_R'
        
        wins = results_df[results_df[r_col] > 0]
        losses = results_df[results_df[r_col] <= 0]
        
        win_rate = len(wins) / total_trades
        avg_win = wins[r_col].mean() if len(wins) > 0 else 0
        avg_loss = losses[r_col].mean() if len(losses) > 0 else 0
        
        expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)
        
        total_wins = wins[r_col].sum()
        total_losses = abs(losses[r_col].sum())
        profit_factor = total_wins / total_losses if total_losses > 0 else float('inf')
        
        return {
            'total_trades': total_trades,
            'expectancy': round(expectancy, 3),
            'profit_factor': round(profit_factor, 2),
            'win_rate': round(win_rate, 3),
            'avg_win': round(avg_win, 2),
            'avg_loss': round(avg_loss, 2),
            'median_r': round(results_df[r_col].median(), 2)
        }
    
    def run_wfo(self, ticker="RELIANCE.NS"):
        """Execute full WFO workflow."""
        print(f"\n{'='*60}")
        print(f"🔬 WALK-FORWARD OPTIMIZATION: {ticker}")
        print(f"{'='*60}\n")
        
        configs = self.generate_configs()
        results = []
        
        # Auto-detect available date range from DataStore to prevent empty backtests
        # Since we rely on Yahoo 60d limit, we must adjust start/end dynamically
        try:
            from src.data.store import DataStore
            ds = DataStore()
            df = ds.load_ticker(ticker)
            if df is not None:
                min_date = df.index.min().tz_localize(None)
                max_date = df.index.max().tz_localize(None)
                print(f"📅 Available Data: {min_date} to {max_date}")
                
                # Split roughly 70/30
                total_days = (max_date - min_date).days
                split_point = min_date + pd.Timedelta(days=int(total_days * 0.7))
                
                self.train_start = min_date.strftime("%Y-%m-%d")
                self.train_end = split_point.strftime("%Y-%m-%d")
                self.val_start = (split_point + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                self.val_end = max_date.strftime("%Y-%m-%d")
                
                print(f"🔄 Dynamic Split: Train[{self.train_start} : {self.train_end}] | Val[{self.val_start} : {self.val_end}]")
        except Exception as e:
            print(f"⚠️ Could not detect dates: {e}")

        
        for i, params in enumerate(configs, 1):
            # print(f"[{i}/{len(configs)}] Testing: {params}") # Too verbose
            sys.stdout.write(f"\r[{i}/{len(configs)}] Processing...")
            sys.stdout.flush()
            
            # Train period
            train_metrics = self.run_backtest_with_params(
                ticker, 
                params,
                {'start': self.train_start, 'end': self.train_end}
            )
            print(f"   [Train] Trades: {train_metrics['total_trades']} | Exp: {train_metrics['expectancy']}")
            
            # Only validate if train passes
            if (train_metrics['expectancy'] >= self.min_train_expectancy and
                train_metrics['total_trades'] >= self.min_trades):
                
                # Validation period
                val_metrics = self.run_backtest_with_params(
                    ticker,
                    params,
                    {'start': self.val_start, 'end': self.val_end}
                )
                
                # Check if config survives
                if (val_metrics['expectancy'] >= self.min_val_expectancy and
                    val_metrics['profit_factor'] >= self.min_profit_factor):
                    
                    result = {
                        **params,
                        'train_exp': train_metrics['expectancy'],
                        'train_pf': train_metrics['profit_factor'],
                        'train_trades': train_metrics['total_trades'],
                        'val_exp': val_metrics['expectancy'],
                        'val_pf': val_metrics['profit_factor'],
                        'val_trades': val_metrics['total_trades'],
                        'exp_degradation': train_metrics['expectancy'] - val_metrics['expectancy'],
                        'status': '✅ PASSED'
                    }
                    results.append(result)
            else:
                pass 
        
        print("\n")
        
        # Save results
        if not results:
             print("❌ No configurations passed the strict WFO criteria.")
             output_path = Path('research/wfo/results/wfo_results.csv')
             pd.DataFrame().to_csv(output_path, index=False)
             return pd.DataFrame()

        results_df = pd.DataFrame(results)
        results_df = results_df.sort_values('val_exp', ascending=False)
        
        output_path = Path('research/wfo/results/wfo_results.csv')
        results_df.to_csv(output_path, index=False)
        
        print(f"\n{'='*60}")
        print(f"📊 WFO COMPLETE")
        print(f"{'='*60}")
        print(f"Configs Tested: {len(configs)}")
        print(f"Configs Passed: {len(results)}")
        print(f"Success Rate:   {len(results)/len(configs)*100:.1f}%")
        print(f"\nResults saved: {output_path}")
        
        if len(results) > 0:
            print(f"\n🏆 TOP 3 CONFIGURATIONS:")
            print(results_df[['rs_lookback', 'atr_stop_mult', 'atr_target_mult', 
                             'kill_threshold', 'train_exp', 'val_exp']].head(3).to_string(index=False))
        
        return results_df


if __name__ == "__main__":
    import sys
    ticker = "RELIANCE.NS"
    if len(sys.argv) > 1:
        ticker = sys.argv[1]
        
    wfo = WFOEngine()
    wfo.run_wfo(ticker)
