"""
BacktestEngine V6.5 — Thin wrapper around PortfolioEngine for single-ticker runs.

Maintains the `run(ticker)` API used by walk_forward_v6.py and other research scripts,
but delegates all simulation logic to PortfolioEngine to avoid code duplication.
"""
import pandas as pd

from src.logger import get_logger
from src.backtest.portfolio_engine import PortfolioEngine
from src.brain_a_v5 import BrainAV5
from src.execution.model import ExecutionModel

log = get_logger(__name__)


class BacktestEngine:
    """
    Single-ticker backtest wrapper.
    
    Delegates to PortfolioEngine internally so there is one canonical
    simulation loop, but exposes the simple `run(ticker)` interface
    expected by research scripts.
    """

    def __init__(self, start_date="2024-01-01", kill_threshold=6.0):
        self.start_date = start_date
        self.kill_threshold = kill_threshold

        # Long-lived brain/exec_model instances so research scripts (e.g.
        # research/wfo/run_wfo.py, which does
        # `engine.brain.rs_lookback = ...` / `engine.exec_model.stop_mult = ...`
        # once and then calls run(ticker) across a whole universe) can tune
        # parameters once and have them apply to every subsequent run().
        self.brain = BrainAV5()
        self.exec_model = ExecutionModel()

    def run(self, ticker: str) -> pd.DataFrame:
        """
        Run a single-ticker backtest by delegating to PortfolioEngine.
        
        Returns a DataFrame of closed trades with Net_R and feature data,
        backward-compatible with walk_forward_v6.py output format.
        """
        log.info("Single-Ticker Backtest for %s (via PortfolioEngine)...", ticker)

        # BUG FIX: this used to reuse a single PortfolioEngine instance
        # (created once in __init__) across every run(ticker) call, just
        # swapping `.tickers`. PortfolioEngine.run() never resets equity,
        # the Portfolio's closed_trades list, equity_history, or
        # peak_equity between calls — so the 2nd+ ticker's backtest
        # silently started from the 1st ticker's ending equity/drawdown
        # state, and report() mixed closed trades from every prior ticker
        # into one DataFrame. Verified directly: a synthetic two-ticker
        # run left ticker A's loss bleeding into ticker B's reported
        # results, with ticker B's own trades computed against ticker A's
        # leftover equity instead of a clean initial_equity.
        #
        # Fix: build a brand-new PortfolioEngine for every call so no
        # state can leak between tickers, but hand it this wrapper's
        # long-lived brain/exec_model so parameter tuning from research
        # scripts still applies.
        portfolio_engine = PortfolioEngine(
            tickers=[ticker],
            start_date=self.start_date,
            kill_threshold=self.kill_threshold,
            
        )
        portfolio_engine.brain = self.brain
        portfolio_engine.exec_model = self.exec_model

        results_df = portfolio_engine.run()

        # PortfolioEngine returns {trade_id, ticker, direction, Net_R, PnL, Reason}
        # Walk-forward expects {trade_id, ticker, direction, entry_time, exit_time, exit_reason, Net_R, + features}
        # Enrich from closed trades for backward compatibility
        if results_df.empty:
            return results_df

        enriched = []
        for pos in portfolio_engine.portfolio.closed_trades:
            enriched.append({
                "trade_id": pos.trade_id,
                "ticker": pos.ticker,
                "direction": pos.direction,
                "entry_time": pos.entry_time,
                "exit_time": pos.exit_time,
                "exit_reason": pos.exit_reason,
                # BUG FIX: this was `engine.cost_model`, but `engine` was
                # never defined anywhere in this method (leftover from an
                # earlier version of this function) — guaranteed
                # NameError on any call that closed at least one trade.
                "Net_R": pos.calculate_net_r(portfolio_engine.cost_model),
                **pos.features,
            })

        log.info("Single-Ticker Complete. Closed Trades: %d", len(enriched))
        return pd.DataFrame(enriched)
