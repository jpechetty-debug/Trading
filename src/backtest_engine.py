"""
BacktestEngine V6.5 — Thin wrapper around PortfolioEngine for single-ticker runs.

Maintains the `run(ticker)` API used by walk_forward_v6.py and other research scripts,
but delegates all simulation logic to PortfolioEngine to avoid code duplication.
"""
import pandas as pd

from src.logger import get_logger
from src.backtest.portfolio_engine import PortfolioEngine

log = get_logger(__name__)


class BacktestEngine:
    """
    Single-ticker backtest wrapper.
    
    Delegates to PortfolioEngine internally so there is one canonical
    simulation loop, but exposes the simple `run(ticker)` interface
    expected by research scripts.
    """

    def __init__(self, start_date="2024-01-01", kill_threshold=6.0, research_mode=False):
        self.start_date = start_date
        self.kill_threshold = kill_threshold
        self.research_mode = research_mode
        self.portfolio_engine = None
        # We initialize brain and exec_model here so research scripts can modify them
        # However, they belong to PortfolioEngine. The best way is to instantiate a dummy
        # or instantiate PortfolioEngine early. We'll instantiate PortfolioEngine without tickers
        # and set them later, or just instantiate it here and override tickers in run().
        self.portfolio_engine = PortfolioEngine(
            tickers=[],
            start_date=self.start_date,
            kill_threshold=self.kill_threshold,
            research_mode=self.research_mode,
        )
        self.brain = self.portfolio_engine.brain
        self.exec_model = self.portfolio_engine.exec_model

    def run(self, ticker: str) -> pd.DataFrame:
        """
        Run a single-ticker backtest by delegating to PortfolioEngine.
        
        Returns a DataFrame of closed trades with Net_R and feature data,
        backward-compatible with walk_forward_v6.py output format.
        """
        log.info("Single-Ticker Backtest for %s (via PortfolioEngine)...", ticker)

        self.portfolio_engine.tickers = [ticker]
        results_df = self.portfolio_engine.run()

        # PortfolioEngine returns {trade_id, ticker, direction, Net_R, PnL, Reason}
        # Walk-forward expects {trade_id, ticker, direction, entry_time, exit_time, exit_reason, Net_R, + features}
        # Enrich from closed trades for backward compatibility
        if results_df.empty:
            return results_df

        enriched = []
        for pos in self.portfolio_engine.portfolio.closed_trades:
            enriched.append({
                "trade_id": pos.trade_id,
                "ticker": pos.ticker,
                "direction": pos.direction,
                "entry_time": pos.entry_time,
                "exit_time": pos.exit_time,
                "exit_reason": pos.exit_reason,
                "Net_R": pos.calculate_net_r(engine.cost_model),
                **pos.features,
            })

        log.info("Single-Ticker Complete. Closed Trades: %d", len(enriched))
        return pd.DataFrame(enriched)
