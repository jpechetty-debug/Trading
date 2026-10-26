import pandas as pd
import pytest

from src import config
from src.backtest.portfolio_engine import PortfolioEngine
from src.execution.model import ExecutionModel
from tests.test_portfolio_safety import make_engine
from tests.market_fixtures import market_bars


def change_load(monkeypatch, engine, change):
    original = engine.load_universe
    def load():
        original()
        change(engine.data_map)
    monkeypatch.setattr(engine, 'load_universe', load)


def test_signal_fills_next_open_and_reprices_actual_risk(monkeypatch):
    engine, times = make_engine(monkeypatch)
    def change(data):
        frame = data['A.NS']
        frame.loc[:, 'Open'] = [100, 101, 101]
    change_load(monkeypatch, engine, change)
    monkeypatch.setattr(engine.exec_model, 'generate_orders', ExecutionModel(base_risk_inr=1000,
                       stop_mult=1, target_mult=5).generate_orders)
    result = engine.run()
    assert len(result) == 1
    trade = engine.portfolio.closed_trades[0]
    assert trade.signal_time == times[0]
    assert trade.entry_time == times[1]
    assert trade.entry == 101  # signal close was 100
    assert trade.initial_stop == 98  # prior-bar structural stop is not shifted
    assert trade.r_unit == 3


def test_gap_is_checked_at_fill_not_at_signal(monkeypatch):
    engine, times = make_engine(monkeypatch)
    def change(data):
        frame = data['A.NS']
        frame.loc[:, 'Open'] = [100, 110, 101]
    change_load(monkeypatch, engine, change)
    assert engine.run().empty
    assert any(e['reason'] == 'GAP_VETO' for e in engine.events)


def test_missing_next_bar_expires_signal(monkeypatch):
    engine, times = make_engine(monkeypatch)
    change_load(monkeypatch, engine, lambda data: data.__setitem__('A.NS', data['A.NS'].drop(times[1])))
    assert engine.run().empty
    assert any(e['reason'] == 'MISSING_EXECUTION_BAR' for e in engine.events)


def test_last_bar_signal_does_not_invent_a_fill(monkeypatch):
    timestamp = pd.Timestamp('2024-01-01 09:15')
    engine, _ = make_engine(monkeypatch, closes=(100,), times=[timestamp])
    assert engine.run().empty
    assert engine.events[-1]['reason'] == 'NO_NEXT_BAR'


def test_signals_do_not_survive_to_next_session(monkeypatch):
    engine, _ = make_engine(monkeypatch, times=pd.to_datetime([
        '2024-01-01 15:05', '2024-01-02 09:15', '2024-01-02 09:20']))
    assert engine.run().empty
    assert any(e['reason'] == 'SIGNAL_EXPIRED' for e in engine.events)


def test_intrabar_profit_cannot_fund_a_fill_at_that_bars_open(monkeypatch):
    times = pd.date_range('2024-01-01 09:15', periods=3, freq='5min')
    engine, _ = make_engine(monkeypatch, closes=(100, 100, 110), stop=90,
                           schedule={(times[0], 'A.NS'): 'LONG', (times[1], 'B.NS'): 'LONG'})
    def change(data):
        data['A.NS'].loc[times[2], 'High'] = 201
    change_load(monkeypatch, engine, change)
    results = engine.run()
    assert results['ticker'].tolist() == ['A.NS']
    assert results.iloc[0]['Reason'] == 'TARGET'
    assert any(e['ticker'] == 'B.NS' and e['reason'] == 'INSUFFICIENT_CAPITAL_AFTER_COSTS'
               for e in engine.events)


def test_daily_halt_survives_intraday_equity_recovery(monkeypatch):
    monkeypatch.setattr(config, 'RISK_PER_TRADE', 100)
    times = pd.date_range('2024-01-01 09:15', periods=4, freq='5min')
    engine, _ = make_engine(monkeypatch, closes=(100, 97.5, 101, 101), shares=100, times=times,
                           schedule={(times[0], 'A.NS'): 'LONG', (times[2], 'B.NS'): 'LONG'})
    result = engine.run()
    assert result['ticker'].tolist() == ['A.NS']
    assert engine.equity > engine.initial_equity
    assert engine._daily_halted


def test_full_notional_order_leaves_cash_for_fees(monkeypatch):
    engine, _ = make_engine(monkeypatch, closes=(100, 100, 100))
    result = engine.run()
    assert len(result) == 1
    pos = engine.portfolio.closed_trades[0]
    friction = engine.cost_model.round_trip_cost(pos.entry, pos.entry, pos.shares)
    assert pos.shares < 1000
    assert pos.entry * pos.shares + friction <= engine.initial_equity


def test_end_boundary_excludes_future_prices_and_closes_open_positions(monkeypatch):
    stock = market_bars()
    nifty = market_bars(20_000, 0.05, volume=0)
    data = {'A.NS': stock, '^NSEI': nifty}
    class Store:
        def load_ticker(self, ticker):
            return data[ticker].copy()
    start, end = stock.index[250], stock.index[260]
    engine = PortfolioEngine(['A.NS'], start_date=start, end_date=end, data_store=Store())
    def signal(ticker, frame, *_args, **_kwargs):
        price = frame['Close'].iloc[-1]
        return {'ticker': ticker, 'close': price, 'atr': float(frame['ATR'].iloc[-1]), 'kill_score': 8., 'direction': 'LONG',
                'resistance': price + 10, 'features': {}}
    monkeypatch.setattr(engine.brain, 'analyze_slice', signal)
    engine.exec_model = ExecutionModel(stop_mult=1, target_mult=5)
    first = engine.run()
    assert len(first) > 0
    assert first['entry_time'].min() > start
    assert first['exit_time'].max() < end
    assert first.iloc[-1]['Reason'] == 'END_OF_DATA'
    assert all(df.index.max() < end for df in engine.data_map.values())
    equity = engine.equity
    data['A.NS'].loc[end:, ['Open', 'High', 'Low', 'Close']] *= 100
    data['^NSEI'].loc[end:, ['Open', 'High', 'Low', 'Close']] *= 0.01
    second = engine.run()
    pd.testing.assert_frame_equal(first, second)
    assert engine.equity == equity


@pytest.mark.parametrize('start,end', [('2024-01-02', '2024-01-01'), ('2024-01-01', '2024-01-01'),
                                     ('NaT', None), ('2024-01-01', 'NaT')])
def test_invalid_simulation_window_fails_early(start, end):
    with pytest.raises(ValueError):
        PortfolioEngine([], start_date=start, end_date=end)
