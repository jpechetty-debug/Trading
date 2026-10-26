"""Shared closed-candle and freshness requirements for live signal sources."""
import pandas as pd

from src import config
from src.data.validation import normalize_ohlcv
from src.utils.market_time import market_index, market_timestamp


def completed_bars(frame, now, min_bars=50):
    if frame is None or frame.empty:
        raise ValueError('Market data is unavailable')
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError('Live bars require a datetime index')
    now = market_timestamp(now)
    interval = pd.Timedelta(seconds=config.CANDLE_INTERVAL_SECONDS)
    frame = frame.copy()
    frame.index = market_index(frame.index)
    # Providers label the START of a candle. A row for the current interval is
    # still forming, even if all its numeric fields are already populated.
    frame = frame.loc[frame.index + interval <= now].dropna(how='all')
    frame = normalize_ohlcv(frame)
    expected = now.floor(f'{config.CANDLE_INTERVAL_SECONDS}s') - interval
    if frame.index[-1] != expected:
        raise ValueError(f'Stale market data: latest {frame.index[-1]}, expected {expected}')
    if len(frame) < min_bars:
        raise ValueError(f'Insufficient completed history: {len(frame)} < {min_bars}')
    return frame
