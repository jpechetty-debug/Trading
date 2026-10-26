"""Offline five-minute fixtures with real session boundaries."""
import numpy as np
import pandas as pd


def market_bars(price=100.0, drift=0.015, volume=1_000_000.0):
    days = pd.bdate_range('2024-01-01', periods=5)
    index = pd.DatetimeIndex([day + pd.Timedelta(hours=9, minutes=15) + pd.Timedelta(minutes=5*i)
                              for n, day in enumerate(days) for i in range(75 if n < 4 else 10)])
    close = price + np.arange(len(index)) * drift
    return pd.DataFrame({'Open': close - 0.01, 'High': close + 0.2, 'Low': close - 0.2,
                         'Close': close, 'Volume': volume}, index=index)
