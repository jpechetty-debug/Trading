import yfinance as yf
import pandas as pd
import pandas_ta as ta
import numpy as np
import src.config as config
from src.logger import get_logger
from src.models import Features

log = get_logger(__name__)

class BrainAV5:
    def __init__(self, rs_lookback=config.RS_LOOKBACK):
        self.interval = "5m"
        self.rs_lookback = rs_lookback

    def fetch_data(self, ticker):
        """
        Canonical Data Fetch Pattern
        """
        try:
            # Fix: Don't mess up Indices
            if not ticker.startswith("^") and not ticker.endswith(".NS") and not ticker.endswith(".BO"):
                ticker = f"{ticker}.NS"
            
            log.info("Downloading %s...", ticker)
            df = yf.download(
                ticker, 
                period="5d", 
                interval="5m", 
                progress=False, 
                threads=False
            )
            
            if df.empty:
                log.warning("Empty DataFrame for %s", ticker)
                return None
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if len(df) < 50: return None
            
            return df
        except Exception as e:
            log.error("Data Pipeline Failure (%s): %s", ticker, e)
            return None

    def calculate_technicals(self, df):
        # FIX: the function docstring/changelog claimed this "returns a new
        # DataFrame to avoid mutating the caller's data", but that was only
        # true of the *return value* — every `df['X'] = ...` line below was
        # still mutating the caller's original object in place (verified:
        # calling this on a fresh df left 14 new columns bolted onto the
        # caller's copy even though the returned object had different row
        # count after dropna()). Some call sites defensively did
        # `calculate_technicals(x.copy())` to work around this; others
        # didn't. Copying once here makes the function honest regardless of
        # what any given caller remembers to do.
        df = df.copy()
        df['EMA_20'] = ta.ema(df['Close'], length=20)
        df['EMA_50'] = ta.ema(df['Close'], length=50)
        df['EMA_200'] = ta.ema(df['Close'], length=200)
        df['ATR'] = ta.atr(df['High'], df['Low'], df['Close'], length=14)
        df['VWAP'] = ta.vwap(df['High'], df['Low'], df['Close'], df['Volume'])
        
        # UPGRADE 6.4: Advanced Metrics
        df['Turnover'] = df['Close'] * df['Volume']
        df['Resistance'] = df['High'].rolling(window=20).max()
        df['Support'] = df['Low'].rolling(window=20).min()
        
        # Realized Volatility (Std Dev of returns)
        df['Realized_Vol_20'] = df['Close'].pct_change().rolling(20).std()
        df['Realized_Vol_60'] = df['Close'].pct_change().rolling(60).std()
        
        # UPGRADE: Structural Swings (for Stop Loss)
        df['Swing_High'] = df['High'].rolling(window=3).max()
        df['Swing_Low'] = df['Low'].rolling(window=3).min()
        
        # Volume MAs (must be computed BEFORE dropna)
        df['Vol_MA_20'] = ta.sma(df['Volume'], length=20)
        df['Turnover_MA_20'] = ta.sma(df['Turnover'], length=20)

        # FIX V6.5: NaN Protection — AFTER all indicators are computed
        # FIX V7.1: Return a new DataFrame to avoid mutating the caller's data
        df = df.dropna()
        
        return df

    def resample_to_15m(self, df):
        logic = {'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}
        df_15m = df.resample('15min').apply(logic).dropna()
        df_15m['EMA_20'] = ta.ema(df_15m['Close'], length=20)
        return df_15m

    # --- INSTITUTIONAL UPGRADES ---

    def calculate_relative_strength(self, stock_df, nifty_df, lookback=None):
        """
        Base RS Score (Log Returns)
        """
        if lookback is None: lookback = self.rs_lookback
        if nifty_df is None or stock_df is None:
            return 0.0
        # Inner join to align timestamps perfectly
        merged = stock_df[['Close']].join(
            nifty_df[['Close']], 
            how="inner", 
            lsuffix="_stock", 
            rsuffix="_nifty"
        )

        if len(merged) < lookback + 1: return 0.0

        stock_ret = np.log(merged['Close_stock'] / merged['Close_stock'].shift(lookback)).iloc[-1]
        nifty_ret = np.log(merged['Close_nifty'] / merged['Close_nifty'].shift(lookback)).iloc[-1]

        return round((stock_ret - nifty_ret) * 100, 2)

    def calculate_rs_slope(self, stock_df, nifty_df, lookback=None):
        """
        UPGRADE 1: RS Trend (Slope)
        Checks if RS is accelerating over the last 5 candles.
        """
        if lookback is None: lookback = self.rs_lookback
        if nifty_df is None or stock_df is None:
            return 0.0

        # FIX: align stock and nifty by timestamp ONCE, up front, then slice
        # the aligned frame positionally. The previous version sliced
        # stock_df.iloc[:-i] and nifty_df.iloc[:-i] independently ("Assuming
        # aligned via previous fetch logic roughly") — if the two frames
        # have different bar counts (a missing candle on one side, holidays,
        # partial data, etc.) that positional slicing silently compares the
        # wrong timestamps against each other.
        merged = stock_df[['Close']].join(
            nifty_df[['Close']], how="inner", lsuffix="_stock", rsuffix="_nifty"
        )

        scores = []
        # Calculate RS for previous 5 steps to see the trend
        for i in range(5, 0, -1):
            if len(merged) <= i + lookback:
                return 0.0
            m_slice = merged.iloc[:-i]
            stock_ret = np.log(m_slice['Close_stock'] / m_slice['Close_stock'].shift(lookback)).iloc[-1]
            nifty_ret = np.log(m_slice['Close_nifty'] / m_slice['Close_nifty'].shift(lookback)).iloc[-1]
            scores.append(round((stock_ret - nifty_ret) * 100, 2))

        if len(scores) < 3: return 0.0
        
        # Polyfit to find slope (Trajectory)
        slope = np.polyfit(range(len(scores)), scores, 1)[0]
        return slope

    def is_nr7(self, df):
        ranges = (df['High'] - df['Low']).tail(7)
        return ranges.iloc[-1] == ranges.min()

    def is_ignition_bar(self, df):
        """
        UPGRADE 2: ATR-Normalized Ignition
        Filters out fake doji breakouts.
        """
        curr = df.iloc[-1]
        avg_vol = df['Volume'].rolling(20).mean().iloc[-1]
        
        # 1. Volume Criteria
        vol_check = curr['Volume'] > (3 * avg_vol)
        
        # 2. Body Criteria (Decisive Move)
        candle_range = curr['High'] - curr['Low']
        if candle_range == 0: return False
        body_check = (abs(curr['Close'] - curr['Open']) / candle_range) > 0.7
        
        # 3. ATR Expansion Check (New!)
        # The move must be significant relative to recent volatility
        atr_check = candle_range > (0.8 * curr['ATR'])
        
        return vol_check and body_check and atr_check

    def calculate_kill_score(self, df, df_15m, market_regime, rs_score, rs_slope):
        """
        KILL SCORE V6.4 (Adaptive Institutional Grade)
        """
        row = df.iloc[-1]
        score = 0
        reasons = []

        if row['ATR'] <= 0: return 0, ["Invalid ATR"], "NEUTRAL", {}

        # --- V6.4 UPGRADE: Volatility Regime Adjustment ---
        vol_20 = row['Realized_Vol_20']
        vol_60 = row['Realized_Vol_60']
        
        weight_patterns = config.SCORE_PATTERNS
        weight_structure = config.SCORE_STRUCTURE
        
        if vol_20 > 1.5 * vol_60:  # High vol regime
             weight_patterns *= 1.3
             weight_structure *= 0.8
             reasons.append("⚡ High Vol Regime (Pattern Heavy)")
        else:  # Low vol / compression regime
             weight_structure *= 1.3
             weight_patterns *= 0.8
             reasons.append("❄️ Low Vol Regime (Structure Heavy)")

        # 1. Structure
        price = row['Close']
        ema = row['EMA_20']
        vwap = row['VWAP']
        
        is_bull = price > ema and price > vwap
        is_bear = price < ema and price < vwap

        direction = "NEUTRAL"
        if is_bull: direction = "LONG"; score += weight_structure; reasons.append("Bull Structure")
        elif is_bear: direction = "SHORT"; score += weight_structure; reasons.append("Bear Structure")

        # 2. Relative Strength
        if rs_score > config.RS_EXTENSION_THRESHOLD:
            score += config.PENALTY_EXTENSION 
            reasons.append(f"⚠️ Overextended (RS {rs_score:.2f})")
        elif config.RS_SWEET_SPOT_LOW <= rs_score <= config.RS_SWEET_SPOT_HIGH:
            if direction == "LONG": 
                score += config.SCORE_SWEET_SPOT
                reasons.append(f"✅ Prime RS Zone ({rs_score:.2f})")
            else:
                score -= 1
        else:
            if direction == "LONG": score -= 1
        
        # Slope Bonus
        if direction == "LONG" and rs_slope > config.RS_SLOPE_THRESHOLD:
            score += 1

        # 3. Pattern Recognition
        if self.is_nr7(df):
            prev_h = df['High'].iloc[-2]
            prev_l = df['Low'].iloc[-2]
            if direction == "LONG" and price > prev_h: score += weight_patterns; reasons.append("🧨 NR7 Breakout")
            elif direction == "SHORT" and price < prev_l: score += weight_patterns; reasons.append("🧨 NR7 Breakdown")

        if self.is_ignition_bar(df):
            score += weight_patterns
            reasons.append("🚀 True Vol Ignition")

        # 4. Context
        if (direction == "LONG" and market_regime == "Bullish") or \
           (direction == "SHORT" and market_regime == "Bearish"):
            score += config.SCORE_CONTEXT; reasons.append("Market Aligned")

        # 5. V6.4 UPGRADE: Institutional Liquidity Filter
        # combined ADV/Turnover/Spread check
        # spread_proxy: High-Low vs ATR
        spread_proxy = (row['High'] - row['Low']) / row['ATR'] if row['ATR'] > 0 else 0
        
        liquidity_ok = (
            row['Vol_MA_20'] > config.ADV_SHARE_FLOOR and
            row['Turnover_MA_20'] > config.ADV_TURNOVER_FLOOR and
            spread_proxy < 1.5 # Proxy for manageable spread
        )

        if not liquidity_ok:
            score += config.PENALTY_LIQUIDITY
            reasons.append("💧 Insufficient Institutional Liquidity")

        # --- FEATURE EXTRACTION (Phase 2) ---
        features = Features(
            rs_score=rs_score,
            rs_slope=rs_slope,
            atr_val=row['ATR'],
            vol_ratio=round(row['Volume'] / row['Vol_MA_20'], 2) if row['Vol_MA_20'] > 0 else 0,
            vol_ma_20=row['Vol_MA_20'],
            dist_ema=round((price - ema) / ema * 100, 2) if ema > 0 else 0,
            candle_range_atr=round((row['High'] - row['Low']) / row['ATR'], 2) if row['ATR'] > 0 else 0,
            resistance=row['Resistance'],
            support=row['Support'],
            market_regime=market_regime
        )

        return max(0, min(10, score)), reasons, direction, features.to_dict()

    def analyze_slice(self, ticker, stock_slice, nifty_slice, market_regime):
        """
        PURE FUNCTION: No API calls. 100% Deterministic.
        Used for both Live Trading and Backtesting.
        """
        # 1. Compute Technicals on the slice
        df = self.calculate_technicals(stock_slice.copy())
        
        # 2. Resample (Simulating 15m context)
        df_15m = self.resample_to_15m(df)
        
        if len(df) < 50 or df_15m.empty: return None

        # 3. RS Calcs
        rs_score = self.calculate_relative_strength(df, nifty_slice)
        rs_slope = self.calculate_rs_slope(df, nifty_slice)

        # 4. Scoring
        kill_score, reasons, direction, features = self.calculate_kill_score(
            df, df_15m, market_regime, rs_score, rs_slope
        )

        if direction == "NEUTRAL": return None

        return {
            "timestamp": df.index[-1],
            "ticker": ticker,
            "close": df['Close'].iloc[-1],
            "atr": df['ATR'].iloc[-1],
            "kill_score": kill_score,
            "direction": direction,
            "reasons": reasons,
            "features": features,
            "swing_high": df['Swing_High'].iloc[-1],
            "swing_low": df['Swing_Low'].iloc[-1],
            "resistance": df['Resistance'].iloc[-1],
            "support": df['Support'].iloc[-1]
        }

    def analyze_ticker(self, ticker, market_regime="Neutral", nifty_df=None):
        df = self.fetch_data(ticker)
        if df is None: return None
        
        df = self.calculate_technicals(df)
        df_15m = self.resample_to_15m(df)
        if df_15m.empty: return None

        # RS Calcs
        rs_score = 0.0
        rs_slope = 0.0
        if nifty_df is not None:
            rs_score = self.calculate_relative_strength(df, nifty_df)
            rs_slope = self.calculate_rs_slope(df, nifty_df)

        kill_score, reasons, direction, features = self.calculate_kill_score(df, df_15m, market_regime, rs_score, rs_slope)
        
        if direction == "NEUTRAL": return None

        # Decision Filter
        decision = "WAIT"
        if kill_score >= 6: decision = "TRADE"
        
        # Global Veto
        if market_regime == "Bullish" and direction == "SHORT": decision = "WAIT"
        if market_regime == "Bearish" and direction == "LONG": decision = "WAIT"

        return {
            "ticker": ticker,
            "current_price": df['Close'].iloc[-1],
            "kill_score": kill_score,
            "decision": decision,
            "direction": direction,
            "reasons": reasons,
            "atr": df['ATR'].iloc[-1],
            "rs_score": rs_score,
            "features": features,
            "swing_high": df['Swing_High'].iloc[-1],
            "swing_low": df['Swing_Low'].iloc[-1],
            "resistance": df['Resistance'].iloc[-1],
            "support": df['Support'].iloc[-1]
        }
