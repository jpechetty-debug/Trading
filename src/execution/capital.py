"""Shared unlevered entry-capital allocation for simulation and paper fills."""


def affordable_shares(order, *, equity, realized_equity, positions, cost_model,
                      direction, average_volume=0, position_cap=1.0):
    invested = sum(p.shares * p.entry for p in positions)
    available = max(0.0, min(realized_equity, equity) - invested)
    available = min(available, equity * position_cap)
    entry = order['entry']
    lo, hi = 0, min(order['shares'], int(available // entry))
    while lo < hi:
        quantity = (lo + hi + 1) // 2
        fees = cost_model.round_trip_cost(entry, entry, quantity,
                                         adv=average_volume, direction=direction)
        if entry * quantity + fees <= available:
            lo = quantity
        else:
            hi = quantity - 1
    return lo
