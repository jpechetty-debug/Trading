"""Local files, validation, and cache behavior with no market network calls."""
import pandas as pd
import pytest

from scripts.audit_local_data import audit_file, audit_paths
from src.data.store import DataStore
from src.data.validation import normalize_ohlcv
from src.utils.market_time import is_entry_time, is_flatten_time


@pytest.fixture
def bars():
    index = pd.date_range('2024-01-01 09:15', periods=4, freq='5min')
    return pd.DataFrame({'Open': [100.0] * 4, 'High': [102.0] * 4,
                         'Low': [99.0] * 4, 'Close': [101.0] * 4,
                         'Volume': [1_000_000.0] * 4}, index=index)


def save_csv(frame, path):
    frame.rename_axis('Datetime').to_csv(path)


def test_external_folders_are_read_only_and_cached_frames_are_isolated(tmp_path, bars):
    external = tmp_path / 'external'
    external.mkdir()
    source = external / 'A.NS_5m.csv'
    save_csv(bars, source)
    original = source.read_bytes()
    primary = tmp_path / 'primary'
    store = DataStore(storage_dir=primary, search_dirs=[external], allow_network=False)
    first = store.load_ticker('a')
    first.iloc[0, 0] = -999
    second = store.load_ticker('A.NS')
    assert second.iloc[0]['Open'] == 100
    assert source.read_bytes() == original
    assert not primary.exists()


def test_offline_mode_never_downloads_even_for_explicit_update(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail('Offline DataStore attempted a download')
    monkeypatch.setattr('src.data.store.yf.download', forbidden)
    store = DataStore(storage_dir=tmp_path, allow_network=False)
    assert store.load_ticker('MISSING') is None
    assert store.update_ticker('MISSING') is None


def test_bse_symbol_uses_existing_file(tmp_path, bars):
    save_csv(bars, tmp_path / 'EXAMPLE.BO.csv')
    store = DataStore(storage_dir=tmp_path, allow_network=False)
    assert store.load_ticker('EXAMPLE.BO') is not None
    assert list(store.cache) == ['EXAMPLE.BO']


def test_lru_access_controls_eviction(tmp_path, bars):
    for symbol in ('A', 'B', 'C'):
        save_csv(bars, tmp_path / f'{symbol}.NS.csv')
    store = DataStore(2, storage_dir=tmp_path, allow_network=False)
    for symbol in ('A', 'B', 'A', 'C'):
        assert store.load_ticker(symbol) is not None
    assert list(store.cache) == ['A.NS', 'C.NS']


def test_invalid_local_data_does_not_silently_switch_to_network(monkeypatch, tmp_path):
    (tmp_path / 'A.NS.parquet').write_bytes(b'corrupt parquet')
    def forbidden(*args, **kwargs):
        pytest.fail('Invalid local vintage was silently replaced from the network')
    monkeypatch.setattr('src.data.store.yf.download', forbidden)
    assert DataStore(storage_dir=tmp_path).load_ticker('A') is None


def test_invalid_download_preserves_previous_cache(monkeypatch, tmp_path, bars):
    path = tmp_path / 'A.NS.parquet'
    bars.to_parquet(path)
    original = path.read_bytes()
    invalid = bars.copy()
    invalid.iloc[0, 0] = float('nan')
    monkeypatch.setattr('src.data.store.yf.download', lambda *a, **kw: invalid)
    store = DataStore(storage_dir=tmp_path)
    assert store.update_ticker('A') is None
    assert path.read_bytes() == original


def test_valid_download_roundtrips_parquet(monkeypatch, tmp_path, bars):
    monkeypatch.setattr('src.data.store.yf.download', lambda *a, **kw: bars)
    store = DataStore(storage_dir=tmp_path)
    expected = store.update_ticker('A')
    loaded = DataStore(storage_dir=tmp_path, allow_network=False).load_ticker('A')
    pd.testing.assert_frame_equal(expected, loaded, check_freq=False)


@pytest.mark.parametrize('case', ['daily', 'duplicate', 'nan', 'negative', 'bounds', 'no_volume', 'wrong_interval'])
def test_bad_market_data_is_rejected(bars, case):
    if case == 'daily':
        bars.index = pd.date_range('2024-01-01', periods=4, freq='D')
    elif case == 'duplicate':
        bars.index = [bars.index[0]] * 4
        bars.index = pd.DatetimeIndex(bars.index)
    elif case == 'nan':
        bars.loc[bars.index[0], 'Close'] = float('nan')
    elif case == 'negative':
        bars.loc[bars.index[0], 'Volume'] = -1
    elif case == 'bounds':
        bars.loc[bars.index[0], 'Open'] = 999
    elif case == 'no_volume':
        bars = bars.drop(columns='Volume')
    else:
        bars.index = pd.date_range('2024-01-01 09:15', periods=4, freq='15min')
    with pytest.raises(ValueError):
        normalize_ohlcv(bars)


def test_missing_bars_are_allowed_without_filling_them(bars):
    result = normalize_ohlcv(bars.drop(index=bars.index[2]))
    assert len(result) == 3


def test_utc_data_is_converted_before_timezone_is_removed(bars):
    bars.index = bars.index.tz_localize('Asia/Kolkata').tz_convert('UTC')
    result = normalize_ohlcv(bars)
    assert result.index[0] == pd.Timestamp('2024-01-01 09:15')
    assert result.index.tz is None
    assert is_flatten_time(pd.Timestamp('2024-01-01 09:45Z'))
    assert not is_entry_time(pd.Timestamp('2024-01-01 09:45Z'))
    assert is_entry_time(pd.Timestamp('2024-01-01 04:00Z'))


def test_audit_records_daily_data_and_missing_volume_without_fabricating_bars(tmp_path, bars):
    bars.index = pd.date_range('2024-01-01', periods=4, freq='D')
    path = tmp_path / 'A.NS_1d.csv'
    bars.drop(columns='Volume').rename_axis('Date').to_csv(path)
    report = audit_file(path)
    assert report['median_interval_seconds'] == 86400
    assert report['rows'] == 4
    assert not report['compatible_5m_ohlcv']
    assert 'Volume' in report['error']
    assert len(report['sha256']) == 64


def test_audit_deduplicates_overlapping_inputs(tmp_path, bars):
    path = tmp_path / 'A.NS.csv'
    save_csv(bars, path)
    report = audit_paths([tmp_path, path])
    assert report['files_checked'] == report['compatible_files'] == 1


@pytest.mark.parametrize('ticker', ['../A', 'A/B', 'A\\B', ''])
def test_ticker_cannot_escape_data_root(ticker, tmp_path):
    with pytest.raises(ValueError, match='ticker'):
        DataStore(storage_dir=tmp_path, allow_network=False).load_ticker(ticker)
