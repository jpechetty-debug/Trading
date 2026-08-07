# src/regime.py
"""
Centralized Market Regime Detection for Indian Stock AI V6.5.

Resolves the inconsistency where backtest_engine, portfolio_engine, and scanner
each used different EMA periods (20 vs 50) for regime classification.

Single Source of Truth for:
  - Primary regime (Bullish / Bearish / Neutral)
  - Intermediate trend gatekeeper (20-EMA veto for longs)
"""
import pandas as pd
from src.logger import get_logger

log = get_logger(__name__)

# Canonical EMA periods — change here, change everywhere.
PRIMARY_EMA_PERIOD = 50
GATEKEEPER_EMA_PERIOD = 20


class RegimeDetector:
    """
    Unified regime detection used by BacktestEngine, PortfolioEngine, and Scanner.

    Two-layer filter:
      1. PRIMARY REGIME: Nifty Close vs 50-EMA → Bullish / Bearish
      2. GATEKEEPER:     Nifty Close vs 20-EMA → Vetoes longs when broken
    """

    def __init__(self, primary_period: int = PRIMARY_EMA_PERIOD, gatekeeper_period: int = GATEKEEPER_EMA_PERIOD):
        self.primary_period = primary_period
        self.gatekeeper_period = gatekeeper_period

    def calculate_emas(self, nifty_df: pd.DataFrame) -> pd.DataFrame:
        """
        Pre-compute EMAs on a Nifty DataFrame. Call once, then use detect() per bar.
        Mutates the DataFrame in-place for performance.
        """
        nifty_df[f'EMA_{self.primary_period}'] = nifty_df['Close'].ewm(span=self.primary_period, adjust=False).mean()
        nifty_df[f'EMA_{self.gatekeeper_period}'] = nifty_df['Close'].ewm(span=self.gatekeeper_period, adjust=False).mean()
        return nifty_df

    def detect(self, nifty_close: float, nifty_ema_primary: float, nifty_ema_gatekeeper: float, breadth_score: float = 1.0) -> dict:
        """
        Classify the current regime from pre-computed values.

        Returns:
            {
                "regime": "Bullish" | "Bearish",
                "longs_authorized": True | False,
                "shorts_authorized": True | False,
                "breadth_score": float
            }
        """
        regime = "Bullish" if nifty_close > nifty_ema_primary else "Bearish"
        
        # V7.0: Breadth Filter
        # Veto longs if breadth < 35%
        breadth_ok = breadth_score >= 0.35
        
        longs_ok = (nifty_close > nifty_ema_gatekeeper) and breadth_ok
        shorts_ok = True  # No gatekeeper for shorts currently

        return {
            "regime": regime,
            "longs_authorized": longs_ok,
            "shorts_authorized": shorts_ok,
            "breadth_score": breadth_score
        }

    def get_breadth_score(self, universe_data: dict, timestamp: pd.Timestamp) -> float:
        """
        V7.0: Calculate % of stocks above their 50-EMA at a given timestamp.
        Optimized to compute vector-wide breadth series and cache it for the
        lifetime of `universe_data`.

        Correctness note: tickers whose Series have a different (or shorter)
        index than their peers result in NaN cells after pd.DataFrame
        union-alignment. A NaN comparison (close > ema) evaluates to False,
        which would silently count absent tickers as "not above EMA" and
        deflate breadth. We therefore build an explicit validity mask
        (close.notna() & ema.notna()) and divide by valid-cell count per
        row, not the full column count.
        """
        if not universe_data:
            return 0.5

        if getattr(self, '_breadth_cache_id', None) != id(universe_data):
            closes = {}
            emas = {}
            for ticker, df in universe_data.items():
                if ticker == "^NSEI":
                    continue
                if 'EMA_50' in df.columns:
                    closes[ticker] = df['Close']
                    emas[ticker] = df['EMA_50']

            if not closes:
                self._breadth_series = pd.Series(dtype=float)
            else:
                close_df = pd.DataFrame(closes)
                ema_df = pd.DataFrame(emas)

                # valid_mask is True only where *both* Close and EMA_50 exist
                # for a given (timestamp, ticker) cell. This excludes tickers
                # that were halted, have shorter history, or are otherwise
                # missing a bar — rather than treating them as "below EMA".
                valid_mask = close_df.notna() & ema_df.notna()
                above_mask = (close_df > ema_df) & valid_mask

                valid_count = valid_mask.sum(axis=1)
                above_count = above_mask.sum(axis=1)

                # Where no ticker had data (valid_count == 0), return NaN so
                # the caller's fallback 0.5 is used cleanly.
                self._breadth_series = above_count.where(valid_count > 0) / valid_count.where(valid_count > 0)

            self._breadth_cache_id = id(universe_data)

        if timestamp in self._breadth_series.index:
            val = self._breadth_series.loc[timestamp]
            if pd.notna(val):
                return float(val)
        return 0.5

    def detect_from_slice(self, nifty_slice: pd.DataFrame) -> dict:
        """
        Convenience method: computes EMAs from a slice then detects regime.
        Used when you don't want to pre-compute EMAs (e.g., Scanner).
        """
        if nifty_slice.empty or len(nifty_slice) < self.primary_period:
            log.warning("Nifty slice too short (%d bars) for regime detection.", len(nifty_slice))
            return {
                "regime": "Neutral",
                "longs_authorized": False,
                "shorts_authorized": False,
            }

        close = nifty_slice['Close'].iloc[-1]
        ema_primary = nifty_slice['Close'].ewm(span=self.primary_period, adjust=False).mean().iloc[-1]
        ema_gatekeeper = nifty_slice['Close'].ewm(span=self.gatekeeper_period, adjust=False).mean().iloc[-1]

        return self.detect(close, ema_primary, ema_gatekeeper)
