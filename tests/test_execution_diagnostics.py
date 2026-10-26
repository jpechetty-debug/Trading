import pandas as pd
import pytest

from src.backtest.diagnostics import summarize_execution
from tests.test_portfolio_safety import make_engine


def test_position_budget_leaves_capital_for_a_second_trade(monkeypatch):
    times = pd.date_range('2024-01-01 09:15', periods=3, freq='5min')
    engine, _ = make_engine(monkeypatch, closes=(100, 100, 100), stop=90, schedule={
        (times[0], 'A.NS'): 'LONG', (times[0], 'B.NS'): 'LONG'})
    engine.max_position_notional_pct = .2
    results = engine.run()
    assert set(results.ticker) == {'A.NS', 'B.NS'}
    assert (results.entry * results.shares <= 20_000).all()
    summary = summarize_execution(results, engine.events, engine.exposure_history)
    assert summary['max_concurrent_positions'] == 2
    assert summary['events']['CAPITAL_RESIZED'] == 2
    assert summary['by_direction']['SHORT']['trades'] == 0
    assert summary['overall']['gross_pnl'] - summary['overall']['costs'] == pytest.approx(results.PnL.sum())
    assert summary['cost_scenarios'][2]['net_pnl_same_fills'] < summary['overall']['net_pnl']
    assert summary['by_exit_reason']['END_OF_DATA']['trades'] == 2


def test_empty_result_diagnostics_remain_explicit(monkeypatch):
    engine, _ = make_engine(monkeypatch, closes=(100,), times=[pd.Timestamp('2024-01-01 09:15')])
    summary = summarize_execution(engine.run(), engine.events, engine.exposure_history)
    assert summary['overall']['trades'] == 0
    assert summary['overall']['mean_net_r'] is None
    assert summary['max_concurrent_positions'] == 0
