import yfinance as yf
import time
import pandas as pd
from src.logger import get_logger

from src.brain_a_v5 import BrainAV5
from src.execution.model import ExecutionModel
from src.data.universe import NIFTY_200
import src.config as config
from src.data.db import get_db
from src.regime import RegimeDetector
from src.data.live_validation import completed_bars
from src.utils.market_time import market_timestamp, market_index, is_entry_time

log = get_logger(__name__)

# --- Ticker Universe ---
# Flatten list for bulk scan
ALL_TICKERS = NIFTY_200

class Scanner:
    def __init__(self):
        self.brain = BrainAV5()
        self.exec_model = ExecutionModel()
        self.regime_detector = RegimeDetector()
        self.last_scan_status = 'NOT_RUN'

    def fetch_market_context(self):
        """
        Fetches Nifty 50 data to determine global regime and RS Baseline.
        """
        log.info("Fetching Market Context (^NSEI)...")
        try:
            # Use one year for daily regime context.
            nifty_df = yf.download("^NSEI", period="1y", interval="1d", progress=False, threads=False)
            
            if nifty_df.empty:
                log.warning("^NSEI fetch failed. Trying NIFTYBEES.NS fallback...")
                nifty_df = yf.download("NIFTYBEES.NS", period="1y", interval="1d", progress=False, threads=False)

            # Handle MultiIndex if present
            if isinstance(nifty_df.columns, pd.MultiIndex):
                nifty_df.columns = nifty_df.columns.get_level_values(0)

            if nifty_df.empty:
                log.critical("Could not fetch Nifty data. RS Scoring will be disabled.")
                return None, "Neutral"

            # Index volume is often zero. Its regime needs only closes, not
            # VWAP/stock liquidity indicators (which erase a zero-volume index).
            nifty_df = nifty_df.copy()
            nifty_df.index = market_index(nifty_df.index)
            now = market_timestamp(pd.Timestamp.now(tz='Asia/Kolkata'))
            if now.time() < pd.Timestamp('15:30').time():
                nifty_df = nifty_df.loc[nifty_df.index.normalize() < now.normalize()]
            
            if nifty_df.empty:
                log.error("Nifty data empty after technical processing.")
                return None, "Neutral"

            # Determine Regime (Unified)
            regime_state = self.regime_detector.detect_from_slice(nifty_df)
            regime = regime_state['regime']
            price = nifty_df['Close'].iloc[-1]
            
            log.info("Market Regime: %s (Nifty: %.2f)", regime, price)
            return nifty_df, regime
        except Exception as e:
            log.error("Market Context Error: %s", e)
            import traceback
            traceback.print_exc()
            return None, "Neutral"

    def fetch_nifty_intraday(self):
        """
        Fetches Nifty at the SAME interval/period as the ticker universe
        (5d/5m) so it can be joined against stock bars for RS scoring.

        BUG FIX: scan_market() used to pass the *daily* nifty_df (from
        fetch_market_context, period=1y/interval=1d) into
        BrainAV5.analyze_slice() as the RS reference series, alongside
        *5-minute* stock bars. calculate_relative_strength() inner-joins
        the two on timestamp — daily and 5-min timestamps almost never
        coincide, so the join returned ~0 rows and RS score silently
        defaulted to 0.0 for every ticker in the live scanner. This method
        fetches Nifty at matching resolution so the join actually has
        overlapping timestamps to work with.
        """
        try:
            nifty_intraday = yf.download(
                "^NSEI", period="5d", interval="5m", progress=False, threads=False
            )
            if isinstance(nifty_intraday.columns, pd.MultiIndex):
                nifty_intraday.columns = nifty_intraday.columns.get_level_values(0)
            if nifty_intraday.empty:
                log.warning("Intraday Nifty fetch failed; RS scoring will default to 0.")
                return None
            return nifty_intraday
        except Exception as e:
            log.error("Intraday Nifty fetch error: %s", e)
            return None

    def fetch_bulk_data(self, tickers):
        """
        Fetches data for ALL tickers in a single call.
        """
        log.info("Bulk Downloading %d tickers...", len(tickers))
        start_t = time.time()
        
        try:
            bulk_df = yf.download(
                tickers, 
                period="5d", 
                interval="5m", 
                group_by='ticker', 
                progress=False, 
                threads=True
            )
            log.info("Download complete in %.2fs", time.time() - start_t)
            return bulk_df
        except Exception as e:
            log.error("Bulk Download Failed: %s", e)
            return None

    def scan_market(self, now=None):
        """Scan only the latest completed, aligned intraday session bar."""
        now = market_timestamp(pd.Timestamp.now(tz='Asia/Kolkata') if now is None else now)
        if not is_entry_time(now):
            self.last_scan_status = 'OUTSIDE_ENTRY_SESSION'
            return []
        try:
            nifty = completed_bars(self.fetch_nifty_intraday(), now)
        except (ValueError, TypeError) as exc:
            self.last_scan_status = f'BENCHMARK_UNAVAILABLE: {exc}'
            log.warning(self.last_scan_status)
            return []
        bulk = self.fetch_bulk_data(ALL_TICKERS)
        if bulk is None or bulk.empty:
            self.last_scan_status = 'UNIVERSE_UNAVAILABLE'
            return []
        as_of = nifty.index[-1]
        prepared = {}
        for ticker in ALL_TICKERS:
            try:
                if isinstance(bulk.columns, pd.MultiIndex):
                    if ticker not in bulk.columns.get_level_values(0):
                        continue
                    raw = bulk[ticker]
                elif len(ALL_TICKERS) == 1:
                    raw = bulk
                else:
                    continue
                raw = completed_bars(raw, now, min_bars=self.brain.MIN_RAW_BARS)
                frame = self.brain.calculate_technicals(raw)
                if len(frame) >= 50 and frame.index[-1] == as_of:
                    prepared[ticker] = frame
            except (ValueError, TypeError, KeyError) as exc:
                log.warning('Rejected %s market data: %s', ticker, exc)
        if not prepared or len(prepared) / len(set(ALL_TICKERS)) < config.MIN_BREADTH_COVERAGE:
            self.last_scan_status = 'INSUFFICIENT_BREADTH_COVERAGE'
            return []
        # Both live scanners and the backtest use intraday EMA regime + breadth.
        self.regime_detector.calculate_emas(nifty)
        row = nifty.iloc[-1]
        breadth = self.regime_detector.get_breadth_score(prepared, as_of)
        state = self.regime_detector.detect(
            row['Close'], row[f'EMA_{self.regime_detector.primary_period}'],
            row[f'EMA_{self.regime_detector.gatekeeper_period}'], breadth_score=breadth)
        results = []
        for ticker, frame in prepared.items():
            try:
                result = self.brain.analyze_slice(ticker, frame, nifty, state['regime'], technicals_ready=True)
                if not result or result['kill_score'] < config.KILL_SCORE_THRESHOLD:
                    continue
                authorized = ((result['direction'] == 'LONG' and state['longs_authorized'])
                              or (result['direction'] == 'SHORT' and state['shorts_authorized']))
                if not authorized:
                    continue
                orders = self.exec_model.generate_orders(result)
                if not orders['valid_rr'] or orders['shares'] <= 0:
                    continue
                result = dict(result, **orders)
                result['breadth_score'] = breadth
                result['breadth_coverage'] = len(prepared) / len(set(ALL_TICKERS))
                get_db().log_trade(ticker=ticker, action=result['direction'], price=result['close'],
                                   shares=orders['shares'], status='SIGNAL')
                results.append(result)
            except Exception:
                log.exception('Analysis failed for %s', ticker)
        self.last_scan_status = 'OK'
        results.sort(key=lambda r: (-r['kill_score'], r['ticker']))
        return results

# --- Test Block ---
if __name__ == "__main__":
    scanner = Scanner()
    opportunities = scanner.scan_market()
    
    for op in opportunities:
        print(f"{op['ticker']} | Score: {op['kill_score']}/10 | {op['direction']}")
        print(f"   Entry: {op['entry']} | Stop: {op['stop']} | Target: {op['target']}")
        print(f"   SHARES: {op['shares']} (Risk: {op['shares'] * abs(op['entry'] - op['stop']):.2f})")
        print(f"   Reasons: {', '.join(op['reasons'])}\n")
