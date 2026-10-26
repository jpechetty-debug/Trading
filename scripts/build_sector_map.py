"""Build sector reference data from a saved NSE constituent CSV, without network I/O."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.data.universe import NIFTY_200


def build(source, output, provenance):
    source, output, provenance = map(Path, (source, output, provenance))
    if source.resolve() in {output.resolve(), provenance.resolve()}:
        raise ValueError('Output must not overwrite the source')
    if output.resolve() == provenance.resolve():
        raise ValueError('Sector CSV and provenance require distinct paths')
    frame = pd.read_csv(source, dtype=str, keep_default_na=False)
    if not {'Symbol', 'Industry'}.issubset(frame.columns):
        raise ValueError('Expected NSE Symbol and Industry columns')
    symbols = frame.Symbol.str.strip().str.upper() + '.NS'
    sectors = frame.Industry.str.strip()
    if symbols.duplicated().any() or (symbols == '.NS').any() or (sectors == '').any():
        raise ValueError('Blank or duplicate classification records')
    result = pd.DataFrame({'Symbol': symbols, 'Sector': sectors}).sort_values('Symbol')
    missing = sorted(set(NIFTY_200) - set(result.Symbol))
    report = {
        'source_path': str(source.resolve()), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'source_kind': 'Saved NSE constituent CSV; Industry column used consistently as sector taxonomy',
        'mapped_symbols': len(result), 'sector_count': int(result.Sector.nunique()),
        'configured_universe_size': len(set(NIFTY_200)),
        'mapped_universe_symbols': len(set(NIFTY_200)) - len(missing), 'missing_universe_symbols': missing,
        'limitations': 'Snapshot classifications, not point-in-time history. Missing names retain conservative OTHER treatment; no identity aliases inferred.',
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    provenance.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output, index=False)
    provenance.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, default=Path('data/nse_sectors.csv'))
    parser.add_argument('--provenance', type=Path, default=Path('data/nse_sectors.provenance.json'))
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.output, args.provenance), indent=2))
