import time
import pandas as pd
import sys
import os
from datetime import datetime

# Ensure project root is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.utils.market_time import MarketClock, IST
from src.scanner import Scanner
from src.notifications import AlertManager

def run_live_bot():
    clock = MarketClock()
    scanner = Scanner()
    alerts = AlertManager()
    
    # We will use the V6.1 Scanner which already has BrainAV5 and ExecutionModel built-in
    
    print("🦅 V6.2 LIVE HEADLESS BOT INITIALIZED")
    print(f"   Mode: Parallel Scan (60 Tickers) | Risk: ₹10,000/trade")
    if not alerts.telegram.enabled and not alerts.email.enabled:
        print("   ⚠️  No alert channels configured — signals will only be logged to live_signals.csv.")
        print("      Run `python -m src.notifications` after setting up .env to enable Telegram/email.")
    
    while True:
        # 1. Check Schedule
        is_open, status = clock.is_market_open()
        
        if not is_open:
            print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] Market Status: {status}. Waiting...")
            time.sleep(60) # Check every minute
            continue

        # 2. Market is Open - Wait for Candle Close
        clock.wait_for_next_candle(interval_minutes=5)
        
        timestamp = datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')
        print(f"\n⚡ [{timestamp}] Triggering 5m Scan...")

        # 3. Run V6.1 Scanner
        try:
            # Note: Scanner.scan_market() handles its own context fetch
            results = scanner.scan_market()
            
            # 4. Process High Conviction Signals
            if results:
                print(f"   Found {len(results)} Setup(s). Filtering for automation...")
                
                for res in results:
                    # Double check Kill Score Threshold for Automation (Institutional Tier)
                    if res['kill_score'] >= 7.0: 
                        
                        # Log to CSV (The Live Feed)
                        log_entry = {
                            "timestamp": timestamp,
                            "ticker": res['ticker'],
                            "action": "SIGNAL",
                            "price": round(res['close'], 2),
                            "kill_score": res['kill_score'],
                            "direction": res['direction'],
                            "shares": res['shares'],
                            "entry": res['entry'],
                            "stop": res['stop'],
                            "target": res['target'],
                            "status": "DETECTED",
                            "reasons": ", ".join(res['reasons'])
                        }
                        
                        # Save to live_signals.csv
                        filename = "live_signals.csv"
                        df = pd.DataFrame([log_entry])
                        file_exists = os.path.isfile(filename)
                        df.to_csv(filename, mode='a', header=not file_exists, index=False)
                        
                        print(f"   >>> 🔔 SIGNAL LOGGED: {res['ticker']} (Score: {res['kill_score']}) | Qty: {res['shares']}")

                        # Fan out to Telegram / email (no-op if neither is
                        # configured; won't raise even if a send fails)
                        sent_via = alerts.send_signal_alert(res)
                        if sent_via:
                            print(f"       📨 Alert sent via: {', '.join(sorted(sent_via))}")
            else:
                print("   No signals found this interval.")

        except Exception as e:
            print(f"   ❌ Critical Scan Error: {e}")

if __name__ == "__main__":
    run_live_bot()
