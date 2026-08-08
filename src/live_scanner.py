"""
Live Scanner - Real-time signal generation using WebSocket data.
"""

import asyncio
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.data.websocket_feed import MockFeed, CandleBuilder, Tick, Candle
from src.brain_a_v5 import BrainAV5
from src.execution.model import ExecutionModel
from src.data.db import get_db
from src.data.universe import NIFTY_200
from src.scanner import Scanner
from src.logger import get_logger
from src.notifications import AlertManager
import src.config as config

log = get_logger(__name__)


class LiveScanner:
    """
    Event-driven scanner that processes ticks in real-time.
    """
    
    def __init__(self, symbols: list = None):
        self.symbols = symbols or NIFTY_200[:20]  # Limit for testing
        self.brain = BrainAV5()
        self.exec_model = ExecutionModel()
        self.candle_builder = CandleBuilder(interval_seconds=config.CANDLE_INTERVAL_SECONDS)
        self.feed = MockFeed(tick_interval_ms=50)  # Fast simulation
        self.alerts = AlertManager()
        
        # Market context (simplified for live mode)
        self.market_regime = "Bullish"
        # BUG FIX: this was never populated, so every call to
        # brain.analyze_slice() below received nifty_slice=None. Since
        # calculate_relative_strength / calculate_rs_slope used to assume a
        # real DataFrame, that raised an exception on every single candle
        # close — silently, because the broad `except Exception: pass`
        # further down swallowed it. LiveScanner therefore built candles
        # correctly but never produced a single signal. It's now backed by
        # a real (5-min resolution) Nifty fetch via Scanner, refreshed
        # periodically in run() below. BrainAV5's RS methods also now
        # tolerate nifty_df=None defensively (return rs=0.0) in case the
        # fetch hasn't completed yet or fails.
        self.nifty_df = None
        self._context_scanner = Scanner()

        # Wire up callbacks
        self.candle_builder.on_candle_close = self.on_candle_close
        self.feed.set_tick_callback(self.on_tick)

    def refresh_market_context(self):
        """Fetch real Nifty context (regime + intraday RS reference)."""
        try:
            _, regime = self._context_scanner.fetch_market_context()
            nifty_intraday = self._context_scanner.fetch_nifty_intraday()
            if regime:
                self.market_regime = regime
            if nifty_intraday is not None:
                self.nifty_df = nifty_intraday
            else:
                log.warning("Nifty intraday context unavailable; RS scoring will default to 0.")
        except Exception:
            log.exception("Failed to refresh market context")
    
    def on_tick(self, tick: Tick):
        """Process incoming tick."""
        self.candle_builder.process_tick(tick)
    
    def on_candle_close(self, symbol: str, candle: Candle):
        """Analyze when a candle closes."""
        # Get dataframe for this symbol
        df = self.candle_builder.get_dataframe(symbol, min_candles=50)
        
        if df is None:
            return  # Not enough data yet
        
        # Run analysis
        try:
            result = self.brain.analyze_slice(symbol, df, self.nifty_df, self.market_regime)
            
            if result and result['kill_score'] >= config.KILL_SCORE_THRESHOLD:
                # Generate orders with full metadata
                orders = self.exec_model.generate_orders(result)
                
                # Log signal
                print(f"\n*** SIGNAL: {symbol} | {result['direction']} | Score: {result['kill_score']}/10 ***")
                print(f"    Entry: {orders['entry']} | Stop: {orders['stop']} | Target: {orders['target']}")
                print(f"    Shares: {orders['shares']} | Reasons: {', '.join(result['reasons'])}")
                
                # Persist to DB
                get_db().log_trade(
                    ticker=symbol,
                    action=result['direction'],
                    price=result['close'],
                    shares=orders['shares'],
                    status="SIGNAL"
                )
                
                # Fan out to Telegram / email
                sent_via = self.alerts.send_signal_alert(result)
                if sent_via:
                    print(f"       📨 Alert sent via: {', '.join(sorted(sent_via))}")
        except Exception:
            # BUG FIX: this used to be `except Exception: pass`, which
            # hid every failure (including the nifty_df=None crash this
            # class used to have) with zero visibility. Log it instead —
            # a live trading component failing silently is worse than one
            # failing loudly.
            log.exception("Analysis failed for %s", symbol)
    
    async def run(self, duration_seconds: int = 60):
        """Run the live scanner for a specified duration."""
        print(f"Starting Live Scanner (Mock Mode) for {duration_seconds}s...")
        print(f"Symbols: {len(self.symbols)}")
        print(f"Candle Interval: {config.CANDLE_INTERVAL_SECONDS}s")
        print("-" * 50)

        # Populate market context before streaming starts (see bug-fix note
        # in __init__ — this used to never happen).
        self.refresh_market_context()

        await self.feed.connect()
        await self.feed.subscribe(self.symbols)
        
        # Create streaming task
        stream_task = asyncio.create_task(self.feed.stream())
        
        # Run for duration
        await asyncio.sleep(duration_seconds)
        
        # Stop
        await self.feed.disconnect()
        stream_task.cancel()
        
        print("\n" + "=" * 50)
        print("Live Scanner Stopped")
        
        # Summary
        total_candles = sum(len(c) for c in self.candle_builder.candles.values())
        print(f"Total Candles Built: {total_candles}")


async def main():
    scanner = LiveScanner(symbols=NIFTY_200[:10])  # Test with 10 symbols
    await scanner.run(duration_seconds=30)  # Run for 30 seconds


if __name__ == "__main__":
    asyncio.run(main())
