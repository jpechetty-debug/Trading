import asyncio
from unittest.mock import Mock, AsyncMock

import pandas as pd
import pytest

from src.scanner import Scanner
from src.live_scanner import LiveScanner
from src.data.websocket_feed import Candle, CandleBuilder, Tick
from src.data.live_validation import completed_bars
from src.data.validation import normalize_ohlcv
from tests.market_fixtures import market_bars


def make_stream_scanner(monkeypatch, simulated=False):
    raw = market_bars(2500.)
    now = raw.index[-1] + pd.Timedelta(minutes=5, seconds=5)
    scanner = LiveScanner(['A.NS'], feed=None if simulated else Mock(),
                          enable_alerts=not simulated, now=lambda: now)
    scanner.nifty_df = market_bars(20_000., .05, volume=0)
    scanner.candle_builder.get_dataframe = Mock(return_value=raw)
    expected = {'ticker': 'A.NS', 'timestamp': raw.index[-1], 'close': float(raw.Close.iloc[-1]),
                'atr': 25., 'direction': 'LONG', 'kill_score': 8.5, 'support': 2400.,
                'resistance': 2600., 'swing_low': 2450., 'reasons': ['Momentum']}
    scanner.brain.analyze_slice = Mock(return_value=expected)
    scanner.exec_model.generate_orders = Mock(return_value={
        'entry': expected['close'], 'stop': 2450., 'target': 2650.,
        'shares': 100, 'risk_reward': 2.5, 'valid_rr': True})
    scanner.alerts.send_signal_alert = Mock(return_value={'telegram'})
    db = Mock()
    monkeypatch.setattr('src.live_scanner.get_db', Mock(return_value=db))
    candle = Candle('A.NS', 2500, 2510, 2490, 2505, 1_000_000, raw.index[-1])
    return scanner, expected, candle, db


def test_stream_rejects_invalid_orders_before_db_or_alert(monkeypatch):
    scanner, _, candle, db = make_stream_scanner(monkeypatch)
    scanner.exec_model.generate_orders.return_value['valid_rr'] = False
    scanner.on_candle_close('A.NS', candle)
    scanner.exec_model.generate_orders.assert_called_once()
    db.log_trade.assert_not_called()
    scanner.alerts.send_signal_alert.assert_not_called()


def test_stream_merges_executable_levels_into_alert_and_deduplicates_bar(monkeypatch):
    scanner, expected, candle, db = make_stream_scanner(monkeypatch)
    scanner.on_candle_close('A.NS', candle)
    scanner.on_candle_close('A.NS', candle)
    db.log_trade.assert_called_once()
    scanner.alerts.send_signal_alert.assert_called_once()
    result = scanner.alerts.send_signal_alert.call_args.args[0]
    assert result['ticker'] == 'A.NS'
    assert result['stop'] == 2450
    assert result['target'] == 2650
    assert result['shares'] == 100
    assert scanner.exec_model.generate_orders.call_args.args[0] == expected


@pytest.mark.parametrize('context', [None, 'stale'])
def test_stream_does_not_analyze_without_current_benchmark(monkeypatch, context):
    scanner, _, candle, db = make_stream_scanner(monkeypatch)
    scanner.nifty_df = None if context is None else scanner.nifty_df.iloc[:-1]
    scanner.on_candle_close('A.NS', candle)
    scanner.brain.analyze_slice.assert_not_called()
    db.log_trade.assert_not_called()


def test_failed_refresh_clears_previous_context(monkeypatch):
    scanner, _, _, _ = make_stream_scanner(monkeypatch)
    scanner._context_scanner.fetch_nifty_intraday = Mock(return_value=None)
    scanner.refresh_market_context()
    assert scanner.nifty_df is None
    assert scanner.market_regime == 'Neutral'


def test_mock_stream_cannot_write_real_signals_or_send_alerts(monkeypatch):
    scanner, _, candle, db = make_stream_scanner(monkeypatch, simulated=True)
    scanner.on_candle_close('A.NS', candle)
    scanner.exec_model.generate_orders.assert_called_once()
    db.log_trade.assert_not_called()
    scanner.alerts.send_signal_alert.assert_not_called()
    with pytest.raises(ValueError, match='Simulated'):
        LiveScanner(['A.NS'], enable_alerts=True)


def test_stream_revisits_early_symbols_when_breadth_snapshot_completes(monkeypatch):
    scanner, _, candle, db = make_stream_scanner(monkeypatch)
    scanner.symbols = ['A.NS', 'B.NS']
    raw = market_bars(2500.)
    available = {'A.NS': raw, 'B.NS': None}
    scanner.candle_builder.get_dataframe = lambda ticker, **kw: available[ticker]
    scanner.on_candle_close('A.NS', candle)
    scanner.brain.analyze_slice.assert_not_called()
    available['B.NS'] = raw
    scanner.on_candle_close('B.NS', candle)
    assert scanner.brain.analyze_slice.call_count == 2
    assert {call.args[0] for call in scanner.brain.analyze_slice.call_args_list} == {'A.NS', 'B.NS'}
    assert db.log_trade.call_count == 2


@pytest.mark.parametrize('stage', ['connect', 'subscribe', 'stream'])
def test_feed_failure_disconnects_and_is_not_swallowed(monkeypatch, stage):
    scanner, _, _, _ = make_stream_scanner(monkeypatch)
    scanner.refresh_market_context = Mock()
    scanner.feed.connect = AsyncMock()
    scanner.feed.subscribe = AsyncMock()
    scanner.feed.stream = AsyncMock()
    getattr(scanner.feed, stage).side_effect = RuntimeError('feed disconnected')
    scanner.feed.disconnect = AsyncMock()
    with pytest.raises(RuntimeError, match='feed disconnected'):
        asyncio.run(scanner.run(duration_seconds=1))
    scanner.feed.disconnect.assert_awaited_once()


def polling_scanner(monkeypatch):
    scanner = Scanner()
    raw = market_bars(2500.)
    now = raw.index[-1] + pd.Timedelta(minutes=5, seconds=5)
    monkeypatch.setattr('src.scanner.ALL_TICKERS', ['A.NS'])
    scanner.fetch_nifty_intraday = Mock(return_value=market_bars(20_000, .05, volume=0))
    scanner.fetch_bulk_data = Mock(return_value=raw)
    scanner.brain.analyze_slice = Mock(return_value={
        'ticker': 'A.NS', 'close': raw.Close.iloc[-1], 'atr': 25., 'kill_score': 8., 'direction': 'LONG'})
    scanner.exec_model.generate_orders = Mock(return_value={
        'entry': 2500., 'stop': 2450., 'target': 2650., 'shares': 100, 'valid_rr': True})
    db = Mock()
    monkeypatch.setattr('src.scanner.get_db', Mock(return_value=db))
    return scanner, raw, now, db


def test_polling_ignores_forming_candle(monkeypatch):
    scanner, raw, now, db = polling_scanner(monkeypatch)
    extended = raw.copy()
    extended.loc[now.floor('5min')] = [9999, 9999, 9999, 9999, 1_000_000]
    scanner.fetch_bulk_data.return_value = extended
    result = scanner.scan_market(now=now)
    assert len(result) == 1
    analyzed = scanner.brain.analyze_slice.call_args.args[1]
    assert analyzed.index[-1] == raw.index[-1]
    assert analyzed.Close.iloc[-1] != 9999
    db.log_trade.assert_called_once()


def test_polling_obeys_breadth_veto(monkeypatch):
    scanner, _, now, db = polling_scanner(monkeypatch)
    scanner.fetch_bulk_data.return_value = market_bars(2500., drift=-.015)
    assert scanner.scan_market(now=now) == []
    scanner.exec_model.generate_orders.assert_not_called()
    db.log_trade.assert_not_called()


def test_polling_missing_benchmark_fails_before_scanning_stocks(monkeypatch):
    scanner, _, now, db = polling_scanner(monkeypatch)
    scanner.fetch_nifty_intraday.return_value = None
    assert scanner.scan_market(now=now) == []
    scanner.fetch_bulk_data.assert_not_called()
    db.log_trade.assert_not_called()


def test_polling_requires_adequate_universe_coverage(monkeypatch):
    scanner, raw, now, db = polling_scanner(monkeypatch)
    monkeypatch.setattr('src.scanner.ALL_TICKERS', ['A.NS', 'B.NS', 'C.NS'])
    scanner.fetch_bulk_data.return_value = pd.concat({'A.NS': raw}, axis=1)
    assert scanner.scan_market(now=now) == []
    assert scanner.last_scan_status == 'INSUFFICIENT_BREADTH_COVERAGE'
    scanner.brain.analyze_slice.assert_not_called()


def test_zero_volume_index_does_not_lose_all_bars(monkeypatch):
    raw = market_bars(20_000, .05, volume=0)
    monkeypatch.setattr('src.scanner.yf.download', lambda *a, **kw: raw.copy())
    fetched = Scanner().fetch_nifty_intraday()
    pd.testing.assert_frame_equal(fetched, raw)


def test_completed_bars_rejects_stale_data_and_discards_invalid_forming_row():
    raw = market_bars()
    now = raw.index[-1] + pd.Timedelta(minutes=5, seconds=5)
    with pytest.raises(ValueError, match='Stale'):
        completed_bars(raw.iloc[:-1], now)
    extended = raw.copy()
    extended.loc[now.floor('5min')] = float('nan')
    pd.testing.assert_frame_equal(completed_bars(extended, now), raw, check_freq=False)


def test_off_grid_five_minute_data_is_rejected():
    raw = market_bars()
    raw.index += pd.Timedelta(minutes=1)
    with pytest.raises(ValueError, match='boundaries'):
        normalize_ohlcv(raw)


def test_late_tick_cannot_corrupt_current_candle():
    builder = CandleBuilder()
    for price, time in [(100, '09:16'), (105, '09:17'), (20, '09:16:30'), (110, '09:20')]:
        builder.process_tick(Tick('A', price, 1, pd.Timestamp('2024-01-01 ' + time)))
    candle = builder.candles['A'][0]
    assert candle.close == 105
    assert candle.low == 100
    assert candle.volume == 2


def test_candle_callback_failure_does_not_duplicate_completed_candle():
    builder = CandleBuilder()
    builder.on_candle_close = Mock(side_effect=RuntimeError('analyzer failure'))
    for time in ('09:15', '09:20', '09:20:01'):
        builder.process_tick(Tick('A', 100., 1, pd.Timestamp('2024-01-01 ' + time)))
    assert len(builder.candles['A']) == 1
    builder.on_candle_close.assert_called_once()
    assert builder.current['A']['volume'] == 2


def test_candle_boundaries_convert_utc_to_ist():
    builder = CandleBuilder()
    builder.process_tick(Tick('A', 100., 1, pd.Timestamp('2024-01-01 03:46Z')))
    assert builder.current['A']['start'] == pd.Timestamp('2024-01-01 09:15')


@pytest.mark.parametrize('price,volume', [(0, 1), (-1, 1), (float('nan'), 1), (float('inf'), 1), (100, -1)])
def test_invalid_tick_does_not_create_candle(price, volume):
    builder = CandleBuilder()
    builder.process_tick(Tick('A', price, volume, pd.Timestamp('2024-01-01 09:15')))
    assert not builder.current


def test_candle_history_is_bounded():
    builder = CandleBuilder(max_candles=250)
    for time in pd.date_range('2024-01-01 09:15', periods=270, freq='5min'):
        builder.process_tick(Tick('A', 100., 1, time))
    assert len(builder.candles['A']) == 250
