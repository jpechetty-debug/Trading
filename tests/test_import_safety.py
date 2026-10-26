import runpy
from pathlib import Path

import pytest


@pytest.mark.parametrize('script', ['tests/test_yf_st.py', 'test_yf_download.py', 'run_local_backtest.py',
                                  'scripts/audit_local_data.py'])
def test_manual_tools_have_no_network_calls_on_import(monkeypatch, script):
    calls = []
    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError('Import attempted network I/O')
    monkeypatch.setattr('yfinance.download', forbidden)
    monkeypatch.setattr('yfinance.Ticker', forbidden)
    runpy.run_path(str(Path(__file__).resolve().parents[1] / script))
    assert not calls  # Also detects calls whose exceptions a script swallowed.
