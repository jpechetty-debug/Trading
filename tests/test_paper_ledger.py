from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest
import pandas as pd

from src import config
from src.paper.ledger import PaperLedger
from src.brain_a_v5 import BrainAV5
from tests.test_review_regressions import breakout_bars
from tests.market_fixtures import market_bars


def proposal(ticker='A.NS'):
    return dict(signal={'ticker': ticker, 'close': 100., 'atr': 2., 'direction': 'LONG',
                        'kill_score': 8., 'features': {}},
                signal_time='2024-01-01 09:15', timestamp='2024-01-01 09:20', price=100., authorized=True)


def quote(price=100., timestamp='2024-01-01 09:25', ticker='A.NS'):
    return {ticker: {'price': price, 'timestamp': timestamp}}


def test_paper_restart_and_replay_do_not_duplicate_cash_or_positions(tmp_path):
    path = tmp_path / 'paper.db'
    ledger = PaperLedger(path)
    filled = ledger.enter('entry-1', **proposal())
    assert filled['status'] == 'FILLED'
    assert filled['entry'] * filled['shares'] <= 20_000
    reopened = PaperLedger(path)
    assert reopened.enter('entry-1', **proposal()) == filled
    assert reopened.snapshot()['event_count'] == 1
    exit_args = dict(position_id='entry-1', price=108., timestamp='2024-01-01 09:25', reason='TARGET')
    closed = reopened.exit('exit-1', **exit_args)
    assert PaperLedger(path).exit('exit-1', **exit_args) == closed
    state = ledger.snapshot()
    assert state['event_count'] == 2
    assert not state['positions']
    assert state['equity'] == pytest.approx(100_000 + closed['net_pnl'])
    assert closed['gross_pnl'] - closed['costs'] == pytest.approx(closed['net_pnl'])


def test_conflicting_duplicate_is_rejected(tmp_path):
    ledger = PaperLedger(tmp_path / 'paper.db')
    ledger.enter('entry', **proposal())
    with pytest.raises(ValueError, match='different content'):
        ledger.enter('entry', **dict(proposal(), price=100.1))
    assert ledger.snapshot()['event_count'] == 1


def test_concurrent_duplicate_events_commit_once(tmp_path):
    path = tmp_path / 'paper.db'
    a, b = PaperLedger(path), PaperLedger(path)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda ledger: ledger.enter('entry', **proposal()), [a, b]))
    assert results[0] == results[1]
    assert a.snapshot()['event_count'] == 1


def test_event_and_state_roll_back_together_when_checkpoint_fails(tmp_path):
    path = tmp_path / 'paper.db'
    ledger = PaperLedger(path)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TRIGGER block_update BEFORE UPDATE ON paper_account BEGIN SELECT RAISE(ABORT,'simulated checkpoint failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='simulated checkpoint failure'):
        ledger.enter('entry', **proposal())
    assert ledger.snapshot()['event_count'] == 0
    assert not ledger.snapshot()['positions']


@pytest.mark.parametrize('changes,reason', [
    ({'authorized': False}, 'DIRECTION_NOT_AUTHORIZED'),
    ({'timestamp': '2024-01-01 09:25'}, 'SIGNAL_EXPIRED'),
    ({'timestamp': '2024-01-02 09:20'}, 'SIGNAL_EXPIRED'),
    ({'price': 110.}, 'GAP_VETO'),
    ({'timestamp': '2024-01-01 15:20'}, 'OUTSIDE_ENTRY_SESSION'),
])
def test_entry_rejections_are_persistent(tmp_path, changes, reason):
    ledger = PaperLedger(tmp_path / 'paper.db')
    result = ledger.enter('entry', **dict(proposal(), **changes))
    assert result['reason'] == reason
    assert ledger.snapshot()['event_count'] == 1
    assert not ledger.snapshot()['positions']


def test_all_open_positions_need_current_marks_and_events_are_chronological(tmp_path):
    ledger = PaperLedger(tmp_path / 'paper.db')
    ledger.enter('entry', **proposal())
    before = ledger.snapshot()
    with pytest.raises(ValueError, match='quote at the event timestamp'):
        ledger.mark('mark', timestamp='2024-01-01 09:25', quotes=quote(timestamp='2024-01-01 09:20'))
    assert ledger.snapshot() == before
    with pytest.raises(ValueError, match='chronological'):
        ledger.mark('mark', timestamp='2024-01-01 09:15', quotes=quote(timestamp='2024-01-01 09:15'))
    assert ledger.snapshot() == before


def test_daily_loss_halt_survives_restart_and_recovery(tmp_path):
    path = tmp_path / 'paper.db'
    ledger = PaperLedger(path)
    ledger.enter('entry', **proposal())
    result = ledger.mark('loss', timestamp='2024-01-01 09:25', quotes=quote(price=80.))
    assert result['daily_halted']
    reopened = PaperLedger(path)
    recovered = reopened.mark('recovery', timestamp='2024-01-01 09:30',
                              quotes=quote(105., '2024-01-01 09:30'))
    assert recovered['daily_halted']
    next_args = dict(proposal('B.NS'), signal_time='2024-01-01 09:30', timestamp='2024-01-01 09:35',
                     quotes=quote(105., '2024-01-01 09:35'))
    assert reopened.enter('entry-2', **next_args)['reason'] == 'RISK_HALT'
    reopened.exit('close', position_id='entry', timestamp='2024-01-01 09:40', price=105., reason='MANUAL')
    assert not reopened.mark('next-day', timestamp='2024-01-02 09:15', quotes={})['daily_halted']


def test_changed_configuration_cannot_silently_reset_account(tmp_path, monkeypatch):
    path = tmp_path / 'paper.db'
    PaperLedger(path)
    monkeypatch.setattr(config, 'MAX_TRADE_RISK_PCT', .02)
    with pytest.raises(ValueError, match='configuration changed'):
        PaperLedger(path)


def test_duplicate_position_is_not_opened(tmp_path):
    ledger = PaperLedger(tmp_path / 'paper.db')
    ledger.enter('entry', **proposal())
    args = dict(proposal(), quotes=quote(timestamp='2024-01-01 09:20'))
    assert ledger.enter('entry-2', **args)['reason'] == 'DUPLICATE_POSITION'
    assert len(ledger.snapshot()['positions']) == 1


def test_system_halt_survives_recovery_and_next_session(tmp_path):
    path = tmp_path / 'paper.db'
    ledger = PaperLedger(path)
    ledger.enter('entry', **proposal())
    assert ledger.mark('loss', timestamp='2024-01-01 09:25', quotes=quote(1.))['system_halted']
    ledger.exit('exit', position_id='entry', timestamp='2024-01-01 09:30', price=100., reason='MANUAL')
    reopened = PaperLedger(path)
    result = reopened.enter('next-entry', **dict(proposal(), signal_time='2024-01-02 09:15',
                                                timestamp='2024-01-02 09:20'))
    assert result['reason'] == 'RISK_HALT'


def test_short_paper_exit_uses_directional_pnl(tmp_path):
    ledger = PaperLedger(tmp_path / 'paper.db')
    args = proposal()
    args['signal']['direction'] = 'SHORT'
    entry = ledger.enter('short', **args)
    assert entry['status'] == 'FILLED'
    result = ledger.exit('cover', position_id='short', price=92., timestamp='2024-01-01 09:25', reason='TARGET')
    assert result['gross_pnl'] == 8 * entry['shares']
    assert ledger.snapshot()['equity'] == pytest.approx(100_000 + result['net_pnl'])


def test_runtime_configuration_change_fails_closed(tmp_path, monkeypatch):
    ledger = PaperLedger(tmp_path / 'paper.db')
    monkeypatch.setattr(config, 'MAX_POSITION_NOTIONAL_PCT', .9)
    with pytest.raises(ValueError, match='configuration changed'):
        ledger.enter('entry', **proposal())
    assert ledger.snapshot()['event_count'] == 0


def test_real_brain_signal_with_pandas_timestamp_can_be_journaled(tmp_path):
    raw = breakout_bars().iloc[:151]
    signal = BrainAV5().analyze_slice('A.NS', raw, market_bars(20_000, .05, volume=0), 'Bullish')
    assert signal is not None
    ledger = PaperLedger(tmp_path / 'paper.db')
    kwargs = dict(signal=signal, signal_time=signal['timestamp'],
                  timestamp=signal['timestamp'] + pd.Timedelta(minutes=5),
                  price=float(signal['close']), authorized=True)
    first = ledger.enter('actual-signal', **kwargs)
    assert first['status'] == 'FILLED'
    assert ledger.enter('actual-signal', **kwargs) == first
