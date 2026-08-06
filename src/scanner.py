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

log = get_logger(__name__)

# --- Ticker Universe ---
# Flatten list for bulk scan
ALL_TICKERS = NIFTY_200

class Scanner:
    def __init__(self):
        self.brain = BrainAV5()
        self.exec_model = ExecutionModel()
        self.regime_detector = RegimeDetector()

    def fetch_market_context(self):
        """
        Fetches Nifty 50 data to determine global regime and RS Baseline.
        """
        log.info("Fetching Market Context (^NSEI)...")
        try:
            # Use 1y period for technicals (EMA_200) and stability
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

            nifty_df = self.brain.calculate_technicals(nifty_df)
            
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
            return self.brain.calculate_technicals(nifty_intraday)
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

    def scan_market(self):
        """
        Main V6.2 Scan Loop (Bulk Optimized)
        """
        start_time = time.time()
        results = []
        
        # 1. Get Context (daily Nifty — used for the Bullish/Bearish regime label)
        nifty_df, market_regime = self.fetch_market_context()

        # 1b. Get a 5-min-resolution Nifty series for RS scoring, so it can
        # actually be joined against the 5-min stock bars below.
        nifty_rs_df = self.fetch_nifty_intraday()

        # 2. Bulk Fetch Data
        bulk_data = self.fetch_bulk_data(ALL_TICKERS)
        
        if bulk_data is None or bulk_data.empty:
            log.warning("No data fetched. Aborting scan.")
            return []

        log.debug("Bulk Data Columns: %s", bulk_data.columns[:5])
        log.debug("Bulk Data Head:\n%s", bulk_data.head())

        # 3. Iterate and Analyze
        log.info("Analyzing %d tickers...", len(ALL_TICKERS))
        
        for ticker in ALL_TICKERS:
            try:
                # Extract Ticker Slice
                if isinstance(bulk_data.columns, pd.MultiIndex):
                    # Check if ticker is in columns (level 0)
                    if ticker not in bulk_data.columns.get_level_values(0):
                        log.debug("Ticker %s not in bulk data", ticker)
                        continue
                    stock_df = bulk_data[ticker].copy()
                else:
                    if len(ALL_TICKERS) == 1:
                        stock_df = bulk_data.copy()
                    else:
                        continue
                
                # Basic Validation
                stock_df.dropna(inplace=True)
                if len(stock_df) < 50: 
                    log.debug("Skipping %s: Not enough data (%d)", ticker, len(stock_df))
                    continue
                
                log.debug("Analyzing %s with %d rows...", ticker, len(stock_df))
                
                # 4. Analyze Slice (Pure Logic)
                # Use the 5-min Nifty series for RS (timeframe-matched to
                # stock_df); market_regime (the label) still comes from the
                # daily context fetched above.
                result = self.brain.analyze_slice(ticker, stock_df, nifty_rs_df, market_regime)
                
                if result:
                    # 5. Filter: Kill Score check
                    if result['kill_score'] >= config.KILL_SCORE_THRESHOLD:
                        # 6. Apply Execution Model (Pass full metadata for Structure Targets)
                        orders = self.exec_model.generate_orders(result)

                        # Same gate the backtest engine applies: reject
                        # trades with poor R:R or degenerate/zero sizing.
                        # Previously this check was missing here, so the
                        # live scanner could surface setups that the
                        # backtester would never have taken.
                        if not orders['valid_rr']:
                            log.debug("Rejected %s: invalid_rr (RR=%.2f, shares=%d)",
                                      ticker, orders['risk_reward'], orders['shares'])
                            continue

                        result.update(orders)
                        
                        # Add Entry/Stop/Target to result for unified interface
                        result['entry'] = orders['entry']
                        result['stop'] = orders['stop']
                        result['target'] = orders['target']
                        result['shares'] = orders['shares']
                        
                        # LOGGING: Persist Signal to DB
                        get_db().log_trade(
                            ticker=ticker,
                            action=result['direction'],
                            price=result['close'],
                            shares=orders['shares'],
                            status="SIGNAL"
                        )
                        
                        results.append(result)
                        
            except KeyError:
                log.debug("KeyError extracting %s from bulk data", ticker)
            except Exception as e:
                log.warning("Analysis failed for %s: %s", ticker, e)

        # 7. Sort by Kill Score
        results.sort(key=lambda x: x['kill_score'], reverse=True)
        
        log.info("Scan Complete in %.2fs", time.time() - start_time)
        log.info("Found %d High-Conviction Setups", len(results))
        
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
