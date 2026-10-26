"""Validation shared by offline imports and downloaded five-minute bars."""
import numpy as np
import pandas as pd

from src import config
from src.utils.market_time import market_index

OHLCV_COLUMNS = ('Open', 'High', 'Low', 'Close', 'Volume')


def normalize_ohlcv(frame):
    """Return sorted numeric OHLCV in naive IST; reject incompatible data.

    Missing candles are allowed. Daily data, duplicate times, invalid prices,
    and fabricated/default volume are never silently accepted as intraday bars.
    """
    if frame is None or frame.empty:
        raise ValueError('OHLCV data is empty')
    frame = frame.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    frame = frame.rename(columns={c: str(c).strip().title() for c in frame.columns})
    if frame.columns.has_duplicates:
        raise ValueError('Duplicate OHLCV columns')
    if not isinstance(frame.index, pd.DatetimeIndex):
        date_column = next((c for c in ('Datetime', 'Timestamp', 'Date') if c in frame), None)
        if date_column is None:
            raise ValueError('A datetime index or Date/Datetime/Timestamp column is required')
        frame.index = market_index(frame.pop(date_column))
    else:
        frame.index = market_index(frame.index)
    if frame.index.isna().any() or frame.index.has_duplicates:
        raise ValueError('Missing or duplicate timestamps')
    frame = frame.sort_index()
    if len(frame) < 2:
        raise ValueError('At least two bars are required to verify an intraday interval')
    interval_ns = config.CANDLE_INTERVAL_SECONDS * 1_000_000_000
    if any(pd.Timestamp(t).value % interval_ns for t in frame.index):
        raise ValueError('Timestamps must align to five-minute bar-start boundaries')
    missing = set(OHLCV_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError('Missing OHLCV columns: ' + ', '.join(sorted(missing)))
    frame = frame.loc[:, list(OHLCV_COLUMNS)].apply(pd.to_numeric, errors='raise')
    if not np.isfinite(frame.to_numpy(dtype=float)).all():
        raise ValueError('Non-finite OHLCV values')
    prices = frame[['Open', 'High', 'Low', 'Close']]
    if (prices <= 0).any().any() or (frame['Volume'] < 0).any():
        raise ValueError('Prices must be positive and volume nonnegative')
    if ((frame['High'] < prices.max(axis=1)) | (frame['Low'] > prices.min(axis=1))).any():
        raise ValueError('OHLC price bounds are inconsistent')
    if len(frame) > 1:
        times = frame.index.to_series()
        same_day = times.dt.normalize().eq(times.shift().dt.normalize())
        seconds = times.diff().dt.total_seconds().loc[same_day]
        interval = config.CANDLE_INTERVAL_SECONDS
        if seconds.empty or not seconds.eq(interval).any() or seconds.mod(interval).ne(0).any():
            raise ValueError(f'Expected {interval}-second intraday bars; daily/other intervals are incompatible')
    return frame
