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
import src.config as config
from src.logger import get_logger

log = get_logger(__name__)

# Correlation parameters
CORRELATION_LOOKBACK = 20       # 20-bar rolling window for return correlations
CORRELATION_THRESHOLD = 0.75    # Pairs with corr > 0.75 are treated as same cluster
MAX_CLUSTER_EXPOSURE_PCT = 0.4  # Max 40% of risk capacity in a single cluster


class PortfolioConstraints:
    def __init__(self, sector_file="data/nse_sectors.csv", max_positions=5, max_dd_pct=0.15):
        self.max_positions = max_positions
        self.max_drawdown = max_dd_pct
        self.max_sector_exposure = 0.4

        # Load Sector Map (still used as fallback when price data unavailable)
        self.sector_map = self._load_sector_map(sector_file)

        # V6.5: Correlation Cache
        self._correlation_cache = {}        # {(ticker_a, ticker_b): correlation}
        self._cache_timestamp = None        # When cache was last computed
        self._price_data = {}               # {ticker: pd.Series of close prices}

        # Drawdown Tracking
        self.peak_equity = 100000.0

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
        self.peak_equity = max(self.peak_equity, current_equity)
        drawdown = (self.peak_equity - current_equity) / self.peak_equity

        if drawdown > self.max_drawdown:
            return False, f"DRAWDOWN_BREAKER_TRIGGERED_(-{drawdown*100:.1f}%)"
        return True, "OK"

    def check_daily_loss_limit(self, current_equity, sod_equity):
        """Daily Circuit Breaker (2R Loss Limit)."""
        daily_loss = sod_equity - current_equity
        risk_limit = 2 * config.RISK_PER_TRADE

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

    def can_open_trade(self, portfolio, ticker, current_equity, timestamp=None):
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

        # 2. Correlation Cluster (Returns-Based with Sector Fallback)
        is_cluster_ok, cluster_reason = self.check_correlation_cluster(
            ticker, portfolio.open_positions, current_equity, timestamp=timestamp
        )
        if not is_cluster_ok:
            return False, cluster_reason

        return True, "APPROVED"
