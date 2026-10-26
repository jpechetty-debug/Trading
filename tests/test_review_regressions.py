import numpy as np
import pandas as pd
import pytest

from src import config
from src.brain_a_v5 import BrainAV5
from src.execution.model import ExecutionModel
from src.backtest.portfolio_engine import PortfolioEngine
from src.data.store import DataStore
from src import live_bot
from tests.market_fixtures import market_bars


def breakout_bars():
    frame = market_bars()
    close = 100 * np.exp(np.arange(len(frame)) * .0002)
    frame['Close'] = close
    frame['Open'] = close - .015
    frame['High'] = close + .005
    frame['Low'] = close - .08
    return frame


def test_real_scoring_sizing_and_portfolio_produce_trades_from_local_data(tmp_path):
    stock = breakout_bars()
    nifty = market_bars(20_000, .05, volume=0)
    stock.to_csv(tmp_path / 'A.NS.csv', index_label='Datetime')
    nifty.to_csv(tmp_path / '^NSEI.csv', index_label='Datetime')
    engine = PortfolioEngine(['A.NS'], start_date=stock.index[150],
                             data_store=DataStore(storage_dir=tmp_path, allow_network=False))
    results = engine.run()  # No scoring, sizing, regime, or execution mocks.
    assert not results.empty, engine.events
    assert (results['entry_time'] > results['signal_time']).all()
    assert (results['shares'] > 0).all()
    assert engine.equity == pytest.approx(engine.initial_equity + results['PnL'].sum())
    assert all(p.features['rs_score'] >= config.RS_SWEET_SPOT_LOW
               for p in engine.portfolio.closed_trades)


def test_unused_ema_no_longer_erases_151_raw_bars():
    brain = BrainAV5()
    raw = breakout_bars().iloc[:151]
    prepared = brain.calculate_technicals(raw)
    assert len(prepared) >= 50
    assert 'EMA_200' not in prepared
    result = brain.analyze_slice('A.NS', raw, market_bars(20_000, .05, volume=0), 'Bullish')
    assert result is not None
    assert result['kill_score'] >= config.KILL_SCORE_THRESHOLD
    assert ExecutionModel().generate_orders(result)['valid_rr']


def test_structural_levels_exclude_signal_bar_extremes():
    brain = BrainAV5()
    raw = breakout_bars()
    before = brain.calculate_technicals(raw).iloc[-1]
    changed = raw.copy()
    changed.loc[changed.index[-1], 'High'] *= 2
    changed.loc[changed.index[-1], 'Low'] /= 2
    after = brain.calculate_technicals(changed).iloc[-1]
    assert before['Resistance'] == after['Resistance'] == raw.High.iloc[-21:-1].max()
    assert before['Support'] == after['Support'] == raw.Low.iloc[-21:-1].min()


@pytest.mark.parametrize('direction,levels', [
    ('LONG', {'Close': 100., 'Resistance': 99., 'Support': 95.}),
    ('SHORT', {'Close': 100., 'Resistance': 105., 'Support': 101.}),
])
def test_broken_barrier_uses_atr_target_without_wrong_side_reward(direction, levels):
    barriers = BrainAV5._target_barriers(levels)
    signal = {'close': 100., 'atr': .3, 'direction': direction, **barriers}
    order = ExecutionModel().generate_orders(signal, equity=100_000)
    assert order['valid_rr']
    expected = 100 + (1 if direction == 'LONG' else -1) * config.TARGET_MULTIPLIER * .3
    assert order['target'] == pytest.approx(expected)


def test_opening_gap_does_not_erase_unbroken_signal_barrier():
    barriers = BrainAV5._target_barriers({'Close': 100., 'Resistance': 101., 'Support': 95.})
    assert barriers['resistance'] == 101
    order = ExecutionModel().generate_orders({'close': 100., 'atr': .3, 'direction': 'LONG',
                                              **barriers}, entry_price=101.1)
    assert not order['valid_rr']


def test_live_bot_threshold_and_banner_follow_configuration(monkeypatch):
    monkeypatch.setattr(config, 'ALERT_KILL_SCORE_THRESHOLD', 8.0)
    monkeypatch.setattr(live_bot, 'ALL_TICKERS', ['A.NS', 'B.NS'])
    assert '2 tickers' in live_bot.startup_summary()
    assert 'Alert score >= 8' in live_bot.startup_summary()
    for score, valid, shares, expected in [(7.9, True, 10, False), (8, True, 10, True),
                                          (9, False, 10, False), (9, True, 0, False),
                                          (float('nan'), True, 10, False)]:
        assert live_bot.should_alert({'kill_score': score, 'valid_rr': valid, 'shares': shares}) == expected
