"""Apply explicit simulated ENTRY/EXIT/MARK events to a persistent paper account.

No market downloads, alerts, broker APIs, or real orders. Replaying identical
event IDs is idempotent; each successful event commits independently.
"""
import argparse
import json
from pathlib import Path

from src.paper.ledger import PaperLedger


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', required=True, type=Path)
    parser.add_argument('--initial-equity', type=float)
    parser.add_argument('--events', type=Path, help='JSONL: action, event_id, and method parameters')
    args = parser.parse_args()
    if args.events and args.events.resolve() == args.ledger.resolve():
        parser.error('Input events and ledger must be different files')
    try:
        account = PaperLedger(args.ledger, initial_equity=args.initial_equity)
        if args.events:
            with args.events.open(encoding='utf-8') as source:
                for number, line in enumerate(source, 1):
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    action = event.pop('action')
                    if action not in {'enter', 'exit', 'mark'}:
                        raise ValueError(f'Line {number}: unsupported paper action')
                    result = getattr(account, action)(**event)
                    print(json.dumps(result, allow_nan=False))
        print(json.dumps(account.snapshot(), indent=2, allow_nan=False))
    except (ValueError, TypeError, KeyError, OSError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
