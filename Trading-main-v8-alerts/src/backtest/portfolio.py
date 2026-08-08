from src.backtest.position import Position

class Portfolio:
    def __init__(self):
        self.open_positions = [] # List of Position objects
        self.closed_trades = []
    
    def is_invested(self, ticker):
        """Check if we already have an open position in this ticker."""
        return any(pos.ticker == ticker for pos in self.open_positions)

    def open_trade(self, trade_id, ticker, direction, entry, stop, target, r_unit, shares, timestamp, features=None):
        pos = Position(trade_id, ticker, direction, entry, stop, target, r_unit, shares, features)
        pos.entry_time = timestamp
        self.open_positions.append(pos)

    def update_ticker_bar(self, ticker, bar, timestamp):
        """
        V7.0: Pass the full bar for structural exit checks.
        """
        for pos in self.open_positions:
            if pos.ticker == ticker and pos.open:
                pos.update(bar, timestamp)

    def cleanup_positions(self):
        """
        Moves closed trades to the archive.
        """
        still_open = []
        for pos in self.open_positions:
            if pos.open:
                still_open.append(pos)
            else:
                self.closed_trades.append(pos)
        self.open_positions = still_open

    def update_bar(self, bar, timestamp):
        """
        LEGACY: Updates all open positions assuming they share the same price data.
        """
        for pos in self.open_positions:
            pos.update(bar, timestamp)
        self.cleanup_positions()
