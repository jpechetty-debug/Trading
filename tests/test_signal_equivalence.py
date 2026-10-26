"""Compare optimized signal calculations against the original pandas operations."""
import numpy as np
import pandas as pd
import pytest

from src.brain_a_v5 import BrainAV5


def bars():
    index = pd.DatetimeIndex([
        day + pd.Timedelta(hours=9, minutes=15 + 5*i)
        for day in pd.bdate_range('2024-01-01', periods=4) for i in range(75)
    ], name='Datetime')
    rng = np.random.default_rng(481)
    close = 100 * np.exp(np.cumsum(rng.normal(0, .002, len(index))))
    return pd.DataFrame({'Open': close - .1, 'High': close + .2,
                         'Low': close - .3, 'Close': close,
                         'Volume': rng.integers(100, 10000, len(index))}, index=index)


def reference_rs(stock, nifty, lookback=10):
    if stock is None or nifty is None:
        return 0.0
    merged = stock[['Close']].join(nifty[['Close']], how='inner',
                                   lsuffix='_stock', rsuffix='_nifty')
    if len(merged) <= lookback:
        return 0.0
    stock_return = np.log(merged.Close_stock / merged.Close_stock.shift(lookback)).iloc[-1]
    nifty_return = np.log(merged.Close_nifty / merged.Close_nifty.shift(lookback)).iloc[-1]
    return round((stock_return - nifty_return) * 100, 2)


def reference_slope(stock, nifty, lookback=10):
    if stock is None or nifty is None:
        return 0.0
    merged = stock[['Close']].join(nifty[['Close']], how='inner',
                                   lsuffix='_stock', rsuffix='_nifty')
    if len(merged) <= lookback + 5:
        return 0.0
    scores = []
    for i in range(5, 0, -1):
        prefix = merged.iloc[:-i]
        stock_return = np.log(prefix.Close_stock / prefix.Close_stock.shift(lookback)).iloc[-1]
        nifty_return = np.log(prefix.Close_nifty / prefix.Close_nifty.shift(lookback)).iloc[-1]
        scores.append(round((stock_return - nifty_return) * 100, 2))
    return np.polyfit(range(5), scores, 1)[0]


@pytest.mark.parametrize('variant', ['regular', 'gaps', 'aware', 'nan', 'duplicate', 'unsorted'])
def test_completed_fifteen_minute_windows_match_pandas(variant):
    frame = bars()
    if variant == 'gaps':
        frame = frame.drop(frame.index[[3, 40, 73, 140, 152, 204]])
    elif variant == 'aware':
        frame.index = frame.index.tz_localize('Asia/Kolkata')
    elif variant == 'nan':
        frame.iloc[130, frame.columns.get_loc('High')] = np.nan
    elif variant == 'duplicate':
        frame = pd.concat([frame, frame.iloc[[130]]]).sort_index()
    elif variant == 'unsorted':
        frame = frame.iloc[::-1]
    brain = BrainAV5()
    for end in [0, 1, 2, 3, 50, 73, 75, 76, 149, 150, 151, 152, 223, len(frame)]:
        window = frame.iloc[max(0, end - 151):end]
        pd.testing.assert_frame_equal(
            brain.resample_to_15m(window), brain._resample_to_15m_reference(window),
            check_freq=False, check_dtype=False, rtol=1e-13, atol=1e-13,
        )


@pytest.mark.parametrize('variant', ['regular', 'gaps', 'duplicate', 'unsorted', 'none'])
def test_relative_strength_and_slope_match_pandas(variant):
    stock = bars()
    nifty = stock.copy()
    nifty['Close'] = np.linspace(20000, 20300, len(nifty))
    if variant == 'gaps':
        stock = stock.drop(stock.index[::11])
        nifty = nifty.drop(nifty.index[::7])
    elif variant == 'duplicate':
        stock = pd.concat([stock, stock.iloc[[130]]]).sort_index()
    elif variant == 'unsorted':
        stock = stock.iloc[::-1]
    elif variant == 'none':
        nifty = None
    brain = BrainAV5()
    for lookback in [1, 10, 20]:
        for count in [0, 1, 10, 11, 15, 16, 40, len(stock)]:
            window = stock.iloc[:count]
            assert brain.calculate_relative_strength(window, nifty, lookback) == reference_rs(window, nifty, lookback)
            assert brain.calculate_rs_slope(window, nifty, lookback) == pytest.approx(reference_slope(window, nifty, lookback), abs=1e-14)


def test_full_engine_preserves_trades_and_rejections(monkeypatch):
    from scripts.benchmark_backtest import run
    optimized = run(days=5, ticker_count=8)
    monkeypatch.setattr(BrainAV5, 'resample_to_15m', BrainAV5._resample_to_15m_reference)
    monkeypatch.setattr(BrainAV5, 'calculate_relative_strength',
                        lambda self, stock, nifty, lookback=None: reference_rs(stock, nifty, self.rs_lookback if lookback is None else lookback))
    monkeypatch.setattr(BrainAV5, 'calculate_rs_slope',
                        lambda self, stock, nifty, lookback=None: reference_slope(stock, nifty, self.rs_lookback if lookback is None else lookback))
    reference = run(days=5, ticker_count=8)
    assert optimized['trades'], 'Equivalence must exercise actual fills and exits'
    assert optimized['trades'] == reference['trades']
    assert optimized['events'] == reference['events']
