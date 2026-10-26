import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src import config
from src.backtest.constraints import PortfolioConstraints
from src.backtest.portfolio import Portfolio
from src.execution.model import ExecutionModel
from src.regime import RegimeDetector


@pytest.mark.parametrize('capital,limit', [(100_000, 2000), (2_000_000, 20_000)])
def test_daily_loss_budget_matches_equity_capped_trade_budget(capital, limit):
    constraints = PortfolioConstraints()
    assert constraints.check_daily_loss_limit(capital - limit + 1, capital)[0]
    assert not constraints.check_daily_loss_limit(capital - limit, capital)[0]


@pytest.mark.parametrize('name,value', [
    ('RISK_PER_TRADE', float('inf')), ('TARGET_PORTFOLIO_VOL', float('nan')),
    ('STOP_LOSS_MULTIPLIER', 0), ('KILL_SCORE_THRESHOLD', -1), ('KILL_SCORE_THRESHOLD', 10),
    ('MAX_TRADE_RISK_PCT', 0.9), ('MAX_PORTFOLIO_RISK_PCT', 0),
    ('MAX_HOLD_BARS', 2.5), ('RS_LOOKBACK', True), ('CANDLE_INTERVAL_SECONDS', 60),
    ('EOD_FLATTEN_MINUTE', 99), ('TRAILING_STOP_LOCK_R', 3), ('MIN_BREADTH_COVERAGE', 0),
])
def test_invalid_config_fails_explicitly(monkeypatch, name, value):
    monkeypatch.setattr(config, name, value)
    with pytest.raises(ValueError):
        config.validate_config()


def test_config_validation_still_runs_under_optimized_python():
    result = subprocess.run([sys.executable, '-O', '-c',
                             'import src.config as c; c.RISK_PER_TRADE=0; c.validate_config()'],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'ValueError' in result.stderr


def test_current_config_is_read_when_model_is_constructed(monkeypatch):
    monkeypatch.setattr(config, 'RISK_PER_TRADE', 123)
    monkeypatch.setattr(config, 'STOP_LOSS_MULTIPLIER', 2.5)
    model = ExecutionModel()
    assert model.base_risk_inr == 123
    assert model.stop_mult == 2.5


def test_actual_equity_caps_dynamic_position_risk():
    model = ExecutionModel(base_risk_inr=10_000)
    order = model.generate_orders({'close': 100., 'atr': 2., 'kill_score': 10.,
                                  'direction': 'LONG', 'resistance': 120.}, equity=25_000)
    assert order['valid_rr']
    assert (order['entry'] - order['stop']) * order['shares'] <= 250


def test_gap_through_stop_or_target_cannot_create_a_repositioned_order():
    model = ExecutionModel()
    signal = {'close': 100., 'atr': 2., 'kill_score': 8., 'direction': 'LONG', 'resistance': 120.}
    for opening in (90, 120):
        order = model.generate_orders(signal, entry_price=opening, equity=100_000)
        assert not order['valid_rr']


@pytest.mark.parametrize('field,value', [('close', None), ('direction', []), ('atr', 'bad'),
                                        ('resistance', float('nan'))])
def test_malformed_signal_is_rejected_without_throwing(field, value):
    signal = {'close': 100., 'atr': 2., 'direction': 'LONG', field: value}
    assert not ExecutionModel().generate_orders(signal)['valid_rr']


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -1., 0.])
def test_invalid_equity_never_passes_risk_gates(value):
    constraints = PortfolioConstraints()
    assert not constraints.check_system_health(value)[0]
    assert not constraints.can_open_trade(Portfolio(), 'A.NS', value)[0]


@pytest.mark.parametrize('value', [float('nan'), float('inf')])
def test_invalid_regime_input_disables_both_directions(value):
    state = RegimeDetector().detect(value, 100., 100.)
    assert state['regime'] == 'Neutral'
    assert not state['longs_authorized'] and not state['shorts_authorized']


def test_aggregate_risk_uses_position_size_not_just_count():
    constraints = PortfolioConstraints()
    portfolio = Portfolio()
    portfolio.open_trade('A', 'A.NS', 'LONG', 100, 50, 200, 50, 90, pd.Timestamp('2024-01-01'))
    # One existing position risks 4,500, plus 900 would exceed 5% of 100,000.
    allowed, reason = constraints.can_open_trade(portfolio, 'B.NS', 100_000,
                                                 order={'entry': 100, 'stop': 91, 'shares': 100})
    assert not allowed and reason == 'PORTFOLIO_RISK_CAP'


def test_sector_limit_uses_actual_initial_risk():
    constraints = PortfolioConstraints()
    portfolio = Portfolio()
    portfolio.open_trade('A', 'A.NS', 'LONG', 100, 90, 130, 10, 150, pd.Timestamp('2024-01-01'))
    allowed, reason = constraints.can_open_trade(portfolio, 'B.NS', 100_000,
                                                 order={'entry': 100, 'stop': 91, 'shares': 100})
    assert not allowed and reason == 'SECTOR_RISK_CAP'


def test_risk_cap_default_follows_configuration(monkeypatch):
    monkeypatch.setattr(config, 'MAX_DD_THRESHOLD', 0.1)
    constraints = PortfolioConstraints()
    assert not constraints.check_system_health(89_000)[0]


def test_correlation_cache_is_bounded_and_recomputes_earlier_dates():
    constraints = PortfolioConstraints()
    index = pd.date_range('2024-01-01', periods=70, freq='5min')
    rng = np.random.default_rng(12)
    a = 100 * np.cumprod(1 + rng.normal(0, .001, len(index)))
    b = a.copy()
    b[40:] = 100 * np.cumprod(1 + rng.normal(0, .001, len(index)-40))
    constraints.update_price_data({'A': pd.DataFrame({'Close': a}, index=index),
                                   'B': pd.DataFrame({'Close': b}, index=index)})
    early = constraints._compute_pairwise_correlation('A', 'B', index[30])
    for time in index[31:]:
        constraints._compute_pairwise_correlation('A', 'B', time)
    assert len(constraints._correlation_cache) == 1
    assert constraints._compute_pairwise_correlation('A', 'B', index[30]) == pytest.approx(early)
    assert early == pytest.approx(1)


def test_walk_forward_enforces_inclusive_last_day_at_simulation_boundary(monkeypatch):
    import research.wfo.run_wfo as module
    from unittest.mock import Mock
    fake = Mock()
    fake.run.return_value = pd.DataFrame({'Net_R': [1.], 'exit_time': [pd.Timestamp('2024-01-10 15:00')]})
    factory = Mock(return_value=fake)
    monkeypatch.setattr(module, 'BacktestEngine', factory)
    result = module.WFOEngine().run_backtest_with_params('A.NS',
        {'rs_lookback': 12, 'atr_stop_mult': 1.5, 'atr_target_mult': 4, 'kill_threshold': 6},
        {'start': '2024-01-01', 'end': '2024-01-10'})
    assert factory.call_args.kwargs['end_date'] == pd.Timestamp('2024-01-11')
    assert result['total_trades'] == 1
