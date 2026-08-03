"""
ML Feature Analyzer
Trains a Decision Tree to find the "Golden Rules" for Alpha.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.tree import DecisionTreeRegressor, export_text
from sklearn.model_selection import train_test_split

def analyze_ml():
    data_path = Path("research/data/midcap_features.csv")
    if not data_path.exists():
        print(f"❌ Data file not found: {data_path}")
        return

    df = pd.read_csv(data_path)
    print(f"📊 Loaded {len(df)} samples.")
    
    # Target: We want Net R > 0.5 (Solid Wins)
    # Actually, let's regress on Net_R directly to find max expectancy
    
    # Drops NaNs
    df = df.dropna()
    
    feature_cols = [
        'kill_score', 'rs_score', 'rs_slope', 
        'atr_val', 'vol_ratio', 'dist_ema', 'candle_range_atr'
    ]
    # Filter text columns/missing
    feature_cols = [c for c in feature_cols if c in df.columns]
    
    X = df[feature_cols]
    y = df['Net_R']
    
    print(f"🧠 Training Decision Tree on {len(X)} samples with features: {feature_cols}")
    
    # Limit depth to keep it human-readable (we need to code these rules)
    model = DecisionTreeRegressor(max_depth=3, min_samples_leaf=10)
    model.fit(X, y)
    
    # 1. Feature Importance
    print("\n🏆 Feature Importance:")
    importances = dict(zip(feature_cols, model.feature_importances_))
    for k, v in sorted(importances.items(), key=lambda item: item[1], reverse=True):
        print(f"   {k}: {v:.4f}")
        
    # 2. Extract Rules
    print("\n📜 Decision Rules (Tree Structure):")
    tree_rules = export_text(model, feature_names=feature_cols)
    print(tree_rules)
    
    with open("research/data/ml_rules.txt", "w", encoding="utf-8") as f:
        f.write("Feature Importance:\n")
        for k, v in sorted(importances.items(), key=lambda item: item[1], reverse=True):
            f.write(f"{k}: {v:.4f}\n")
        f.write("\nDecision Tree Rules:\n")
        f.write(tree_rules)
    print("✅ Rules saved to research/data/ml_rules.txt")
    
    # 3. Simulate "Best Leaf"
    # (Simple logic: find leaf with highest mean value)
    # For now, just printing the tree is enough for manual "Insight Extraction"

if __name__ == "__main__":
    analyze_ml()
