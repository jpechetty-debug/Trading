"""
NOTE: This module is retained for historical reference only.

This was the entry point for the V4.1 prototype and imported modules
(`brain_a`, `brain_b`, `project_types`, `validator`, `metrics`) that no
longer exist under those names — they were superseded by the V7.0
architecture (`brain_a_v5.py`, `brain_b_v5.py`, etc.) documented in the
project README. Running this file directly will raise ModuleNotFoundError.

Current entry points:
  - Live dashboard:      streamlit run src/app_v6.py
  - Backtest / research:  python run_certification.py
  - Portfolio simulation: python run_portfolio_sim.py
  - Test suite:           python -m pytest tests/ -v
"""

if __name__ == "__main__":
    print(__doc__)
