"""
Feature Analysis Tool
Statistically validates which Kill Score components predict profit (Net R).
"""

import pandas as pd
import numpy as np
from pathlib import Path
import sys

def analyze():
    data_path = Path("research/data/trade_features.csv")
    if not data_path.exists():
        print(f"❌ Data file not found: {data_path}")
        print("   Run research/feature_logger.py first.")
        return

    df = pd.read_csv(data_path)
    print(f"📊 Loaded {len(df)} trade samples.")
    
    # Target Variable
    target = 'Net_R'
    
    # Feature Columns (Exclude metadata)
    feature_cols = [
        'kill_score', 'rs_score', 'rs_slope', 
        'atr_val', 'vol_ratio', 'dist_ema', 'candle_range_atr'
    ]
    
    # Filter available columns
    feature_cols = [c for c in feature_cols if c in df.columns]
    
    print(f"\n🔬 Analyzing Correlation with {target}...")
    print("-" * 50)
    
    results = []
    for feat in feature_cols:
        # Pearson Correlation (Linear)
        corr = df[feat].corr(df[target])
        
        # Win Rate in High vs Low bins
        median_val = df[feat].median()
        high_group = df[df[feat] > median_val]
        low_group = df[df[feat] <= median_val]
        
        win_rate_high = len(high_group[high_group[target] > 0]) / len(high_group) if len(high_group) > 0 else 0
        win_rate_low = len(low_group[low_group[target] > 0]) / len(low_group) if len(low_group) > 0 else 0
        
        results.append({
            "Feature": feat,
            "Correlation": corr,
            "WR_High": win_rate_high,
            "WR_Low": win_rate_low,
            "Delta_WR": win_rate_high - win_rate_low
        })
        
    results_df = pd.DataFrame(results).sort_values("Correlation", ascending=False)
    
    print(results_df.round(3).to_string(index=False))
    print("-" * 50)
    
    # Recommendation
    best_feat = results_df.iloc[0]
    print(f"\n🏆 Best Predictor: {best_feat['Feature']} (r={best_feat['Correlation']:.3f})")
    print(f"   Win Rate Shift: {(best_feat['WR_Low']*100):.1f}% -> {(best_feat['WR_High']*100):.1f}%")

if __name__ == "__main__":
    analyze()
