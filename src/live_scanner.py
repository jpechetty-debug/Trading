"""
Live Scanner - Real-time signal generation using WebSocket data.
"""

import asyncio
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.data.websocket_feed import MockFeed, CandleBuilder, Tick, Candle
from src.brain_a_v5 import BrainAV5
from src.execution.model import ExecutionModel
from src.data.db import db
from src.data.universe import NIFTY_200
import src.config as config


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
        
        # Market context (simplified for live mode)
        self.market_regime = "Bullish"
        self.nifty_df = None
        
        # Wire up callbacks
        self.candle_builder.on_candle_close = self.on_candle_close
        self.feed.set_tick_callback(self.on_tick)
    
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
                # Generate orders
                signal_packet = {
                    'close': result['close'],
                    'atr': result['atr'],
                    'direction': result['direction']
                }
                orders = self.exec_model.generate_orders(signal_packet)
                
                # Log signal
                print(f"\n*** SIGNAL: {symbol} | {result['direction']} | Score: {result['kill_score']}/10 ***")
                print(f"    Entry: {orders['entry']} | Stop: {orders['stop']} | Target: {orders['target']}")
                print(f"    Shares: {orders['shares']} | Reasons: {', '.join(result['reasons'])}")
                
                # Persist to DB
                db.log_trade(
                    ticker=symbol,
                    action=result['direction'],
                    price=result['close'],
                    shares=orders['shares'],
                    status="SIGNAL"
                )
        except Exception as e:
            # Silently skip errors during live processing
            pass
    
    async def run(self, duration_seconds: int = 60):
        """Run the live scanner for a specified duration."""
        print(f"Starting Live Scanner (Mock Mode) for {duration_seconds}s...")
        print(f"Symbols: {len(self.symbols)}")
        print(f"Candle Interval: {config.CANDLE_INTERVAL_SECONDS}s")
        print("-" * 50)
        
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
