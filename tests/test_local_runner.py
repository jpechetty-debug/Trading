import hashlib
import json
import sys

import pandas as pd
import pytest

import run_local_backtest
from src.brain_a_v5 import BrainAV5
from tests.market_fixtures import market_bars


def test_offline_cli_writes_nonempty_reconciled_timestamped_report(monkeypatch, tmp_path, capsys):
    source = tmp_path / 'inputs'
    source.mkdir()
    stock = market_bars()
    stock.to_csv(source / 'A.NS.csv', index_label='Datetime')
    market_bars(20_000, .05, volume=0).to_csv(source / '^NSEI.csv', index_label='Datetime')
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()}
    # Deterministic signal, real file loader, indicators, sizing, constraints,
    # simulation, transaction costs, and JSON serialization.
    def signal(self, ticker, frame, *_args, **_kwargs):
        price = float(frame['Close'].iloc[-1])
        return {'ticker': ticker, 'close': price, 'atr': float(frame['ATR'].iloc[-1]),
                'kill_score': 8., 'direction': 'LONG', 'resistance': price + 10, 'features': {}}
    monkeypatch.setattr(BrainAV5, 'analyze_slice', signal)
    output = tmp_path / 'report.json'
    end = stock.index[260]
    monkeypatch.setattr(sys, 'argv', ['run_local_backtest.py', '--data-dir', str(source),
                                    '--tickers', 'A.NS', '--start-date', str(stock.index[250]),
                                    '--end-date', str(end), '--output', str(output)])
    run_local_backtest.main()
    report = json.loads(output.read_text())
    assert report['closed_trades'] > 0
    assert report['execution_model'] == 'next_bar_open'
    assert report['final_equity'] == pytest.approx(
        report['initial_equity'] + sum(t['PnL'] for t in report['trades']))
    for trade in report['trades']:
        assert pd.Timestamp(trade['signal_time']) < pd.Timestamp(trade['entry_time'])
        assert pd.Timestamp(trade['entry_time']) <= pd.Timestamp(trade['exit_time']) < end
    assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()}
    assert any(e['reason'] == 'FILLED_NEXT_OPEN' for e in report['events'])
