import datetime

import pandas as pd
import pytest

from src.brain_a_v5 import BrainAV5
from src.utils import market_time
from tests.market_fixtures import market_bars


def test_fifteen_minute_confirmation_ignores_forming_or_gapped_groups():
    frame = market_bars().iloc[:74]  # Last bucket has only 15:15 and 15:20.
    frame = frame.drop(frame.index[1])  # First bucket is missing 09:20.
    result = BrainAV5().resample_to_15m(frame)
    assert result.index.min() == pd.Timestamp('2024-01-01 09:30')
    assert result.index.max() == pd.Timestamp('2024-01-01 15:00')
    assert len(result) == 23
    changed = frame.copy()
    changed.loc[changed.index[-2:], ['Open', 'High', 'Low', 'Close']] *= 10
    pd.testing.assert_frame_equal(result, BrainAV5().resample_to_15m(changed))


def test_wait_for_next_candle_crosses_midnight(monkeypatch):
    class FrozenDateTime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return market_time.IST.localize(cls(2024, 1, 1, 23, 59, 59))
    delays = []
    monkeypatch.setattr(market_time.datetime, 'datetime', FrozenDateTime)
    monkeypatch.setattr(market_time.time, 'sleep', delays.append)
    market_time.MarketClock().wait_for_next_candle()
    assert delays == [6.0]


@pytest.mark.parametrize('value', [0, -5, 7, 2.5])
def test_invalid_clock_interval_fails(value):
    with pytest.raises(ValueError):
        market_time.MarketClock().wait_for_next_candle(value)
