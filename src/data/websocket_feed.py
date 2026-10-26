"""
WebSocket Data Feed Infrastructure
Supports real-time tick streaming from various brokers.
"""

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional
from datetime import datetime
import pandas as pd
import numpy as np
from src.utils.market_time import market_timestamp
from src.logger import get_logger

log = get_logger(__name__)


@dataclass
class Tick:
    """Single price update from the market."""
    symbol: str
    price: float
    volume: int
    timestamp: datetime


@dataclass
class Candle:
    """OHLCV candle built from ticks."""
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    timestamp: datetime


class CandleBuilder:
    """
    Aggregates ticks into OHLCV candles.
    Emits a candle when the interval closes.
    """
    
    def __init__(self, interval_seconds: int = 300, max_candles: int = 500):
        if not isinstance(interval_seconds, int) or interval_seconds <= 0:
            raise ValueError('interval_seconds must be a positive integer')
        if not isinstance(max_candles, int) or max_candles < 250:
            raise ValueError('max_candles must retain at least 250 bars for indicators')
        self.interval = interval_seconds
        self.max_candles = max_candles
        self.candles: Dict[str, List[Candle]] = {}  # symbol -> list of completed candles
        self.current: Dict[str, dict] = {}  # symbol -> current building candle
        self.on_candle_close: Optional[Callable[[str, Candle], None]] = None
    
    def _get_candle_start(self, ts: datetime) -> datetime:
        """Round timestamp down to interval boundary."""
        return market_timestamp(ts).floor(f'{self.interval}s')
    
    def process_tick(self, tick: Tick):
        """Process incoming tick and build candles."""
        if not np.isfinite(tick.price) or tick.price <= 0 or not np.isfinite(tick.volume) or tick.volume < 0:
            log.warning('Discarded invalid tick for %s', tick.symbol)
            return False
        tick_time = market_timestamp(tick.timestamp)
        candle_start = self._get_candle_start(tick_time)
        symbol = tick.symbol
        
        if symbol not in self.current:
            self.current[symbol] = {
                'start': candle_start,
                'open': tick.price,
                'high': tick.price,
                'low': tick.price,
                'close': tick.price,
                'volume': tick.volume,
                'last_timestamp': tick_time,
            }
            self.candles[symbol] = []
            return
        
        curr = self.current[symbol]
        if tick_time < curr['last_timestamp']:
            log.warning('Discarded out-of-order tick for %s', symbol)
            return False
        
        # Check if we've moved to a new candle period
        if candle_start > curr['start']:
            # Close current candle
            completed = Candle(
                symbol=symbol,
                open=curr['open'],
                high=curr['high'],
                low=curr['low'],
                close=curr['close'],
                volume=curr['volume'],
                timestamp=curr['start']
            )
            self.candles[symbol].append(completed)
            del self.candles[symbol][:-self.max_candles]
            
            # Start new candle
            self.current[symbol] = {
                'start': candle_start,
                'open': tick.price,
                'high': tick.price,
                'low': tick.price,
                'close': tick.price,
                'volume': tick.volume,
                'last_timestamp': tick_time,
            }
            # Commit the new state before invoking downstream code. A failed
            # analyzer must not cause this completed candle to be emitted twice.
            if self.on_candle_close:
                try:
                    self.on_candle_close(symbol, completed)
                except Exception:
                    log.exception('Candle callback failed for %s', symbol)
        else:
            # Update current candle
            curr['high'] = max(curr['high'], tick.price)
            curr['low'] = min(curr['low'], tick.price)
            curr['close'] = tick.price
            curr['volume'] += tick.volume
            curr['last_timestamp'] = tick_time
    
    def get_dataframe(self, symbol: str, min_candles: int = 50) -> Optional[pd.DataFrame]:
        """Convert completed candles to DataFrame for analysis."""
        if symbol not in self.candles or len(self.candles[symbol]) < min_candles:
            return None
        
        data = [{
            'Open': c.open,
            'High': c.high,
            'Low': c.low,
            'Close': c.close,
            'Volume': c.volume
        } for c in self.candles[symbol]]
        
        df = pd.DataFrame(data)
        df.index = pd.DatetimeIndex([c.timestamp for c in self.candles[symbol]])
        return df


class DataFeed(ABC):
    """Abstract base class for data providers."""
    
    @abstractmethod
    async def connect(self):
        """Establish connection to data source."""
        pass
    
    @abstractmethod
    async def subscribe(self, symbols: List[str]):
        """Subscribe to tick updates for symbols."""
        pass
    
    @abstractmethod
    async def disconnect(self):
        """Close connection."""
        pass
    
    @abstractmethod
    def set_tick_callback(self, callback: Callable[[Tick], None]):
        """Set callback for incoming ticks."""
        pass


class MockFeed(DataFeed):
    """
    Mock data feed for testing.
    Simulates tick stream by generating random price moves.
    """
    
    def __init__(self, tick_interval_ms: int = 100):
        self.tick_interval = tick_interval_ms / 1000
        self.symbols: List[str] = []
        self.running = False
        self.callback: Optional[Callable[[Tick], None]] = None
        self.base_prices: Dict[str, float] = {}
    
    async def connect(self):
        print("MockFeed: Connected (Simulation Mode)")
        self.running = True
    
    async def subscribe(self, symbols: List[str]):
        self.symbols = symbols
        # Initialize with random base prices
        for s in symbols:
            self.base_prices[s] = np.random.uniform(100, 5000)
        print(f"MockFeed: Subscribed to {len(symbols)} symbols")
    
    async def disconnect(self):
        self.running = False
        print("MockFeed: Disconnected")
    
    def set_tick_callback(self, callback: Callable[[Tick], None]):
        self.callback = callback
    
    async def stream(self):
        """Generate simulated tick stream."""
        while self.running:
            for symbol in self.symbols:
                # Random walk
                change = np.random.uniform(-0.5, 0.5)
                self.base_prices[symbol] *= (1 + change / 100)
                
                tick = Tick(
                    symbol=symbol,
                    price=round(self.base_prices[symbol], 2),
                    volume=np.random.randint(100, 10000),
                    timestamp=datetime.now()
                )
                
                if self.callback:
                    self.callback(tick)
            
            await asyncio.sleep(self.tick_interval)


# --- Broker-Specific Implementations (Stubs) ---

class ZerodhaFeed(DataFeed):
    """Zerodha Kite Connect WebSocket feed."""
    
    def __init__(self, api_key: str, access_token: str):
        self.api_key = api_key
        self.access_token = access_token
        self.callback = None
        # from kiteconnect import KiteTicker
        # self.kws = KiteTicker(api_key, access_token)
    
    async def connect(self):
        # self.kws.connect(threaded=True)
        print("ZerodhaFeed: Connect not implemented. Install kiteconnect.")
    
    async def subscribe(self, symbols: List[str]):
        # Convert symbols to instrument tokens
        print("ZerodhaFeed: Subscribe not implemented.")
    
    async def disconnect(self):
        # self.kws.close()
        pass
    
    def set_tick_callback(self, callback):
        self.callback = callback


class AngelOneFeed(DataFeed):
    """Angel One SmartAPI WebSocket feed."""
    
    def __init__(self, api_key: str, client_id: str, password: str, totp_secret: str):
        self.api_key = api_key
        self.client_id = client_id
        self.callback = None
    
    async def connect(self):
        print("AngelOneFeed: Connect not implemented. Install smartapi-python.")
    
    async def subscribe(self, symbols: List[str]):
        print("AngelOneFeed: Subscribe not implemented.")
    
    async def disconnect(self):
        pass
    
    def set_tick_callback(self, callback):
        self.callback = callback
