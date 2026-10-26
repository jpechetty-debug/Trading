"""Controlled signals exercise accounting and gates through the real run loop."""
import numpy as np
import pandas as pd
import pytest

from src import config
from src.backtest.portfolio_engine import PortfolioEngine


def make_engine(monkeypatch, closes=(100, 101, 103), times=None,
                initial_equity=100_000, schedule=None, shares=1000, stop=50):
    # Isolate accounting/loss tests from the independent exposure-cap tests.
    monkeypatch.setattr(config, 'MAX_TRADE_RISK_PCT', 0.9)
    monkeypatch.setattr(config, 'MAX_PORTFOLIO_RISK_PCT', 0.95)
    monkeypatch.setattr('src.backtest.constraints.MAX_CLUSTER_EXPOSURE_PCT', 1.0)
    times = pd.DatetimeIndex(times if times is not None else
                             pd.date_range('2024-01-01 09:15', periods=len(closes), freq='5min'))
    opens = np.r_[closes[0], closes[:-1]]
    frame = pd.DataFrame({'Open': opens, 'Close': closes,
                          'High': np.maximum(opens, closes) + 0.8, 'Low': np.minimum(opens, closes) - 0.1,
                          'ATR': 2.0, 'EMA_20': 1.0, 'EMA_50': 1.0, 'RS_Score': 1.0}, index=times)
    engine = PortfolioEngine(['A.NS', 'B.NS'], start_date=times[0], initial_equity=initial_equity)
    engine.regime_detector.primary_period = 1
    engine.regime_detector.gatekeeper_period = 1
    engine.constraints.max_sector_exposure = 1.0
    schedule = schedule if schedule is not None else {(times[0], 'A.NS'): 'LONG'}

    def load():
        engine.data_map = {'^NSEI': frame.copy(), 'A.NS': frame.copy(), 'B.NS': frame.copy()}
        engine.data_map['B.NS'].loc[:, ['Open', 'Close']] = 100.0
        engine.data_map['B.NS'].loc[:, 'High'] = 100.8
        engine.data_map['B.NS'].loc[:, 'Low'] = 99.9
        engine.master_timeline = list(times)
        engine.constraints.update_price_data(engine.data_map)

    def signal(ticker, stock_slice, *_args, **_kwargs):
        timestamp = stock_slice.index[-1]
        direction = schedule.get((timestamp, ticker))
        if not direction:
            return None
        return {'ticker': ticker, 'close': stock_slice['Close'].iloc[-1], 'atr': 2,
                'kill_score': 8, 'direction': direction, 'features': {}}

    def orders(sig, **kwargs):
        return {'entry': kwargs.get('entry_price', sig['close']), 'stop': stop, 'target': 200,
                'shares': shares, 'valid_rr': True}

    monkeypatch.setattr(engine, 'load_universe', load)
    monkeypatch.setattr(engine.brain, 'analyze_slice', signal)
    monkeypatch.setattr(engine.exec_model, 'generate_orders', orders)
    monkeypatch.setattr(engine.regime_detector, 'detect', lambda *a, **kw:
                        {'regime': 'Bullish', 'longs_authorized': True, 'shorts_authorized': True})
    return engine, times


def test_unrealized_daily_loss_blocks_new_trade_on_same_bar(monkeypatch):
    monkeypatch.setattr(config, 'RISK_PER_TRADE', 100)
    engine, times = make_engine(monkeypatch, closes=(100, 99.5, 99.5), schedule={
        (pd.Timestamp('2024-01-01 09:15'), 'A.NS'): 'LONG',
        (pd.Timestamp('2024-01-01 09:20'), 'B.NS'): 'LONG',
    })
    result = engine.run()
    assert result['ticker'].tolist() == ['A.NS']
    assert engine.equity_history[1] < engine.initial_equity - 200


def test_realized_exit_loss_blocks_new_trade_without_one_bar_delay(monkeypatch):
    monkeypatch.setattr(config, 'RISK_PER_TRADE', 100)
    engine, times = make_engine(monkeypatch, closes=(100, 80, 80), stop=90, schedule={
        (pd.Timestamp('2024-01-01 09:15'), 'A.NS'): 'LONG',
        (pd.Timestamp('2024-01-01 09:20'), 'B.NS'): 'LONG',
    })
    engine.constraints.max_drawdown = 0.9
    result = engine.run()
    assert result['ticker'].tolist() == ['A.NS']
    assert result.iloc[0]['Reason'] == 'STOP'


def test_unrealized_drawdown_liquidates_and_reconciles(monkeypatch):
    engine, times = make_engine(monkeypatch, closes=(100, 60, 65), initial_equity=250_000)
    result = engine.run()
    assert result['Reason'].tolist() == ['CIRCUIT_BREAKER']
    assert engine.portfolio.open_positions == []
    assert engine.portfolio.closed_trades[0].exit_time == times[1]
    assert engine.equity == pytest.approx(engine.initial_equity + result['PnL'].sum())


def test_terminal_positions_are_included_in_results(monkeypatch):
    engine, times = make_engine(monkeypatch)
    result = engine.run()
    assert result['Reason'].tolist() == ['END_OF_DATA']
    assert engine.portfolio.closed_trades[0].exit_price == 103
    assert engine.portfolio.closed_trades[0].exit_time == times[-1]
    assert not engine.portfolio.open_positions
    assert engine.equity == pytest.approx(engine.initial_equity + result['PnL'].sum())
    assert engine.equity_history[-1] == engine.equity
    assert engine.portfolio.closed_trades[0].r_unit == 50  # entry minus initial stop, not ATR=2


def test_repeat_runs_are_independent_and_keep_configured_limits(monkeypatch):
    engine, _ = make_engine(monkeypatch)
    engine.constraints.max_positions = 4
    first = engine.run()
    assert not first.empty
    equity = engine.equity
    history = engine.equity_history.copy()
    second = engine.run()
    pd.testing.assert_frame_equal(first, second)
    assert engine.equity == equity
    assert engine.equity_history == history
    assert engine.constraints.max_positions == 4


def test_small_starting_equity_does_not_trigger_phantom_drawdown(monkeypatch):
    engine, times = make_engine(monkeypatch, initial_equity=25_000, schedule={})
    result = engine.run()
    assert result.empty
    assert engine.equity == 25_000
    assert len(engine.equity_history) == len(times)


def test_longs_are_blocked_even_when_short_scan_is_authorized(monkeypatch):
    engine, _ = make_engine(monkeypatch)
    monkeypatch.setattr(engine.regime_detector, 'detect', lambda *a, **kw:
                        {'regime': 'Bullish', 'longs_authorized': False, 'shorts_authorized': True})
    assert engine.run().empty


def test_eod_closes_at_cutoff_open_and_does_not_reopen(monkeypatch):
    times = pd.date_range('2024-01-01 15:05', periods=3, freq='5min')
    schedule = {(times[0], 'A.NS'): 'LONG', (times[2], 'B.NS'): 'LONG'}
    engine, _ = make_engine(monkeypatch, times=times, schedule=schedule)
    result = engine.run()
    assert result['ticker'].tolist() == ['A.NS']
    assert result.iloc[0]['Reason'] == 'EOD_FLATTEN'
    assert engine.portfolio.closed_trades[0].exit_price == 101


@pytest.mark.parametrize('start', ['2024-01-01 08:00', '2024-01-01 15:15', '2024-01-06 10:00'])
def test_no_entries_outside_session(monkeypatch, start):
    engine, _ = make_engine(monkeypatch, times=pd.date_range(start, periods=3, freq='5min'))
    assert engine.run().empty


def test_volatility_uses_daily_closes_not_five_minute_changes():
    engine = PortfolioEngine([])
    days = pd.bdate_range('2024-01-01', periods=21)
    closes = 100_000 * np.cumprod(1 + np.linspace(-0.01, 0.02, 21))
    for day, close in zip(days, closes):
        engine.equity_timestamps.extend([day + pd.Timedelta(hours=10), day + pd.Timedelta(hours=15)])
        engine.equity_history.extend([close * 1.5, close])
    expected = pd.Series(closes).pct_change().dropna().std()
    assert engine._calculate_portfolio_vol() == pytest.approx(expected)


def test_many_bars_in_one_session_do_not_fake_twenty_days_of_history():
    engine = PortfolioEngine([])
    engine.equity_timestamps = list(pd.date_range('2024-01-01 09:15', periods=60, freq='5min'))
    engine.equity_history = list(np.linspace(100_000, 110_000, 60))
    assert engine._calculate_portfolio_vol() == pytest.approx(config.TARGET_PORTFOLIO_VOL / np.sqrt(252))
