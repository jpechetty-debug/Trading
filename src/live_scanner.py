"""
Live Scanner - Real-time signal generation using WebSocket data.
"""

import asyncio
import pandas as pd

from src.data.websocket_feed import MockFeed, CandleBuilder, Tick, Candle
from src.brain_a_v5 import BrainAV5
from src.execution.model import ExecutionModel
from src.data.db import get_db
from src.data.universe import NIFTY_200
from src.scanner import Scanner
from src.logger import get_logger
from src.notifications import AlertManager
from src.regime import RegimeDetector
from src.data.live_validation import completed_bars
from src.utils.market_time import market_timestamp, is_entry_time
import src.config as config

log = get_logger(__name__)


class LiveScanner:
    """
    Event-driven scanner that processes ticks in real-time.
    """
    
    def __init__(self, symbols: list = None, *, feed=None, enable_alerts=False, now=None):
        self.symbols = list(dict.fromkeys(symbols if symbols is not None else NIFTY_200[:20]))
        self._processed_bar = None
        self._processed_symbols = set()
        self.brain = BrainAV5()
        self.exec_model = ExecutionModel()
        self.candle_builder = CandleBuilder(interval_seconds=config.CANDLE_INTERVAL_SECONDS)
        self.feed = MockFeed(tick_interval_ms=50) if feed is None else feed
        self.simulated = isinstance(self.feed, MockFeed)
        if self.simulated and enable_alerts:
            raise ValueError('Simulated feeds cannot send real alerts')
        self.enable_alerts = enable_alerts
        self.now = now or (lambda: pd.Timestamp.now(tz='Asia/Kolkata'))
        self.regime_detector = RegimeDetector()
        self.alerts = AlertManager()
        
        # Market context (simplified for live mode)
        self.market_regime = "Neutral"
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
        self.nifty_df = None
        self.market_regime = 'Neutral'
        try:
            nifty_intraday = self._context_scanner.fetch_nifty_intraday()
            if nifty_intraday is not None:
                self.nifty_df = completed_bars(nifty_intraday, self.now())
                self.market_regime = self.regime_detector.detect_from_slice(self.nifty_df)['regime']
            else:
                log.warning('Nifty unavailable; signal generation disabled')
        except Exception:
            log.exception("Failed to refresh market context")
    
    def on_tick(self, tick: Tick):
        """Process incoming tick."""
        self.candle_builder.process_tick(tick)
    
    def on_candle_close(self, symbol: str, candle: Candle):
        """Publish only validated, aligned, completed-candle signals."""
        now = market_timestamp(self.now())
        if not is_entry_time(now):
            return
        try:
            nifty = completed_bars(self.nifty_df, now)
            as_of = nifty.index[-1]
            if market_timestamp(candle.timestamp) != as_of:
                return
            prepared = {}
            for ticker in self.symbols:
                raw = self.candle_builder.get_dataframe(ticker, min_candles=self.brain.MIN_RAW_BARS)
                if raw is None:
                    continue
                try:
                    raw = completed_bars(raw, now, min_bars=self.brain.MIN_RAW_BARS)
                    frame = self.brain.calculate_technicals(raw)
                    if len(frame) >= 50 and frame.index[-1] == as_of:
                        prepared[ticker] = frame
                except (ValueError, TypeError):
                    continue
            if not prepared or len(prepared) / max(1, len(self.symbols)) < config.MIN_BREADTH_COVERAGE:
                return
            self.regime_detector.calculate_emas(nifty)
            row = nifty.iloc[-1]
            breadth = self.regime_detector.get_breadth_score(prepared, as_of)
            state = self.regime_detector.detect(
                row['Close'], row[f'EMA_{self.regime_detector.primary_period}'],
                row[f'EMA_{self.regime_detector.gatekeeper_period}'], breadth_score=breadth)
            self.market_regime = state['regime']
            if self._processed_bar != as_of:
                self._processed_bar = as_of
                self._processed_symbols.clear()
            # Once enough symbols have closed, evaluate the full snapshot so
            # early-arriving symbols are not permanently skipped each interval.
            for symbol, frame in prepared.items():
                if symbol in self._processed_symbols:
                    continue
                result = self.brain.analyze_slice(symbol, frame, nifty, state['regime'], technicals_ready=True)
                if not result or result['kill_score'] < config.KILL_SCORE_THRESHOLD:
                    self._processed_symbols.add(symbol)
                    continue
                authorized = ((result['direction'] == 'LONG' and state['longs_authorized'])
                              or (result['direction'] == 'SHORT' and state['shorts_authorized']))
                if not authorized:
                    self._processed_symbols.add(symbol)
                    continue
                orders = self.exec_model.generate_orders(result)
                if not orders.get('valid_rr', False) or orders['shares'] <= 0:
                    self._processed_symbols.add(symbol)
                    continue
                result = dict(result, ticker=symbol, **orders)
                result['breadth_score'] = breadth
                result['breadth_coverage'] = len(prepared) / len(self.symbols)
                if self.simulated:
                    log.info('SIMULATED signal %s %s (no persistence or alerts)', symbol, result['direction'])
                else:
                    get_db().log_trade(ticker=symbol, action=result['direction'], price=result['close'],
                                       shares=orders['shares'], status='SIGNAL')
                    if self.enable_alerts:
                        self.alerts.send_signal_alert(result)
                self._processed_symbols.add(symbol)
        except (ValueError, TypeError) as exc:
            log.warning('Signal disabled for %s: %s', symbol, exc)
        except Exception:
            log.exception('Analysis failed for %s', symbol)

    async def run(self, duration_seconds: int = 60):
        """Run the live scanner for a specified duration."""
        mode = 'Simulated feed' if self.simulated else 'Market feed'
        print(f"Starting Live Scanner ({mode}) for {duration_seconds}s...")
        print(f"Symbols: {len(self.symbols)}")
        print(f"Candle Interval: {config.CANDLE_INTERVAL_SECONDS}s")
        print("-" * 50)

        await asyncio.to_thread(self.refresh_market_context)
        stream_task = None
        refresh_task = None
        async def refresh_loop():
            while True:
                now = market_timestamp(self.now())
                target = now.floor(f'{config.CANDLE_INTERVAL_SECONDS}s') + pd.Timedelta(
                    seconds=config.CANDLE_INTERVAL_SECONDS + 5)
                await asyncio.sleep(max(0.1, (target - now).total_seconds()))
                await asyncio.to_thread(self.refresh_market_context)
                # A provider may publish its benchmark a few seconds after the
                # first stock tick closes a bar. Retry the completed snapshot.
                for symbol, candles in list(self.candle_builder.candles.items()):
                    if candles:
                        self.on_candle_close(symbol, candles[-1])
        try:
            await self.feed.connect()
            await self.feed.subscribe(self.symbols)
            stream_task = asyncio.create_task(self.feed.stream())
            refresh_task = asyncio.create_task(refresh_loop())
            done, _ = await asyncio.wait({stream_task}, timeout=duration_seconds)
            if done:
                await stream_task  # Surface feed failures immediately.
        finally:
            for task in (stream_task, refresh_task):
                if task is not None:
                    task.cancel()
            await asyncio.gather(*(t for t in (stream_task, refresh_task) if t is not None),
                                 return_exceptions=True)
            await self.feed.disconnect()

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
