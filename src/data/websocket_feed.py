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
    
    def __init__(self, interval_seconds: int = 300):
        self.interval = interval_seconds
        self.candles: Dict[str, List[Candle]] = {}  # symbol -> list of completed candles
        self.current: Dict[str, dict] = {}  # symbol -> current building candle
        self.on_candle_close: Optional[Callable[[str, Candle], None]] = None
    
    def _get_candle_start(self, ts: datetime) -> datetime:
        """Round timestamp down to interval boundary."""
        epoch = ts.timestamp()
        boundary = (epoch // self.interval) * self.interval
        return datetime.fromtimestamp(boundary)
    
    def process_tick(self, tick: Tick):
        """Process incoming tick and build candles."""
        candle_start = self._get_candle_start(tick.timestamp)
        symbol = tick.symbol
        
        if symbol not in self.current:
            self.current[symbol] = {
                'start': candle_start,
                'open': tick.price,
                'high': tick.price,
                'low': tick.price,
                'close': tick.price,
                'volume': tick.volume
            }
            self.candles[symbol] = []
            return
        
        curr = self.current[symbol]
        
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
            
            # Callback
            if self.on_candle_close:
                self.on_candle_close(symbol, completed)
            
            # Start new candle
            self.current[symbol] = {
                'start': candle_start,
                'open': tick.price,
                'high': tick.price,
                'low': tick.price,
                'close': tick.price,
                'volume': tick.volume
            }
        else:
            # Update current candle
            curr['high'] = max(curr['high'], tick.price)
            curr['low'] = min(curr['low'], tick.price)
            curr['close'] = tick.price
            curr['volume'] += tick.volume
    
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
