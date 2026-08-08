"""
test_constraints.py — Portfolio constraint tests including
sector limits, correlation filter, circuit breakers.
"""
import pytest
from src.backtest.constraints import PortfolioConstraints
from src.backtest.position import Position
from src.backtest.portfolio import Portfolio
from datetime import datetime


class TestCorrelationFilter:
    """V6.5: Return-based correlation filter with sector fallback."""

    def test_same_sector_high_correlation(self):
        c = PortfolioConstraints()
        corr = c._sector_correlation_proxy("HDFCBANK.NS", "ICICIBANK.NS")
        assert corr >= 0.75

    def test_different_sector_low_correlation(self):
        c = PortfolioConstraints()
        corr = c._sector_correlation_proxy("HDFCBANK.NS", "TCS.NS")
        assert corr < 0.75

    def test_cluster_breach_blocks_trade(self):
        c = PortfolioConstraints()
        positions = []
        # Create 2 BANK positions
        for i, ticker in enumerate(["HDFCBANK.NS", "ICICIBANK.NS"]):
            pos = Position(f"T{i}", ticker, "LONG", 100, 90, 120, 5, 100)
            pos.entry_time = datetime(2024, 1, 1, 10, 0)
            positions.append(pos)
        # Third BANK position should be blocked
        ok, reason = c.check_correlation_cluster("SBIN.NS", positions, 100000)
        assert not ok
        assert "CLUSTER" in reason


class TestCircuitBreaker:
    """System health and daily loss circuit breakers."""

    def test_drawdown_breaker(self):
        c = PortfolioConstraints()
        c.peak_equity = 100000
        ok, reason = c.check_system_health(84000)  # -16% > 15% limit
        assert not ok
        assert "DRAWDOWN" in reason

    def test_healthy_within_limits(self):
        c = PortfolioConstraints()
        c.peak_equity = 100000
        ok, _ = c.check_system_health(95000)  # -5% < 15% limit
        assert ok

    def test_daily_loss_limit(self):
        c = PortfolioConstraints()
        ok, _ = c.check_daily_loss_limit(78000, 100000)  # -22k > 2*10k
        assert not ok


class TestPositionLimits:
    """Max positions and sector exposure caps."""

    def test_max_positions_reached(self):
        c = PortfolioConstraints()
        portfolio = Portfolio()
        for i in range(5):
            portfolio.open_trade(f"T{i}", f"STOCK{i}.NS", "LONG",
                                 100, 90, 120, 5, 100, datetime(2024, 1, 1, 10, 0))
        ok, reason = c.can_open_trade(portfolio, "NEWSTOCK.NS", 100000)
        assert not ok
        assert reason == "MAX_POSITIONS"
