"""Transactional simulated fills with durable state and idempotent event IDs.

Callers provide authorized signals and synchronized opening quotes. Exits are
explicit; this is a paper execution/accounting layer, not an autonomous bot.
"""
from contextlib import contextmanager
import hashlib
import json
import math
from numbers import Real
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pandas as pd
import numpy as np

from src import config
from src.backtest.constraints import PortfolioConstraints
from src.backtest.portfolio import Portfolio
from src.data.store import DataStore
from src.execution.capital import affordable_shares
from src.execution.costs import NSECostModel
from src.execution.model import ExecutionModel
from src.utils.market_time import market_timestamp, is_entry_time


def canonical(value):
    def native(item):
        if isinstance(item, pd.Timestamp):
            return market_timestamp(item).isoformat()
        if isinstance(item, np.generic):
            return item.item()
        raise TypeError(f'Unsupported paper-event value: {type(item).__name__}')
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False, default=native)


def positive(value):
    if not isinstance(value, Real) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ValueError('Prices and capital must be finite and positive')
    return float(value)


class PaperLedger:
    def __init__(self, path, initial_equity=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.cost_model = NSECostModel()
        self.constraints = PortfolioConstraints()
        self.execution = ExecutionModel()
        self.contract = {
            'version': 1,
            'parameters': {name: getattr(config, name) for name in (
                'MAX_POSITION_NOTIONAL_PCT', 'MAX_TRADE_RISK_PCT', 'MAX_PORTFOLIO_RISK_PCT',
                'RISK_PER_TRADE', 'MAX_DD_THRESHOLD', 'STOP_LOSS_MULTIPLIER', 'TARGET_MULTIPLIER',
                'GAP_VETO_MULT', 'KILL_SCORE_THRESHOLD', 'CONVICTION_MIN_MULT', 'CONVICTION_MAX_MULT',
                'TARGET_PORTFOLIO_VOL', 'EOD_FLATTEN_HOUR', 'EOD_FLATTEN_MINUTE')},
            'costs': vars(self.cost_model), 'sectors': self.constraints.sector_map,
        }
        with self._transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS paper_account (id INTEGER PRIMARY KEY CHECK(id=1), state TEXT NOT NULL, contract TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS paper_events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL, digest TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, result TEXT NOT NULL)')
            row = db.execute('SELECT state,contract FROM paper_account WHERE id=1').fetchone()
            if row:
                state = json.loads(row[0])
                if row[1] != canonical(self.contract):
                    raise ValueError('Paper account configuration changed; explicit migration or a new account is required')
                if initial_equity is not None and positive(initial_equity) != state['initial_equity']:
                    raise ValueError('Existing paper account initial equity does not match')
            else:
                capital = positive(100_000 if initial_equity is None else initial_equity)
                state = dict(initial_equity=capital, realized_equity=capital, equity=capital,
                             peak=capital, sod=capital, day=None, last_time=None, positions={},
                             daily_halted=False, system_halted=False)
                db.execute('INSERT INTO paper_account VALUES(1,?,?)', (canonical(state), canonical(self.contract)))

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            db.execute('PRAGMA busy_timeout=10000')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def snapshot(self):
        with self._transaction() as db:
            state = json.loads(db.execute('SELECT state FROM paper_account WHERE id=1').fetchone()[0])
            state['event_count'] = db.execute('SELECT COUNT(*) FROM paper_events').fetchone()[0]
            state['mode'] = 'PAPER'
            return state

    def mark(self, event_id, *, timestamp, quotes):
        """Persist synchronized marks and latch loss limits without placing an order."""
        stamp = market_timestamp(timestamp)
        payload = dict(timestamp=stamp.isoformat(), quotes=quotes)
        def transition(state):
            self._mark(state, stamp, quotes)
            return dict(status='MARKED', equity=state['equity'], daily_halted=state['daily_halted'],
                        system_halted=state['system_halted'])
        return self._apply(event_id, 'MARK', payload, transition)

    def _apply(self, event_id, kind, payload, transition):
        if any(getattr(config, key) != value for key, value in self.contract['parameters'].items()):
            raise ValueError('Paper account configuration changed during this session')
        if not isinstance(event_id, str) or not event_id or len(event_id) > 128:
            raise ValueError('A nonempty event ID of at most 128 characters is required')
        body = canonical(payload)
        digest = hashlib.sha256((kind + body).encode()).hexdigest()
        with self._transaction() as db:
            row = db.execute('SELECT digest,result FROM paper_events WHERE event_id=?', (event_id,)).fetchone()
            if row:
                if row[0] != digest:
                    raise ValueError('Event ID reused with different content')
                return json.loads(row[1])
            state = json.loads(db.execute('SELECT state FROM paper_account WHERE id=1').fetchone()[0])
            result = dict(transition(state), mode='PAPER', event_id=event_id)
            # The event and account checkpoint commit together, or neither does.
            db.execute('INSERT INTO paper_events(event_id,digest,kind,payload,result) VALUES(?,?,?,?,?)',
                       (event_id, digest, kind, body, canonical(result)))
            db.execute('UPDATE paper_account SET state=? WHERE id=1', (canonical(state),))
            return result

    def _mark(self, state, timestamp, quotes):
        stamp = market_timestamp(timestamp)
        if state['last_time'] and stamp < market_timestamp(state['last_time']):
            raise ValueError('Paper events must be chronological')
        day = str(stamp.date())
        if day != state['day']:
            state.update(day=day, sod=state['equity'], daily_halted=False)
        equity = state['realized_equity']
        for pos in state['positions'].values():
            quote = quotes.get(pos['ticker'])
            if quote is None or market_timestamp(quote['timestamp']) != stamp:
                raise ValueError('Every open position requires a quote at the event timestamp')
            price = positive(quote['price'])
            sign = 1 if pos['direction'] == 'LONG' else -1
            equity += (price - pos['entry']) * pos['shares'] * sign - self.cost_model.round_trip_cost(
                pos['entry'], price, pos['shares'], adv=pos['average_volume'], direction=pos['direction'])
        state.update(equity=equity, peak=max(state['peak'], equity), last_time=stamp.isoformat())
        self.constraints.reset_tracking(state['peak'])
        if not self.constraints.check_system_health(equity)[0]:
            state['system_halted'] = True
        if not self.constraints.check_daily_loss_limit(equity, state['sod'])[0]:
            state['daily_halted'] = True

    def enter(self, event_id, *, signal, signal_time, timestamp, price, authorized=False, quotes=None):
        """Simulate a next-open entry; caller must supply the regime authorization.

        A duplicate event returns its original result. Retrying with changed
        content is an error. Rejected entries are persisted as decisions too.
        """
        stamp, signal_stamp = market_timestamp(timestamp), market_timestamp(signal_time)
        price = positive(price)
        payload = dict(signal=signal, signal_time=signal_stamp.isoformat(), timestamp=stamp.isoformat(),
                       price=price, authorized=authorized, quotes=quotes or {})

        def transition(state):
            self._mark(state, stamp, payload['quotes'])
            def reject(reason):
                return {'status': 'REJECTED', 'reason': reason}
            if not isinstance(authorized, (bool, np.bool_)) or not authorized:
                return reject('DIRECTION_NOT_AUTHORIZED')
            if not is_entry_time(stamp):
                return reject('OUTSIDE_ENTRY_SESSION')
            if stamp != signal_stamp + pd.Timedelta(minutes=5) or stamp.date() != signal_stamp.date():
                return reject('SIGNAL_EXPIRED')
            if state['system_halted'] or state['daily_halted']:
                return reject('RISK_HALT')
            if not isinstance(signal.get('kill_score'), Real) or not math.isfinite(signal['kill_score']):
                return reject('INVALID_SIGNAL')
            if signal['kill_score'] < config.KILL_SCORE_THRESHOLD:
                return reject('SCORE_VETO')
            close, atr = positive(signal.get('close')), positive(signal.get('atr'))
            if abs(price - close) > config.GAP_VETO_MULT * atr:
                return reject('GAP_VETO')
            ticker = DataStore._normalize_ticker(signal['ticker'])
            if ticker == '^NSEI':
                return reject('BENCHMARK_NOT_TRADABLE')
            order = self.execution.generate_orders(signal, entry_price=price, equity=state['equity'],
                current_dd=(state['peak'] - state['equity']) / state['peak'])
            if not order['valid_rr']:
                return reject('INVALID_ORDER')
            portfolio = Portfolio()
            portfolio.open_positions = [SimpleNamespace(**p) for p in state['positions'].values()]
            volume = float(signal.get('features', {}).get('vol_ma_20', 0))
            if not math.isfinite(volume) or volume < 0:
                return reject('INVALID_VOLUME')
            requested = order['shares']
            order['shares'] = affordable_shares(order, equity=state['equity'],
                realized_equity=state['realized_equity'], positions=portfolio.open_positions,
                cost_model=self.cost_model, direction=signal['direction'], average_volume=volume,
                position_cap=config.MAX_POSITION_NOTIONAL_PCT)
            if not order['shares']:
                return reject('INSUFFICIENT_CAPITAL')
            allowed, reason = self.constraints.can_open_trade(portfolio, ticker, state['equity'],
                                                               timestamp=signal_stamp, order=order)
            if not allowed:
                return reject(reason)
            state['positions'][event_id] = dict(ticker=ticker, direction=signal['direction'],
                entry=order['entry'], shares=order['shares'], initial_stop=order['stop'],
                target=order['target'], average_volume=volume, entry_time=stamp.isoformat())
            current_quotes = dict(payload['quotes'], **{ticker: {'price': price, 'timestamp': stamp.isoformat()}})
            self._mark(state, stamp, current_quotes)
            return dict(order, status='FILLED', position_id=event_id, ticker=ticker,
                        requested_shares=requested)
        return self._apply(event_id, 'ENTRY', payload, transition)

    def exit(self, event_id, *, position_id, timestamp, price, reason, quotes=None):
        """Simulate a full exit. No partial fills or automatic stop processing."""
        stamp, price = market_timestamp(timestamp), positive(price)
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('An exit reason is required')
        payload = dict(position_id=position_id, timestamp=stamp.isoformat(), price=price,
                       reason=reason, quotes=quotes or {})
        def transition(state):
            pos = state['positions'].get(position_id)
            if pos is None:
                raise ValueError('Position is not open')
            marks = dict(payload['quotes'], **{pos['ticker']: {'price': price, 'timestamp': stamp.isoformat()}})
            self._mark(state, stamp, marks)
            gross = (price - pos['entry']) * pos['shares'] * (1 if pos['direction'] == 'LONG' else -1)
            costs = self.cost_model.round_trip_cost(pos['entry'], price, pos['shares'],
                                                    adv=pos['average_volume'], direction=pos['direction'])
            state['realized_equity'] += gross - costs
            del state['positions'][position_id]
            self._mark(state, stamp, marks)
            return dict(status='FILLED', position_id=position_id, price=price, gross_pnl=gross,
                        costs=costs, net_pnl=gross-costs, reason=reason)
        return self._apply(event_id, 'EXIT', payload, transition)
