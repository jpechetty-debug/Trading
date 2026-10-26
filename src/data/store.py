"""Parquet cache with optional read-only data roots and explicit offline mode."""
from collections import OrderedDict
from pathlib import Path
from threading import RLock
import os
import tempfile

import pandas as pd
import yfinance as yf

from src.data.validation import normalize_ohlcv
from src.logger import get_logger

log = get_logger(__name__)
STORAGE_DIR = os.path.join(os.path.dirname(__file__), 'storage')


class DataStore:
    def __init__(self, max_cache=200, *, storage_dir=None, search_dirs=(), allow_network=True):
        if not isinstance(max_cache, int) or max_cache < 1:
            raise ValueError('max_cache must be a positive integer')
        self.cache = OrderedDict()
        self.max_cache = max_cache
        self.storage_dir = Path(storage_dir if storage_dir is not None else STORAGE_DIR)
        self.search_dirs = tuple(Path(p) for p in search_dirs)
        self.allow_network = allow_network
        self._cache_lock = RLock()

    @staticmethod
    def _normalize_ticker(ticker):
        ticker = ticker.strip().upper()
        if not ticker or any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.^&-_' for c in ticker):
            raise ValueError('Invalid ticker')
        if not ticker.startswith('^') and not ticker.endswith(('.NS', '.BO')):
            ticker += '.NS'
        return ticker

    def _remember(self, ticker, frame):
        with self._cache_lock:
            self.cache[ticker] = frame.copy()
            self.cache.move_to_end(ticker)
            while len(self.cache) > self.max_cache:
                self.cache.popitem(last=False)

    def update_ticker(self, ticker):
        """Download valid bars and atomically replace only this store's cache."""
        ticker = self._normalize_ticker(ticker)
        if not self.allow_network:
            log.warning('Offline mode: download disabled for %s', ticker)
            return None
        temporary_path = None
        try:
            log.info('Ingesting %s...', ticker)
            frame = yf.download(ticker, period='59d', interval='5m', progress=False, threads=False)
            frame = normalize_ohlcv(frame)
            self.storage_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.storage_dir, suffix='.parquet', delete=False) as temporary:
                temporary_path = Path(temporary.name)
            frame.to_parquet(temporary_path, engine='pyarrow')
            temporary_path.replace(self.storage_dir / f'{ticker}.parquet')
            self._remember(ticker, frame)
            return frame.copy()
        except Exception as exc:
            log.error('Error updating %s: %s', ticker, exc)
            return None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def load_ticker(self, ticker):
        """Read validated local bars first; offline mode never falls back to Yahoo.

        Additional folders are read-only and searched after storage_dir. Supported
        names: SYMBOL.NS.parquet, SYMBOL.NS_5m.csv, and SYMBOL.NS.csv.
        Callers receive independent frames so indicator updates cannot poison RAM.
        """
        ticker = self._normalize_ticker(ticker)
        with self._cache_lock:
            if ticker in self.cache:
                self.cache.move_to_end(ticker)
                return self.cache[ticker].copy()
        found_local = False
        for root in (self.storage_dir, *self.search_dirs):
            for filename in (f'{ticker}.parquet', f'{ticker}_5m.csv', f'{ticker}.csv'):
                path = root / filename
                if not path.is_file():
                    continue
                found_local = True
                try:
                    frame = (pd.read_parquet(path, engine='pyarrow') if path.suffix == '.parquet'
                             else pd.read_csv(path))
                    frame = normalize_ohlcv(frame)
                except Exception as exc:
                    log.error('Rejected local data %s: %s', path, exc)
                    continue
                self._remember(ticker, frame)
                return frame.copy()
        # Bad local data must be visible, not replaced silently by another vintage.
        if found_local or not self.allow_network:
            log.warning('No compatible local five-minute OHLCV for %s', ticker)
            return None
        return self.update_ticker(ticker)


if __name__ == '__main__':
    store = DataStore()
    store.update_ticker('RELIANCE.NS')
    store.update_ticker('^NSEI')
