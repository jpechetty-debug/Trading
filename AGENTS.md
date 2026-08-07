# AGENTS.md — Navigation & Execution Map

## Project map
```
src/
  config.py              # All tunable params + validate_config() — read this first
  regime.py               # RegimeDetector: Nifty regime + breadth score (35% floor)
  brain_a_v5.py            # Kill Score generation (6.0–10.0 conviction scoring)
  brain_b_v5.py            # AI commentary layer (needs GEMINI_API_KEY, optional)
  scanner.py / live_scanner.py   # Universe scanning
  live_bot.py               # 5-min polling live bot → notifications.py
  notifications.py           # Telegram/Email alerting, fault-tolerant
  app_v6.py                  # Streamlit dashboard (entry point for manual use)
  execution/
    model.py                 # calculate_dynamic_risk() — conviction/DD/vol scalars
    costs.py                  # Slippage, STT, brokerage cost model
  backtest/
    portfolio_engine.py        # PortfolioEngine — top-level simulation driver
    portfolio.py                 # Portfolio/position bookkeeping
    position.py                   # Structural exit logic (RS/EMA/ATR)
    constraints.py                 # Trade approval/veto gates
  data/
    store.py                  # DataStore — yfinance fetch → Parquet cache (LIVE NETWORK)
    universe.py / db.py         # Universe + local db helpers
  utils/market_time.py          # IST market hours, EOD flatten logic

data/midcap_universe.py     # Ticker universe definitions
tests/                       # 86 tests, fully offline (fixtures, no live data)
scripts/                     # Dev utilities (check_env.py, check_models.py, verify_project.py)
run_phase3_simulation.py    # Full sim — HITS LIVE YAHOO FINANCE
run_v7_simulation.py         # Full sim — HITS LIVE YAHOO FINANCE
run_certification.py          # Backtest tearsheet, 3 benchmark tickers
run_portfolio_sim.py            # Full-universe portfolio simulation
```

## Execution flow
```
DataStore (Parquet cache)
   → RegimeDetector (breadth %, Nifty regime; veto longs if breadth < 35%)
      → BrainA V5 (Kill Score 6.0–10.0 per ticker slice)
         → ExecutionModel.calculate_dynamic_risk()
             risk = base_risk × conviction_scalar(kill_score)
                         × dd_scalar(current_dd, MAX_DD_THRESHOLD)
                         × vol_scalar(portfolio_vol, TARGET_PORTFOLIO_VOL)
            → PortfolioConstraints (approve/deny: liquidity, spread, gap veto)
               → PortfolioEngine (execute, track open positions)
                  → position.py structural exits (RS deterioration / 20-EMA break / ATR collapse)
                     → CostModel → Net R → BrainB commentary (optional)
```

## Key parameter registry (src/config.py — current values)
| Parameter | Value | Meaning |
|---|---|---|
| `SYSTEM_VERSION` | 8.0 | System version release |
| `RISK_PER_TRADE` | ₹10,000 | Base risk per trade before scalars |
| `TARGET_PORTFOLIO_VOL` | 0.12 | 12% annualized vol target |
| `BREADTH_THRESHOLD` | 0.35 | Veto all longs below this breadth |
| `MAX_DD_THRESHOLD` | 0.15 | Drawdown scaling ceiling |
| `CONVICTION_MIN_MULT` / `MAX_MULT` | 0.75 / 1.25 | Sizing band from Kill Score |
| `KILL_SCORE_THRESHOLD` | 6.0 | Min conviction score to trade |
| `GAP_VETO_MULT` | 1.5× ATR | Overnight gap veto |
| `STOP_LOSS_MULTIPLIER` / `TARGET_MULTIPLIER` | 1.5 / 4.0 | ATR-based stop/target |
| `MAX_HOLD_BARS` | 50 | ~4hrs at 5min candles, forces stale-position exit |
| `EOD_FLATTEN_HOUR:MINUTE` | 15:15 IST | Forced flatten before close |

## Verification workflow
1. `python -m pytest tests/ -v` — must be 86/86 passing, offline.
2. `python scripts/verify_project.py` — syntax + config + tests + import-safety check on run scripts.
3. Only run `python run_phase3_simulation.py` / `run_v7_simulation.py` directly (outside CI) when you
   want a real network-backed simulation — they are NOT part of the automated gate.
