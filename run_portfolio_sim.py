import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from src.backtest.portfolio_engine import PortfolioEngine
from src.data.store import DataStore
from src.data.universe import NIFTY_200
from datetime import datetime, timedelta

# --- 1. Define Universe ---
# Default to a smaller subset for quick testing, but can switch to NIFTY_200
UNIVERSE_NIFTY10 = [
    "RELIANCE.NS", "TCS.NS", "ADANIENT.NS", 
    "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS", 
    "SBIN.NS", "BHARTIARTL.NS", "ITC.NS", "LT.NS"
]
UNIVERSE = NIFTY_200 # Now scaling to full Nifty 200!

def ensure_data():
    store = DataStore()
    print(f"⏳ Checking data for {len(UNIVERSE)} tickers...")
    
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        executor.map(store.load_ticker, UNIVERSE + ["^NSEI"])
        
    print("✅ Universe data ready.\n")

def run_simulation():
    # Calculate 59 days ago to respect yfinance intraday limits
    start_date = (datetime.now() - timedelta(days=59)).strftime("%Y-%m-%d")
    print(f"\n⚠️ Note: Backtest start_date dynamically clamped to {start_date} due to yfinance 60-day 5m data limit.")
    
    engine = PortfolioEngine(
        tickers=UNIVERSE,
        start_date=start_date,
        initial_equity=1000000.0 # 10 Lakh starting capital
    )
    
    results = engine.run()
    
    if results.empty:
        print("⚠️ No trades were taken during the simulation.")
        return

    # --- Metrics ---
    total_trades = len(results)
    win_rate = (results['Net_R'] > 0).mean() * 100
    total_pnl = results['PnL'].sum()
    avg_net_r = results['Net_R'].mean()
    
    # Profit Factor
    gross_profits = results[results['PnL'] > 0]['PnL'].sum()
    gross_losses = abs(results[results['PnL'] < 0]['PnL'].sum())
    profit_factor = gross_profits / gross_losses if gross_losses > 0 else float('inf')

    print("\n" + "="*40)
    print("INSTITUTIONAL PORTFOLIO SUMMARY (V7.0)")
    print("="*40)
    print(f"Final Equity:    Rs.{1000000.0 + total_pnl:,.2f}")
    print(f"Total Trades:    {total_trades}")
    print(f"Win Rate:        {win_rate:.1f}%")
    print(f"Avg Net R:       {avg_net_r:.2f}")
    print(f"Profit Factor:   {profit_factor:.2f}")
    print("="*40)

    # Breakdown by Ticker
    print("\n📈 Performance by Ticker:")
    ticker_stats = results.groupby('ticker').agg({
        'Net_R': ['count', 'mean', 'sum'],
        'PnL': 'sum'
    })
    print(ticker_stats)

if __name__ == "__main__":
    ensure_data()
    run_simulation()
