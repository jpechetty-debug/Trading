import pandas as pd
import pytest

from src.backtest.constraints import PortfolioConstraints
from src.backtest.portfolio import Portfolio
from src.data.universe import NIFTY_200


def test_configured_universe_sector_coverage_has_only_documented_exceptions():
    coverage = PortfolioConstraints().sector_coverage(NIFTY_200)
    assert coverage['requested'] == 201
    assert coverage['mapped'] == 199
    assert coverage['unmapped'] == ['DUMMYHDLVR.NS', 'LTIM.NS']


def test_five_distinct_classified_sectors_are_not_pooled_as_other():
    constraints = PortfolioConstraints()
    portfolio = Portfolio()
    tickers = ['ABB.NS', 'ACC.NS', 'CIPLA.NS', 'TCS.NS', 'HDFCBANK.NS']
    assert len({constraints.sector_map[t] for t in tickers}) == 5
    for n, ticker in enumerate(tickers):
        order = dict(entry=100., stop=99., shares=100)
        allowed, reason = constraints.can_open_trade(portfolio, ticker, 100_000., order=order)
        assert allowed, reason
        portfolio.open_trade(str(n), ticker, 'LONG', 100., 99., 104., 1., 100,
                             pd.Timestamp('2024-01-02 10:00'))
    assert len(portfolio.open_positions) == 5


def test_real_sector_cap_still_blocks_third_financial_position():
    constraints = PortfolioConstraints()
    portfolio = Portfolio()
    for ticker in ['HDFCBANK.NS', 'ICICIBANK.NS']:
        portfolio.open_trade(ticker, ticker, 'LONG', 100., 99., 104., 1., 100,
                             pd.Timestamp('2024-01-02 10:00'))
    allowed, reason = constraints.can_open_trade(portfolio, 'SBIN.NS', 100_000,
                                                order=dict(entry=100., stop=99., shares=100))
    assert not allowed
    assert reason == 'SECTOR_FULL_(Financial Services)'


@pytest.mark.parametrize('csv', ['Symbol,Sector\nA.NS,\n',
                               'Symbol,Sector\nA.NS,IT\nA.NS,BANK\n', 'Symbol,Wrong\nA.NS,IT\n'])
def test_malformed_sector_file_fails_visibly(tmp_path, csv):
    path = tmp_path / 'sectors.csv'
    path.write_text(csv)
    with pytest.raises(ValueError, match='Sector map'):
        PortfolioConstraints(sector_file=path)
