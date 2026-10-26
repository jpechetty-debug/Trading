"""
Portfolio Constraints V6.5 — Risk management with:
  - Max position limits
  - Sector exposure caps  
  - Real return-based correlation clustering (replaces sector proxy)
  - Drawdown circuit breaker
  - Daily loss circuit breaker
"""
import pandas as pd
import numpy as np
import os
from pathlib import Path
import src.config as config
from src.logger import get_logger

log = get_logger(__name__)

# Correlation parameters
CORRELATION_LOOKBACK = 20       # 20-bar rolling window for return correlations
CORRELATION_THRESHOLD = 0.75    # Pairs with corr > 0.75 are treated as same cluster
MAX_CLUSTER_EXPOSURE_PCT = 0.4  # Max 40% of risk capacity in a single cluster


class PortfolioConstraints:
    def __init__(self, sector_file=None, max_positions=5, max_dd_pct=None):
        self.max_positions = max_positions
        self.max_drawdown = config.MAX_DD_THRESHOLD if max_dd_pct is None else max_dd_pct
        self.max_sector_exposure = 0.4
        if not isinstance(max_positions, int) or max_positions <= 0:
            raise ValueError('max_positions must be a positive integer')
        if not np.isfinite(self.max_drawdown) or not 0 < self.max_drawdown < 1:
            raise ValueError('max_dd_pct must be in (0, 1)')

        # Load Sector Map (still used as fallback when price data unavailable)
        self.sector_map = self._load_sector_map(
            sector_file if sector_file is not None else Path(__file__).resolve().parents[2] / 'data/nse_sectors.csv'
        )

        # V6.5: Correlation Cache
        self._correlation_cache = {}        # {(ticker_a, ticker_b): correlation}
        self._cache_timestamp = None        # When cache was last computed
        self._price_data = {}               # {ticker: pd.Series of close prices}

        # Drawdown Tracking
        self.peak_equity = 100000.0

    def reset_tracking(self, initial_equity):
        """Reset per-run state while preserving configured limits and sectors."""
        self.peak_equity = initial_equity
        self._correlation_cache.clear()
        self._price_data.clear()
        self._cache_timestamp = None

    def _load_sector_map(self, filepath):
        if os.path.exists(filepath):
            try:
                df = pd.read_csv(filepath, index_col=0)
                return df['Sector'].to_dict()
            except Exception:
                log.warning("Could not parse nse_sectors.csv. Using fallback.")

        return {
            "HDFCBANK.NS": "BANK", "ICICIBANK.NS": "BANK", "SBIN.NS": "BANK", "AXISBANK.NS": "BANK",
            "RELIANCE.NS": "ENERGY", "ONGC.NS": "ENERGY", "NTPC.NS": "ENERGY",
            "TCS.NS": "IT", "INFY.NS": "IT", "WIPRO.NS": "IT"
        }

    def update_price_data(self, data_map: dict):
        """
        V6.5: Ingest price data for correlation computation.
        Called once by PortfolioEngine after loading the universe.
        
        Args:
            data_map: {ticker: DataFrame with 'Close' column}
        """
        self._price_data.clear()
        self._correlation_cache.clear()
        self._cache_timestamp = None
        for ticker, df in data_map.items():
            if ticker == "^NSEI":
                continue
            if df is not None and 'Close' in df.columns:
                self._price_data[ticker] = df['Close']
        log.info("Correlation engine loaded %d tickers.", len(self._price_data))

    def _compute_pairwise_correlation(self, ticker_a: str, ticker_b: str,
                                       as_of: "pd.Timestamp | None" = None) -> float:
        """
        Compute rolling return correlation between two tickers, using only
        data available as of `as_of` (point-in-time — no look-ahead).

        Returns 0.0 (via sector proxy) if data is insufficient.
        """
        # Cache is keyed by (pair, as_of) so a query at time T never reuses
        # a correlation computed with data from T' > T ("future" for T).
        if self._cache_timestamp != as_of:
            self._correlation_cache.clear()
            self._cache_timestamp = as_of
        pair_key = (tuple(sorted([ticker_a, ticker_b])), as_of)
        if pair_key in self._correlation_cache:
            return self._correlation_cache[pair_key]

        series_a = self._price_data.get(ticker_a)
        series_b = self._price_data.get(ticker_b)

        if series_a is None or series_b is None:
            # Fallback to sector-based proxy
            return self._sector_correlation_proxy(ticker_a, ticker_b)

        # CRITICAL: restrict to data on/before `as_of` before doing anything
        # else. Without this, .tail(CORRELATION_LOOKBACK) below would pull
        # the last N bars of the *entire* stored series (which may extend
        # well past the current point in a backtest) — i.e. look-ahead bias.
        if as_of is not None:
            series_a = series_a.loc[:as_of]
            series_b = series_b.loc[:as_of]

        # Align and compute returns
        combined = pd.DataFrame({'a': series_a, 'b': series_b}).dropna()
        if len(combined) < CORRELATION_LOOKBACK + 1:
            return self._sector_correlation_proxy(ticker_a, ticker_b)

        returns_a = combined['a'].pct_change().tail(CORRELATION_LOOKBACK)
        returns_b = combined['b'].pct_change().tail(CORRELATION_LOOKBACK)

        corr = returns_a.corr(returns_b)
        if np.isnan(corr):
            corr = self._sector_correlation_proxy(ticker_a, ticker_b)

        # Cache the result
        self._correlation_cache[pair_key] = corr
        return corr

    def _sector_correlation_proxy(self, ticker_a: str, ticker_b: str) -> float:
        """Fallback: same sector = 0.8 assumed correlation, different = 0.2."""
        sector_a = self.sector_map.get(ticker_a, "OTHER")
        sector_b = self.sector_map.get(ticker_b, "OTHER")
        if sector_a == sector_b and sector_a != "OTHER":
            return 0.80
        return 0.20

    def check_system_health(self, current_equity):
        """V6.1: The Portfolio Circuit Breaker."""
        if not np.isfinite(current_equity) or not np.isfinite(self.peak_equity) or self.peak_equity <= 0:
            return False, 'INVALID_EQUITY'
        self.peak_equity = max(self.peak_equity, current_equity)
        drawdown = (self.peak_equity - current_equity) / self.peak_equity

        if current_equity <= 0 or drawdown >= self.max_drawdown:
            return False, f"DRAWDOWN_BREAKER_TRIGGERED_(-{drawdown*100:.1f}%)"
        return True, "OK"

    def check_daily_loss_limit(self, current_equity, sod_equity):
        """Daily Circuit Breaker (2R Loss Limit)."""
        if not np.isfinite(current_equity) or not np.isfinite(sod_equity) or sod_equity <= 0:
            return False, 'INVALID_EQUITY'
        daily_loss = sod_equity - current_equity
        # Match the equity-capped trade budget. A small account must not inherit
        # a daily allowance based on the much larger nominal rupee budget.
        risk_limit = 2 * min(config.RISK_PER_TRADE, sod_equity * config.MAX_TRADE_RISK_PCT)

        if daily_loss >= risk_limit:
            return False, f"DAILY_LOSS_LIMIT_REACHED_(-₹{daily_loss:.2f})"
        return True, "OK"

    def check_correlation_cluster(self, new_ticker, open_positions, total_equity, timestamp=None):
        """
        V6.5 UPGRADE: Real Return-Based Correlation Cluster Filter
        
        Sums risk exposure from all open positions with correlation > 0.75
        to the proposed new ticker. Vetoes if cluster exposure exceeds 40%
        of total risk capacity.

        `timestamp`: current sim time. Correlation is computed point-in-time
        (data on/before this timestamp only) to avoid look-ahead bias.
        """
        if not open_positions:
            return True, "OK"

        cluster_exposure = 0
        risk_per_trade = config.RISK_PER_TRADE
        correlated_tickers = []

        for pos in open_positions:
            corr = self._compute_pairwise_correlation(new_ticker, pos.ticker, as_of=timestamp)

            if corr >= CORRELATION_THRESHOLD:
                cluster_exposure += risk_per_trade
                correlated_tickers.append(f"{pos.ticker}({corr:.2f})")

        max_cluster_risk = MAX_CLUSTER_EXPOSURE_PCT * self.max_positions * risk_per_trade

        if cluster_exposure + risk_per_trade > max_cluster_risk:
            cluster_str = ", ".join(correlated_tickers)
            return False, f"CORRELATION_CLUSTER_BREACH_({cluster_str})"

        return True, "OK"

    def can_open_trade(self, portfolio, ticker, current_equity, timestamp=None, order=None):
        if not np.isfinite(current_equity) or current_equity <= 0:
            return False, 'INVALID_EQUITY'
        if portfolio.is_invested(ticker):
            return False, 'DUPLICATE_POSITION'
        if len(portfolio.open_positions) >= self.max_positions:
            return False, "MAX_POSITIONS"

        # 1. Sector Cap (Hard Label)
        sector = self.sector_map.get(ticker, "OTHER")
        current_sector_count = 0
        for pos in portfolio.open_positions:
            if self.sector_map.get(pos.ticker, "OTHER") == sector:
                current_sector_count += 1

        if (current_sector_count + 1) / self.max_positions > self.max_sector_exposure:
            return False, f"SECTOR_FULL_({sector})"

        # The execution-time gate uses actual initial stop-distance exposure.
        # Count-only calls remain available as a preliminary screen.
        if order is not None:
            new_risk = abs(order['entry'] - order['stop']) * order['shares']
            if not np.isfinite(new_risk) or new_risk <= 0:
                return False, 'INVALID_ORDER_RISK'
            if new_risk > current_equity * config.MAX_TRADE_RISK_PCT + 1e-8:
                return False, 'TRADE_RISK_CAP'
            exposures = [(pos, abs(pos.entry - pos.initial_stop) * pos.shares)
                         for pos in portfolio.open_positions]
            if any(not np.isfinite(risk) or risk < 0 for _, risk in exposures):
                return False, 'INVALID_POSITION_RISK'
            capacity = current_equity * config.MAX_PORTFOLIO_RISK_PCT
            if sum(risk for _, risk in exposures) + new_risk > capacity + 1e-8:
                return False, 'PORTFOLIO_RISK_CAP'
            sector_risk = sum(risk for pos, risk in exposures
                              if self.sector_map.get(pos.ticker, 'OTHER') == sector)
            if sector_risk + new_risk > capacity * self.max_sector_exposure + 1e-8:
                return False, 'SECTOR_RISK_CAP'
            cluster_risk = sum(risk for pos, risk in exposures
                               if self._compute_pairwise_correlation(ticker, pos.ticker, as_of=timestamp)
                               >= CORRELATION_THRESHOLD)
            if cluster_risk + new_risk > capacity * MAX_CLUSTER_EXPOSURE_PCT + 1e-8:
                return False, 'CORRELATION_RISK_CAP'
            return True, 'APPROVED'

        # 2. Correlation Cluster (Returns-Based with Sector Fallback)
        is_cluster_ok, cluster_reason = self.check_correlation_cluster(
            ticker, portfolio.open_positions, current_equity, timestamp=timestamp
        )
        if not is_cluster_ok:
            return False, cluster_reason

        return True, "APPROVED"
