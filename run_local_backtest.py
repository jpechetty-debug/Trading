"""Run the portfolio engine on local five-minute files, with downloads disabled."""
import argparse
import json
from pathlib import Path

from src.backtest.portfolio_engine import PortfolioEngine
from src.data.store import DataStore
from src.backtest.diagnostics import summarize_execution


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', action='append', required=True, type=Path,
                        help='Read-only folder containing OHLCV files; repeat to search multiple folders')
    parser.add_argument('--tickers', nargs='+', required=True)
    parser.add_argument('--start-date', default='2024-01-01')
    parser.add_argument('--end-date', help='Exclusive end timestamp/date (IST)')
    parser.add_argument('--initial-equity', type=float, default=100_000)
    parser.add_argument('--max-position-notional-pct', type=float,
                        help='Entry capital ceiling per position; default is config.MAX_POSITION_NOTIONAL_PCT')
    parser.add_argument('--output', type=Path, help='Optional JSON trade report')
    args = parser.parse_args()
    if args.max_position_notional_pct is not None:
        if not 0 < args.max_position_notional_pct <= 1:
            parser.error('Position notional cap must be in (0, 1]')
    for root in args.data_dir:
        if not root.is_dir():
            parser.error(f'Data directory does not exist: {root}')
    store = DataStore(storage_dir=args.data_dir[0], search_dirs=args.data_dir[1:], allow_network=False)
    try:
        missing = [ticker for ticker in ['^NSEI', *args.tickers] if store.load_ticker(ticker) is None]
        if missing:
            parser.error('Missing or incompatible five-minute data: ' + ', '.join(missing))
        engine = PortfolioEngine(args.tickers, start_date=args.start_date,
                                 initial_equity=args.initial_equity, data_store=store,
                                 end_date=args.end_date, max_position_notional_pct=args.max_position_notional_pct)
        results = engine.run()
    except ValueError as exc:
        parser.error(str(exc))
    report = {
        'mode': 'offline',
        'data_directories': [str(p.resolve()) for p in args.data_dir],
        'initial_equity': engine.initial_equity,
        'final_equity': engine.equity,
        'closed_trades': len(results),
        'trades': json.loads(results.to_json(orient='records', date_format='iso')),
        'events': engine.events,
        'execution_model': 'next_bar_open',
        'max_position_notional_pct': engine.max_position_notional_pct,
        'diagnostics': summarize_execution(results, engine.events, engine.exposure_history),
        'assumptions': ['Signals fill at the next contiguous five-minute bar open after revalidation.',
                        'Remaining positions close at the last observed price when data ends; stale marks are labelled.'],
    }
    if args.output:
        # Keep all external data directories read-only, including when output was mistyped.
        output = args.output.resolve()
        if any(output.is_relative_to(root.resolve()) for root in args.data_dir):
            parser.error('Output must be outside the input data directories')
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
