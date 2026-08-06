import pandas as pd
import numpy as np
import concurrent.futures
from datetime import datetime

from src.logger import get_logger
import src.config as config
from src.data.store import DataStore
from src.brain_a_v5 import BrainAV5
from src.execution.model import ExecutionModel
from src.execution.costs import NSECostModel
from src.backtest.portfolio import Portfolio
from src.backtest.constraints import PortfolioConstraints
from src.regime import RegimeDetector

log = get_logger(__name__)

class PortfolioEngine:
    """
    V7.0 Sovereign Portfolio Engine
    Orchestrates:
    - Multi-ticker backtesting
    - Dynamic Position Sizing (Conviction/Vol/DD)
    - Portfolio Volatility Targeting (12% Annualized)
    - Market Breadth Filter (50-EMA Universe scan)
    - Event & Gap Risk Filtering
    """
    def __init__(self, tickers, start_date="2024-01-01", initial_equity=100000.0, kill_threshold=6.0, research_mode=False, adaptive_mode=False):
        self.tickers = tickers
        self.start_date = pd.to_datetime(start_date).tz_localize(None)
        self.initial_equity = initial_equity
        self.equity = initial_equity
        self.kill_threshold = kill_threshold
        self.research_mode = research_mode
        self.adaptive_mode = adaptive_mode
        
        self.store = DataStore()
        self.brain = BrainAV5()
        self.exec_model = ExecutionModel()
        self.portfolio = Portfolio()
        self.cost_model = NSECostModel()
        self.constraints = PortfolioConstraints()
        self.regime_detector = RegimeDetector()
        
        self.data_map = {}
        self.master_timeline = None
        self.warmup = 150 
        
        # V7.0 Tracking
        self.equity_history = []  # List of daily/interval equity for vol calc
        self.peak_equity = initial_equity
        self.daily_returns = []

    def _compute_rs_score_series(self, stock_df, nifty_df):
        """
        Vectorized, point-in-time RS score per bar: rolling (lookback-bar)
        log-return spread of stock vs Nifty, on the same scale as
        BrainAV5.calculate_relative_strength (percentage points).

        Previously no code path ever attached an 'RS_Score' column to a
        ticker's DataFrame, so Position._check_structural_exit's RS_Score
        read (`bar.get('RS_Score', 1.0)`) always fell back to 1.0 and the
        RS_DETERIORATION structural exit could never trigger. This makes
        that exit real.
        """
        lookback = self.brain.rs_lookback
        merged = stock_df[['Close']].join(
            nifty_df[['Close']], how="inner", lsuffix="_stock", rsuffix="_nifty"
        )
        stock_ret = np.log(merged['Close_stock'] / merged['Close_stock'].shift(lookback))
        nifty_ret = np.log(merged['Close_nifty'] / merged['Close_nifty'].shift(lookback))
        rs = (stock_ret - nifty_ret) * 100
        # Reindex back onto the stock's own index (join can drop timestamps
        # that don't exist in nifty_df).
        return rs.reindex(stock_df.index)

    def load_universe(self):
        log.info("Loading V7.0 Universe (%d tickers)...", len(self.tickers))
        
        nifty_df = self.store.load_ticker("^NSEI")
        if nifty_df is None:
            raise Exception("Nifty Data Required")
        nifty_df.index = pd.to_datetime(nifty_df.index).tz_localize(None)
        self.data_map["^NSEI"] = nifty_df

        def load_one(ticker):
            df = self.store.load_ticker(ticker)
            if df is not None:
                df.index = pd.to_datetime(df.index).tz_localize(None)
                # V7.0: Pre-compute indicators for breadth
                df = self.brain.calculate_technicals(df)
                df['RS_Score'] = self._compute_rs_score_series(df, nifty_df)
                return ticker, df
            return ticker, None

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(load_one, self.tickers))

        all_timestamps = set(nifty_df.index)
        for ticker, df in results:
            if df is not None:
                self.data_map[ticker] = df
                all_timestamps = all_timestamps.union(set(df.index))
        
        self.master_timeline = sorted(list(all_timestamps))
        self.master_timeline = [t for t in self.master_timeline if t >= self.start_date]
        self.constraints.update_price_data(self.data_map)

    def _calculate_portfolio_vol(self):
        """
        V7.0: Calculate realized annualized volatility based on equity history.
        Returns daily-equivalent vol if data exists, else default.
        """
        if len(self.equity_history) < 20:
            return 0.12 / (252 ** 0.5) # Default to 12% annualized target
            
        returns = pd.Series(self.equity_history).pct_change().dropna()
        if returns.std() == 0:
            return 0.12 / (252 ** 0.5)
            
        return returns.std()

    def _check_gap_risk(self, ticker, timestamp, bar):
        """
        V7.0: Event/Gap Risk Filter
        Vetoes if the overnight gap is > 1.5x ATR.
        """
        ticker_df = self.data_map.get(ticker)
        idx = ticker_df.index.get_loc(timestamp)
        if idx == 0: return False
        
        prev_close = ticker_df.iloc[idx-1]['Close']
        prev_atr = ticker_df.iloc[idx-1].get('ATR', bar['ATR'])
        
        gap = abs(bar['Open'] - prev_close)
        if prev_atr > 0 and gap > (config.GAP_VETO_MULT * prev_atr):
            return True # VETO
            
        return False

    def run(self):
        self.load_universe()
        nifty_df = self.data_map["^NSEI"]
        self.regime_detector.calculate_emas(nifty_df)
        
        log.info("Starting V7.0 Multi-Factor Simulation (Equity: ₹%.2f)", self.equity)
        
        for i, timestamp in enumerate(self.master_timeline):
            if i < self.warmup: continue
            
            # --- 1. Portfolio Maintenance ---
            # Update Equity Tracking
            self.equity_history.append(self.equity)
            self.peak_equity = max(self.peak_equity, self.equity)
            current_dd = (self.peak_equity - self.equity) / self.peak_equity
            portfolio_vol = self._calculate_portfolio_vol()

            # --- 2. System Health Check ---
            is_healthy, reason = self.constraints.check_system_health(self.equity)
            if not is_healthy:
                log.critical("CIRCUIT BREAKER: %s at %s", reason, timestamp)
                break

            # --- 3. Position Updates (Structural Exits) ---
            for pos in self.portfolio.open_positions:
                ticker_df = self.data_map.get(pos.ticker)
                if ticker_df is not None and timestamp in ticker_df.index:
                    bar = ticker_df.loc[timestamp]
                    pos.update(bar, timestamp)
            
            self.portfolio.cleanup_positions()
            for pos in self.portfolio.closed_trades:
                if not getattr(pos, 'accounted_for', False):
                    self.equity += pos.calculate_net_pnl(self.cost_model)
                    pos.accounted_for = True
            
            # --- 4. Market Context (Regime & Breadth) ---
            if timestamp not in nifty_df.index: continue
            nifty_row = nifty_df.loc[timestamp]
            nifty_slice = nifty_df.loc[:timestamp].tail(self.warmup + 1)
            
            # V7.0 Breadth Calculation
            breadth = self.regime_detector.get_breadth_score(self.data_map, timestamp)
            
            regime_state = self.regime_detector.detect(
                nifty_row['Close'],
                nifty_row[f'EMA_{self.regime_detector.primary_period}'],
                nifty_row[f'EMA_{self.regime_detector.gatekeeper_period}'],
                breadth_score=breadth
            )
            
            # --- 5. Signal Scan ---
            potential_signals = []
            current_threshold = self.kill_threshold
            if self.adaptive_mode:
                current_threshold = 4.0 if regime_state['regime'] == "Bullish" else 6.0

            # Only scan if Nifty allows
            if regime_state['longs_authorized'] or regime_state['shorts_authorized']:
                for ticker in self.tickers:
                    if self.portfolio.is_invested(ticker): continue
                    
                    df = self.data_map.get(ticker)
                    if df is None or timestamp not in df.index: continue
                    
                    bar = df.loc[timestamp]
                    
                    # V7.0 Gap Risk Check
                    if self._check_gap_risk(ticker, timestamp, bar):
                        continue
                        
                    ticker_slice = df.loc[:timestamp].tail(self.warmup + 1)
                    sig = self.brain.analyze_slice(ticker, ticker_slice, nifty_slice, regime_state['regime'])
                    
                    if sig and sig['kill_score'] >= current_threshold:
                        sig['breadth_score'] = breadth # Log for features
                        potential_signals.append(sig)

            # --- 6. Execution & Dynamic Sizing ---
            potential_signals.sort(key=lambda x: x['kill_score'], reverse=True)
            
            for sig in potential_signals:
                ticker = sig['ticker']
                allowed, reason = self.constraints.can_open_trade(
                    self.portfolio, ticker, self.equity, timestamp=timestamp
                )
                
                if allowed:
                    # DYNAMIC SIZING logic orchestrated here
                    # Pass Vol and DD to the execution model
                    orders = self.exec_model.generate_orders(
                        sig, 
                        portfolio_vol=portfolio_vol, 
                        current_dd=current_dd
                    )
                    
                    if orders['valid_rr']:
                        trade_id = f"{ticker}_{timestamp.strftime('%Y%m%d%H%M')}"
                        self.portfolio.open_trade(
                            trade_id=trade_id,
                            ticker=ticker,
                            direction=sig['direction'],
                            entry=orders['entry'],
                            stop=orders['stop'],
                            target=orders['target'],
                            r_unit=sig['atr'],
                            shares=orders['shares'],
                            timestamp=timestamp,
                            features=sig['features']
                        )

            if i % 1000 == 0:
                log.info("%s | Equity: ₹%.0f | Breadth: %.2f | Open: %d", 
                         timestamp, self.equity, breadth, len(self.portfolio.open_positions))

        return self.report()

    def report(self):
        log.info("V7.0 Simulation Complete.")
        results = []
        for pos in self.portfolio.closed_trades:
            results.append({
                "ticker": pos.ticker,
                "PnL": pos.calculate_net_pnl(self.cost_model),
                "Net_R": pos.calculate_net_r(self.cost_model),
                "Reason": pos.exit_reason,
                "Held": pos.bars_held
            })
        
        df = pd.DataFrame(results)
        if not df.empty:
            log.info("Trades: %d | Final Equity: ₹%.2f | CAGR Proxy: %.1f%%", 
                     len(df), self.equity, ((self.equity/self.initial_equity)-1)*100)
        return df
