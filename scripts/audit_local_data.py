"""Inspect local market files without downloading data or altering sources."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.validation import normalize_ohlcv
from src.utils.market_time import market_index


def audit_file(path):
    path = Path(path).resolve()
    result = {'path': str(path), 'compatible_5m_ohlcv': False}
    try:
        result['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        frame = pd.read_parquet(path) if path.suffix.lower() == '.parquet' else pd.read_csv(path)
        result['rows'] = len(frame)
        result['columns'] = [str(c) for c in frame.columns]
        dates = next((c for c in frame.columns if str(c).lower() in {'date', 'datetime', 'timestamp'}), None)
        index = market_index(frame[dates]) if dates is not None else (
            market_index(frame.index) if isinstance(frame.index, pd.DatetimeIndex) else None
        )
        if index is not None and len(index):
            result['start'] = str(index.min())
            result['end'] = str(index.max())
            result['duplicate_timestamps'] = int(index.duplicated().sum())
            deltas = index.sort_values().to_series().diff().dt.total_seconds().dropna()
            result['median_interval_seconds'] = float(deltas.median()) if len(deltas) else None
        normalize_ohlcv(frame)
        result['compatible_5m_ohlcv'] = True
    except Exception as exc:
        result['error'] = str(exc)
    return result


def audit_paths(paths):
    files = set()
    for value in paths:
        path = Path(value).resolve()
        if not path.exists():
            raise ValueError(f'Input does not exist: {path}')
        if path.is_file():
            if path.suffix.lower() not in {'.csv', '.parquet'}:
                raise ValueError(f'Expected CSV or Parquet: {path}')
            files.add(path)
        else:
            files.update(p for p in path.rglob('*') if p.is_file() and p.suffix.lower() in {'.csv', '.parquet'})
    results = [audit_file(path) for path in sorted(files)]
    return {
        'files_checked': len(results),
        'compatible_files': sum(r['compatible_5m_ohlcv'] for r in results),
        'notes': [
            'Structural compatibility does not certify price adjustments, exchange calendars, or provenance.',
            'This strategy also requires timestamp-aligned ^NSEI five-minute benchmark data and indicator warmup.',
            'Daily bars and absent volume are never synthesized into five-minute OHLCV.',
        ],
        'files': results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('paths', nargs='+', help='CSV/Parquet files or folders (read-only)')
    parser.add_argument('--output', type=Path, help='Optional JSON audit output')
    args = parser.parse_args()
    try:
        report = audit_paths(args.paths)
    except ValueError as exc:
        parser.error(str(exc))
    if args.output:
        output = args.output.resolve()
        input_files = {Path(item['path']) for item in report['files']}
        if output in input_files:
            parser.error('Audit output must not overwrite an input file')
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(f"Checked {report['files_checked']} files; {report['compatible_files']} compatible five-minute OHLCV files")
    for item in report['files']:
        if not item['compatible_5m_ohlcv']:
            print(f"  {Path(item['path']).name}: {item['error']}")


if __name__ == '__main__':
    main()
