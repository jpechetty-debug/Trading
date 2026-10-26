import pandas as pd
import numpy as np
import concurrent.futures

from src.logger import get_logger
import src.config as config
from src.data.store import DataStore
from src.brain_a_v5 import BrainAV5
from src.execution.model import ExecutionModel
from src.execution.costs import NSECostModel
from src.backtest.portfolio import Portfolio
from src.backtest.constraints import PortfolioConstraints
from src.regime import RegimeDetector
from src.utils.market_time import market_index, market_timestamp, is_entry_time, is_flatten_time

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
    def __init__(self, tickers, start_date="2024-01-01", initial_equity=100000.0, kill_threshold=6.0, adaptive_mode=False, data_store=None, end_date=None):
        if not np.isfinite(initial_equity) or initial_equity <= 0:
            raise ValueError("initial_equity must be finite and positive")
        self.tickers = list(dict.fromkeys(DataStore._normalize_ticker(t) for t in tickers))
        if '^NSEI' in self.tickers:
            raise ValueError('The benchmark cannot also be a traded instrument')
        self.start_date = market_timestamp(start_date)
        self.end_date = market_timestamp(end_date) if end_date is not None else None
        if pd.isna(self.start_date) or (self.end_date is not None and
                (pd.isna(self.end_date) or self.end_date <= self.start_date)):
            raise ValueError('Expected a valid start and an exclusive end after start')
        if not np.isfinite(kill_threshold) or not 0 <= kill_threshold <= 10:
            raise ValueError('kill_threshold must be in [0, 10]')
        self.initial_equity = initial_equity
        self.equity = initial_equity
        self.kill_threshold = kill_threshold

        self.adaptive_mode = adaptive_mode
        
        self.store = data_store if data_store is not None else DataStore()
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
        self.realized_equity = initial_equity
        self.equity_timestamps = []
        self._last_prices = {}
        self._current_day = None
        self._sod_equity = initial_equity
        self.constraints.reset_tracking(initial_equity)
        self.events = []
        self._pending = []
        self._daily_halted = False

    def _reset_run(self):
        """Isolate repeat runs without replacing tuned strategy/constraint objects."""
        self.equity = self.realized_equity = self.initial_equity
        self.peak_equity = self.initial_equity
        self.portfolio = Portfolio()
        self.equity_history = []
        self.equity_timestamps = []
        self.daily_returns = []
        self._last_prices = {}
        self._current_day = None
        self._sod_equity = self.initial_equity
        self.data_map = {}
        self.master_timeline = None
        self.constraints.reset_tracking(self.initial_equity)
        self.regime_detector._breadth_cache_id = None
        self.regime_detector._breadth_cache_source = None
        self.events = []
        self._pending = []
        self._daily_halted = False

    def _settle_closed_trades(self):
        self.portfolio.cleanup_positions()
        for pos in self.portfolio.closed_trades:
            if not getattr(pos, 'accounted_for', False):
                self.realized_equity += pos.calculate_net_pnl(self.cost_model)
                pos.accounted_for = True

    def _mark_equity(self):
        """Liquidation equity, including open PnL and estimated round-trip costs."""
        equity = self.realized_equity
        for pos in self.portfolio.open_positions:
            price, _ = self._last_prices.get(pos.ticker, (pos.entry, pos.entry_time))
            change = price - pos.entry if pos.direction == 'LONG' else pos.entry - price
            friction = self.cost_model.round_trip_cost(
                pos.entry, price, pos.shares, adv=pos.features.get('vol_ma_20', 0),
                direction=pos.direction,
            )
            equity += change * pos.shares - friction
        self.equity = equity

    def _record_equity(self, timestamp):
        if self.equity_timestamps and self.equity_timestamps[-1] == timestamp:
            self.equity_history[-1] = self.equity
        else:
            self.equity_timestamps.append(timestamp)
            self.equity_history.append(self.equity)
        self.peak_equity = max(self.peak_equity, self.equity)

    def _close_remaining(self, reason, timestamp=None):
        for pos in self.portfolio.open_positions:
            price, quote_time = self._last_prices.get(pos.ticker, (pos.entry, pos.entry_time))
            exit_time = quote_time if timestamp is None else timestamp
            pos.exit_quote_time = quote_time
            exit_reason = 'STALE_MARK_' + reason if quote_time < exit_time else reason
            pos._close(price, exit_reason, exit_time)
        self._settle_closed_trades()
        self._mark_equity()

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
        if nifty_df is None or nifty_df.empty:
            raise ValueError("Nifty five-minute data required; supply ^NSEI in the data store")
        nifty_df = nifty_df.copy()
        nifty_df.index = market_index(nifty_df.index)
        nifty_df = nifty_df.sort_index()
        if self.end_date is not None:
            nifty_df = nifty_df.loc[nifty_df.index + pd.Timedelta(seconds=config.CANDLE_INTERVAL_SECONDS) <= self.end_date]
        if nifty_df.empty:
            raise ValueError('No benchmark bars before the exclusive end date')
        if nifty_df.index.has_duplicates:
            raise ValueError("Duplicate Nifty timestamps")
        self.data_map["^NSEI"] = nifty_df

        def load_one(ticker):
            df = self.store.load_ticker(ticker)
            if df is not None and not df.empty:
                df = df.copy()
                df.index = market_index(df.index)
                df = df.sort_index()
                if self.end_date is not None:
                    df = df.loc[df.index + pd.Timedelta(seconds=config.CANDLE_INTERVAL_SECONDS) <= self.end_date]
                if df.index.has_duplicates:
                    raise ValueError(f"Duplicate timestamps for {ticker}")
                if len(df) < self.brain.MIN_RAW_BARS:
                    return ticker, None
                # V7.0: Pre-compute indicators for breadth
                df = self.brain.calculate_technicals(df)
                df['RS_Score'] = self._compute_rs_score_series(df, nifty_df)
                return ticker, df
            return ticker, None

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(load_one, self.tickers))

        all_timestamps = set(nifty_df.index)
        for ticker, df in results:
            if df is not None and not df.empty:
                self.data_map[ticker] = df
                all_timestamps = all_timestamps.union(set(df.index))
            else:
                raise ValueError(f'Missing data or insufficient indicator history for {ticker}')
        
        self.master_timeline = sorted(list(all_timestamps))
        self.master_timeline = [t for t in self.master_timeline if t >= self.start_date]
        if self.end_date is not None:
            self.master_timeline = [t for t in self.master_timeline if t < self.end_date]
        if not self.master_timeline:
            raise ValueError('No completed candles in the requested simulation window')
        self.constraints.update_price_data(self.data_map)

    def _calculate_portfolio_vol(self):
        """
        V7.0: Calculate realized annualized volatility based on equity history.
        Returns daily-equivalent vol if data exists, else default.
        """
        default = config.TARGET_PORTFOLIO_VOL / np.sqrt(252)
        if not self.equity_timestamps:
            return default
        # The execution model annualizes DAILY volatility by sqrt(252).
        # Five-minute changes must first be reduced to session closes.
        daily_equity = pd.Series(self.equity_history, index=self.equity_timestamps)
        daily_equity = daily_equity.groupby(daily_equity.index.normalize()).last()
        returns = daily_equity.pct_change(fill_method=None).dropna().tail(20)
        self.daily_returns = returns.tolist()
        if len(returns) < 20:
            return default
        vol = returns.std()
        return float(vol) if np.isfinite(vol) and vol > 0 else default

    def _check_gap_risk(self, ticker, timestamp, bar):
        """
        V7.0: Event/Gap Risk Filter
        Vetoes if the overnight gap is > 1.5x ATR.
        """
        ticker_df = self.data_map.get(ticker)
        idx = ticker_df.index.get_loc(timestamp)
        if idx == 0: return False
        
        prev_close = ticker_df.iloc[idx-1]['Close']
        prev_atr = ticker_df.iloc[idx-1].get('ATR', np.nan)
        if not np.isfinite(prev_atr) or prev_atr <= 0:
            return True
        
        gap = abs(bar['Open'] - prev_close)
        if prev_atr > 0 and gap > (config.GAP_VETO_MULT * prev_atr):
            return True # VETO
            
        return False

    def _event(self, timestamp, ticker, reason):
        self.events.append({'timestamp': timestamp.isoformat(), 'ticker': ticker, 'reason': reason})

    def _risk_gate(self, timestamp):
        self.peak_equity = max(self.peak_equity, self.equity)
        healthy, reason = self.constraints.check_system_health(self.equity)
        if not healthy:
            self._event(timestamp, None, reason)
            self._close_remaining('CIRCUIT_BREAKER', timestamp)
            self._pending = []
            return False
        daily_ok, reason = self.constraints.check_daily_loss_limit(self.equity, self._sod_equity)
        if not daily_ok and not self._daily_halted:
            self._daily_halted = True
            self._event(timestamp, None, reason)
        return True

    def _mark_prices(self, timestamp, field):
        for ticker, df in self.data_map.items():
            if ticker != '^NSEI' and timestamp in df.index:
                self._last_prices[ticker] = (float(df.at[timestamp, field]), timestamp)
        self._mark_equity()

    def _affordable_shares(self, order, signal):
        invested = sum(p.shares * p.entry for p in self.portfolio.open_positions)
        available = max(0.0, min(self.realized_equity, self.equity) - invested)
        entry = order['entry']
        lo, hi = 0, min(order['shares'], int(available // entry))
        # Reserve round-trip friction as well as notional; cost is monotone in
        # quantity, so a bounded integer search handles brokerage caps/impact.
        while lo < hi:
            quantity = (lo + hi + 1) // 2
            costs = self.cost_model.round_trip_cost(
                entry, entry, quantity, adv=signal.get('features', {}).get('vol_ma_20', 0),
                direction=signal['direction'])
            if entry * quantity + costs <= available:
                lo = quantity
            else:
                hi = quantity - 1
        return lo

    def _execute_pending(self, timestamp):
        pending, self._pending = self._pending, []
        interval = pd.Timedelta(seconds=config.CANDLE_INTERVAL_SECONDS)
        for signal in pending:
            ticker = signal['ticker']
            signal_time = signal['signal_time']
            if (timestamp != signal_time + interval or timestamp.date() != signal_time.date()
                    or not is_entry_time(timestamp)):
                self._event(timestamp, ticker, 'SIGNAL_EXPIRED')
                continue
            if self._daily_halted:
                self._event(timestamp, ticker, 'DAILY_HALT')
                continue
            df = self.data_map.get(ticker)
            if df is None or timestamp not in df.index:
                self._event(timestamp, ticker, 'MISSING_EXECUTION_BAR')
                continue
            bar = df.loc[timestamp]
            if self._check_gap_risk(ticker, timestamp, bar):
                self._event(timestamp, ticker, 'GAP_VETO')
                continue
            drawdown = (self.peak_equity - self.equity) / self.peak_equity
            order = self.exec_model.generate_orders(
                signal, portfolio_vol=self._calculate_portfolio_vol(), current_dd=drawdown,
                entry_price=float(bar['Open']), equity=self.equity)
            if not order['valid_rr']:
                self._event(timestamp, ticker, 'INVALID_ORDER_AT_OPEN')
                continue
            order['shares'] = self._affordable_shares(order, signal)
            if order['shares'] <= 0:
                self._event(timestamp, ticker, 'INSUFFICIENT_CAPITAL_AFTER_COSTS')
                continue
            allowed, reason = self.constraints.can_open_trade(
                self.portfolio, ticker, self.equity, timestamp=signal_time, order=order)
            if not allowed:
                self._event(timestamp, ticker, reason)
                continue
            features = dict(signal.get('features', {}))
            features['breadth_score'] = signal['breadth_score']
            features.setdefault('atr_val', signal['atr'])
            self.portfolio.open_trade(
                trade_id=f"{ticker}_{timestamp.strftime('%Y%m%d%H%M')}", ticker=ticker,
                direction=signal['direction'], entry=order['entry'], stop=order['stop'],
                target=order['target'], r_unit=abs(order['entry'] - order['stop']),
                shares=order['shares'], timestamp=timestamp, features=features)
            self.portfolio.open_positions[-1].signal_time = signal_time
            self._event(timestamp, ticker, 'FILLED_NEXT_OPEN')
            self._mark_equity()
            if not self._risk_gate(timestamp):
                return False
        return True

    def _queue_signals(self, timestamp):
        interval = pd.Timedelta(seconds=config.CANDLE_INTERVAL_SECONDS)
        if (self._daily_halted or not is_entry_time(timestamp)
                or not is_entry_time(timestamp + interval)
                or (self.end_date is not None and timestamp + interval >= self.end_date)):
            return
        nifty = self.data_map['^NSEI']
        if timestamp not in nifty.index:
            return
        nifty_slice = nifty.loc[:timestamp].tail(self.warmup + 1)
        if len(nifty_slice) < self.regime_detector.primary_period:
            return
        row = nifty.loc[timestamp]
        coverage = sum(timestamp in self.data_map[t].index for t in self.tickers) / max(1, len(self.tickers))
        if coverage < config.MIN_BREADTH_COVERAGE:
            self._event(timestamp, None, 'INSUFFICIENT_BREADTH_COVERAGE')
            return
        breadth = self.regime_detector.get_breadth_score(self.data_map, timestamp)
        state = self.regime_detector.detect(
            row['Close'], row[f'EMA_{self.regime_detector.primary_period}'],
            row[f'EMA_{self.regime_detector.gatekeeper_period}'], breadth_score=breadth)
        threshold = self.kill_threshold
        if self.adaptive_mode:
            threshold = 4.0 if state['regime'] == 'Bullish' else 6.0
        for ticker in self.tickers:
            if self.portfolio.is_invested(ticker):
                continue
            df = self.data_map.get(ticker)
            if df is None or timestamp not in df.index:
                continue
            signal = self.brain.analyze_slice(
                ticker, df.loc[:timestamp].tail(self.warmup + 1), nifty_slice,
                state['regime'], technicals_ready=True)
            if not signal or not np.isfinite(signal['kill_score']) or signal['kill_score'] < threshold:
                continue
            authorized = ((signal['direction'] == 'LONG' and state['longs_authorized'])
                          or (signal['direction'] == 'SHORT' and state['shorts_authorized']))
            if not authorized:
                self._event(timestamp, ticker, 'DIRECTION_VETO')
                continue
            self._pending.append(dict(signal, signal_time=timestamp, breadth_score=breadth))
        self._pending.sort(key=lambda s: (-s['kill_score'], s['ticker']))

    def run(self):
        self._reset_run()
        self.load_universe()
        self.regime_detector.calculate_emas(self.data_map['^NSEI'])
        log.info('Starting simulation with next-bar-open fills (equity %.2f)', self.equity)
        for timestamp in self.master_timeline:
            if self._current_day != timestamp.date():
                self._current_day = timestamp.date()
                self._sod_equity = self.equity
                self._daily_halted = False

            # Opening quotes are the ONLY current-bar information available to
            # execution. Intrabar exits cannot finance an earlier opening fill.
            self._mark_prices(timestamp, 'Open')
            for pos in self.portfolio.open_positions:
                quote = self._last_prices.get(pos.ticker)
                if quote and quote[1] == timestamp:
                    if pos.entry_time.date() < timestamp.date():
                        pos._close(quote[0], 'MISSED_EOD', timestamp)
                    else:
                        pos.update_open(quote[0], timestamp)
            self._settle_closed_trades()
            self._mark_equity()
            if is_flatten_time(timestamp):
                self._close_remaining('EOD_FLATTEN', timestamp)
            if not self._risk_gate(timestamp) or not self._execute_pending(timestamp):
                self._record_equity(timestamp)
                break

            for pos in self.portfolio.open_positions:
                df = self.data_map.get(pos.ticker)
                if df is not None and timestamp in df.index:
                    pos.update(df.loc[timestamp], timestamp)
            self._settle_closed_trades()
            self._mark_prices(timestamp, 'Close')
            self._record_equity(timestamp)
            if not self._risk_gate(timestamp):
                self._record_equity(timestamp)
                break
            self._queue_signals(timestamp)

        if self.portfolio.open_positions:
            self._close_remaining('END_OF_DATA', self.equity_timestamps[-1])
            self._record_equity(self.equity_timestamps[-1])
        for signal in self._pending:
            self._event(signal['signal_time'], signal['ticker'], 'NO_NEXT_BAR')
        self._pending = []
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
                "Held": pos.bars_held,
                "trade_id": pos.trade_id,
                "direction": pos.direction,
                "signal_time": getattr(pos, 'signal_time', None),
                "entry_time": pos.entry_time,
                "exit_time": pos.exit_time,
                "exit_quote_time": getattr(pos, 'exit_quote_time', pos.exit_time),
                "entry": pos.entry, "exit": pos.exit_price,
                "initial_stop": pos.initial_stop, "shares": pos.shares,
            })
        
        df = pd.DataFrame(results, columns=['ticker', 'PnL', 'Net_R', 'Reason', 'Held',
                          'trade_id', 'direction', 'signal_time', 'entry_time', 'exit_time',
                          'exit_quote_time', 'entry', 'exit', 'initial_stop', 'shares'])
        if not df.empty:
            log.info("Trades: %d | Final Equity: ₹%.2f | Net Return: %.1f%%",
                     len(df), self.equity, ((self.equity/self.initial_equity)-1)*100)
        return df
