"""Descriptive execution diagnostics; never a claim of out-of-sample edge."""
from collections import Counter


def summarize_execution(trades, events=(), exposures=()):
    def group(frame):
        count = len(frame)
        risk = frame['Initial_Risk']
        return {
            'trades': count,
            'gross_pnl': float(frame['Gross_PnL'].sum()),
            'costs': float(frame['Costs'].sum()),
            'net_pnl': float(frame['PnL'].sum()),
            'mean_net_r': float((frame['PnL'] / risk).mean()) if count else None,
            'mean_cost_r': float((frame['Costs'] / risk).mean()) if count else None,
            'mean_bars_held': float(frame['Held'].mean()) if count else None,
        }

    return {
        'overall': group(trades),
        'by_exit_reason': {str(reason): group(frame) for reason, frame in trades.groupby('Reason')},
        'by_direction': {direction: group(trades[trades.direction == direction])
                         for direction in ('LONG', 'SHORT')},
        'events': dict(Counter(e['reason'] for e in events)),
        'max_concurrent_positions': max((e['positions'] for e in exposures), default=0),
        'largest_entry_notional': float((trades.entry * trades.shares).max()) if len(trades) else 0.,
        'max_aggregate_entry_notional': max((e['entry_notional'] for e in exposures), default=0.),
        'cost_scenarios': [
            {'cost_multiplier': multiplier,
             'net_pnl_same_fills': float((trades['Gross_PnL'] - multiplier * trades['Costs']).sum())}
            for multiplier in (1., 1.5, 2.)],
        'cost_scenario_limit': 'Accounting sensitivity only: same fills and quantities, no re-sizing or re-simulation.',
    }
