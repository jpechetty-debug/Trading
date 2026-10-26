"""Reproducible offline synthetic runtime benchmark, not strategy performance evidence."""
import argparse
import cProfile
import hashlib
import json
from pathlib import Path
import pstats
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.backtest.portfolio_engine import PortfolioEngine


def run(days=5, ticker_count=8, profile=False):
    symbols = ['ABB.NS', 'ACC.NS', 'CIPLA.NS', 'TCS.NS', 'HDFCBANK.NS', 'ITC.NS', 'ONGC.NS', 'DLF.NS'][:ticker_count]
    index = pd.DatetimeIndex([d + pd.Timedelta(hours=9, minutes=15 + 5*i)
                              for d in pd.bdate_range('2024-01-01', periods=days) for i in range(75)])
    data = {}
    for seed, ticker in enumerate(['^NSEI', *symbols]):
        rng = np.random.default_rng(seed)
        initial = 20_000 if ticker == '^NSEI' else 100 + seed * 100
        close = initial * np.exp(np.cumsum(rng.normal(.00003, .0005, len(index))))
        opening = np.r_[close[0], close[:-1]]
        data[ticker] = pd.DataFrame({'Open': opening, 'High': np.maximum(opening, close)*1.0002,
                                    'Low': np.minimum(opening, close)*.9998, 'Close': close,
                                    'Volume': rng.uniform(900_000, 1_100_000, len(index))}, index=index)
    class Store:
        def load_ticker(self, ticker):
            return data[ticker].copy()
    engine = PortfolioEngine(symbols, start_date=index[0], initial_equity=1_000_000, data_store=Store())
    profiler = cProfile.Profile() if profile else None
    start = time.perf_counter()
    if profiler:
        profiler.enable()
    trades = engine.run()
    if profiler:
        profiler.disable()
        pstats.Stats(profiler).sort_stats('cumtime').print_stats(18)
    elapsed = time.perf_counter() - start
    serialized = trades.to_json(orient='records', date_format='iso', double_precision=12)
    return {'synthetic': True, 'days': days, 'tickers': ticker_count, 'elapsed_seconds': elapsed,
            'profiled': profile, 'trades': json.loads(serialized),
            'trade_digest': hashlib.sha256(serialized.encode()).hexdigest(),
            'events': engine.events}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--days', type=int, default=5)
    parser.add_argument('--tickers', type=int, choices=range(1, 9), default=8)
    parser.add_argument('--profile', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.days < 3:
        parser.error('At least three sessions are required for indicator warm-up')
    result = run(args.days, args.tickers, args.profile)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k not in {'trades', 'events'}}))
