import time
import pandas as pd
import sys
import os
import math
from numbers import Real
from datetime import datetime

# Ensure project root is in path
if __name__ == '__main__' and not __package__:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.utils.market_time import MarketClock, IST
from src.scanner import Scanner, ALL_TICKERS
from src.notifications import AlertManager
import src.config as config


def startup_summary():
    return (f'V{config.SYSTEM_VERSION} SIGNAL ALERT BOT | Universe: {len(ALL_TICKERS)} tickers | '
            f'Alert score >= {config.ALERT_KILL_SCORE_THRESHOLD:g} | '
            'Indicative quantities; no broker/account risk enforcement')


def should_alert(signal):
    score = signal.get('kill_score')
    return (isinstance(score, Real) and math.isfinite(score)
            and score >= config.ALERT_KILL_SCORE_THRESHOLD
            and signal.get('valid_rr', False) and signal.get('shares', 0) > 0)

def run_live_bot():
    # Configure the interactive console only when running, never on import.
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    clock = MarketClock()
    scanner = Scanner()
    alerts = AlertManager()
    
    print(startup_summary())
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
        if not clock.is_market_open()[0]:
            continue
        
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
                    if should_alert(res):
                        
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
                        df.to_csv(filename, mode='a', header=not file_exists, index=False, encoding="utf-8")
                        
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
