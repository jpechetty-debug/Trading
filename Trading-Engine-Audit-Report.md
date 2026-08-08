# Sovereign AI Trading Engine V7.0/V7.1 — Institutional Code Audit

**Reviewer role:** Principal Quant Engineer / Hedge Fund CTO / Institutional Software Architect / Hackathon Judge
**Scope reviewed:** Full repository (~4,900 lines of Python across `src/`, `tests/`, `research/`, and root-level runners), all 67 unit tests executed, checked-in research artifacts (`wfo_results.csv`, `sensitivity_report.csv`, `walk_forward_results.csv`) inspected as evidence, one external fact (SEBI turnover fee) verified against current published rates.

---

## 1. Executive Verdict

This is a **well-organized, actively-hardened signal-generation and backtesting research framework** — not, as its name claims, a "Trading Engine." There is no code anywhere in the repository that places a real order with a broker; `ZerodhaFeed` and `AngelOneFeed` are unimplemented stubs, and the "live" scripts only ever log signals to CSV/SQLite. As a **research and paper-signal system**, the codebase shows real engineering discipline: point-in-time correctness for correlation and RS calculations, an explicit config-validation gate, a genuinely institutional-grade transaction-cost model, and 67 passing unit tests with good coverage of the position/constraints/execution core.

However, three things should stop anyone from trusting this system's performance claims or flipping it to "live" today:

1. **The walk-forward validation gate that's supposed to prove the strategy isn't overfit is explicitly disabled** (`min_train_expectancy = -999.0`, comment reads `# DEBUG: Accept Everything`), and the checked-in output (`wfo_results.csv`) proves it — every row shows **zero trades** in both train and validation splits, yet all are marked `✅ PASSED`.
2. **The only real backtest evidence in the repo is a losing strategy on a toy sample**: `sensitivity_report.csv` shows **-1.44R expectancy, 18.2% win rate, 11 trades** — identical at every kill-score threshold tested, because the entire dataset is capped at **~59 days of 5-minute bars** (a hard `yfinance` API limit that the code works around rather than solves).
3. **The live signal-logging path is silently broken.** `live_bot.py` reads `res['current_price']`, a key that does not exist on the dict `Scanner.scan_market()` actually returns (`close`) — every qualifying signal throws a `KeyError` that a blanket `except Exception` swallows. In its current state, running the "live bot" for a full session would produce zero rows in `live_signals.csv`.

None of this means the underlying idea (RS-momentum + regime/breadth filtering + dynamic vol-targeted sizing) is bad — it means the system cannot currently *prove* it works, and the one automation path that would act on it doesn't run. Treat this as a strong research scaffold that needs another hardening pass before any of its claims (or its capital) should be trusted.

---

## 2. What's Genuinely Good

- **Point-in-time discipline.** `PortfolioConstraints._compute_pairwise_correlation` explicitly slices `series.loc[:as_of]` before computing correlations, with a comment explaining why — this is exactly the kind of look-ahead-bias bug that quietly inflates backtests, and it's handled correctly.
- **Realistic, itemized transaction-cost model** (`NSECostModel`): brokerage cap, STT (side-aware), GST, stamp duty, SEBI fees, and a *non-linear* impact/slippage model that scales with `quantity/ADV` rather than a flat percentage — this is more sophisticated than most retail backtesters bother with.
- **Deliberate `shares == 0` handling** in `ExecutionModel.generate_orders` — the code explicitly refuses to floor position size to 1 share when the risk budget can't support it, with a comment explaining why that's the correct (not lazy) choice.
- **Config validation on import** (`validate_config()` in `config.py`) — asserts invariants like `0 < BREADTH_THRESHOLD < 1.0` at startup rather than failing silently mid-simulation.
- **Self-documented bug-fix history.** Nine separate `# BUG FIX` / `# FIX V...` comments across `brain_a_v5.py`, `scanner.py`, `live_scanner.py`, `store.py`, and `costs.py` each explain a real prior defect (DataFrame mutation leaking to callers, timeframe-mismatched RS joins, a `.BO` ticker suffix bug, a swallowed exception hiding a `None` crash) and how it was fixed. This is a codebase that has clearly been through real debugging cycles, not just written once and shipped.
- **Test suite is real, not decorative.** 67/67 tests pass (see §7), and they test genuinely tricky things: gap-stop fills at open, trailing-stop activation math, EOD flatten boundary conditions, and correlation-cluster vetoes — not just "does the function return."
- **No security red flags.** No `eval`/`exec`/`pickle`/`subprocess`/shell injection, no string-interpolated SQL (the one SQLite writer uses parameterized queries throughout), secrets loaded via `.env`/`os.getenv` and `.gitignore`d.

---

## 3. Critical / Blocking Issues

### 3.1 The walk-forward "overfitting gate" is switched off, and the shipped output proves it
`research/wfo/run_wfo.py`:
```python
# Acceptance criteria (DEBUG: Accept Everything)
self.min_train_expectancy = -999.0
self.min_val_expectancy = -999.0
self.min_profit_factor = 0.0
self.min_trades = 0
```
With `min_trades = 0`, a configuration that produces **zero trades in both the training and validation windows** trivially satisfies every check and gets written to disk as `✅ PASSED`. That is exactly what's in the committed `research/wfo/results/wfo_results.csv` — all 4 rows show `train_trades=0, val_trades=0, train_exp=0.0, val_exp=0.0, status=✅ PASSED`. A walk-forward report whose every row is an empty backtest is not evidence of robustness; it's evidence the gate never ran a real test. If this file is ever cited as "we validated the strategy out-of-sample," that claim is not currently supportable.

### 3.2 The only populated backtest result in the repo is a loser, on an unreliable sample size
`research/sensitivity_report.csv` (checked-in, presumably the most recent real run):

| Threshold | Total Net R | Trades | Win Rate | Expectancy |
|---|---|---|---|---|
| 5.0 – 6.5 (all four) | **-15.79** | **11** | **18.2%** | **-1.44 R** |

Eleven trades across three tickers is not a statistically meaningful sample in any framework. The root cause is structural: `DataStore.update_ticker` fetches `yf.download(..., period="59d", interval="5m", ...)` — Yahoo Finance's hard ceiling for 5-minute data. Every research script in the repo (`walk_forward_v6.py` literally comments *"We assume ~59 days of history is available in storage"*) is built around this constraint rather than sourcing a real historical intraday dataset. **This is the single most important limitation for a "Hedge Fund CTO" reader to internalize**: nothing in this repository can currently demonstrate a statistically significant edge, because the data window is too short to span even one full market regime, let alone several. The identical results across all four kill-score thresholds also deserves a second look — it's plausible (kill scores are a fairly coarse discrete sum, so no signal may have landed in `[5.0, 6.5)`), but it's the kind of coincidence worth re-running with a print of the actual score distribution before trusting it.

### 3.3 The live signal-logging pipeline is broken — no bug fix comment covers this one
`src/live_bot.py`, line 55:
```python
"price": round(res['current_price'], 2),
```
`res` here comes from `Scanner.scan_market()`, which builds its results from `BrainAV5.analyze_slice()` — and `analyze_slice()`'s return dict has a `close` key, **not** `current_price` (`current_price` only exists on the *other* method, `analyze_ticker()`, which `scan_market()` never calls). Every time the live bot finds a signal scoring ≥ 7.0, this line raises a `KeyError`, which is caught by the surrounding `except Exception as e: print(f"❌ Critical Scan Error: {e}")` in the main loop. The loop keeps running (so it *looks* healthy from the console), but `live_signals.csv` never gets a row written for a qualifying signal. This is the one automation entry point meant to turn detected signals into a persisted, actionable feed, and as shipped it's a silent no-op on the exact condition it exists to handle.

### 3.4 "Trading Engine" is a misnomer — there is no order execution anywhere
`src/data/websocket_feed.py` contains `ZerodhaFeed` and `AngelOneFeed` classes whose `connect()`/`subscribe()` methods are literally `print("... not implemented. Install kiteconnect.")`. There is no code path in this repository that sends an order to a broker. Everything downstream of a "signal" is: log to SQLite, append to a CSV, or render in a Streamlit table. This isn't a defect exactly — plenty of legitimate systems stop at signal generation — but the README's framing ("Sovereign AI Trading Engine," live dashboard "Run Scan" actions) should be explicit that this is a **decision-support / signal-generation system with a backtester**, not an automated execution system, so nobody mistakes "V7.1 Resilience & Safety" for "safe to trade real capital."

### 3.5 A documented risk control is never actually enforced
`PortfolioConstraints.check_daily_loss_limit()` exists, is unit-tested in isolation (`tests/test_constraints.py::test_daily_loss_limit`), and is explicitly named in the module docstring ("Daily loss circuit breaker"). It is **never called** from `PortfolioEngine.run()` — grep confirms the only call site anywhere in the codebase is the test file. The equivalent *drawdown* breaker (`check_system_health`) *is* wired into the main loop and works. The daily-loss breaker looks wired (it's tested, it's documented, it takes the right arguments) but silently does nothing in the actual simulation or live path. This is the most dangerous category of bug in a risk system: not "broken and obviously broken," but "looks enforced, isn't."

---

## 4. High-Severity Correctness & Design Issues

| # | Issue | Where | Impact |
|---|---|---|---|
| 4.1 | `df_15m` ("15-minute context") is computed and resampled on **every single signal evaluation** but is never referenced inside `calculate_kill_score()`'s body — it's only used as an emptiness gate. The README's architecture claims a multi-timeframe signal; the code doesn't act on one. | `brain_a_v5.py: calculate_kill_score` | Wasted compute on every scan/bar; a documented feature (multi-timeframe confirmation) doesn't actually influence the score. |
| 4.2 | `config.TARGET_MULTIPLIER` is stored as `self.target_mult` but the actual target calculation hardcodes the literal `4.0` instead of `self.target_mult`. Changing the config constant silently does nothing to the real target math (it only happens to match today because the default is also `4.0`). | `execution/model.py: generate_orders` | Config drift — a tunable parameter that isn't actually tunable, which will bite the first person who changes it in `config.py` expecting it to take effect (e.g., during a WFO parameter sweep — and indeed, `run_wfo.py` *does* try to inject `atr_target_mult` this exact way via `engine.exec_model.target_mult = params['atr_target_mult']`, which this hardcoding silently defeats). |
| 4.3 | `NSECostModel.sebi_fees = 0.0001` (labeled "CORRECTED... 1 bps"). Current published SEBI turnover charges are **₹10 per crore = 0.0001% = 0.000001 as a decimal fraction** — the code's constant is **~100x too high** (1 bp instead of ~0.01 bp). *Verified against current NSE/broker documentation.* | `execution/costs.py` | Overstates round-trip friction by roughly 0.02% of turnover. This errs conservative (understates returns rather than overstating them), so it's lower-risk than most bugs on this list, but it undercuts the "institutional-grade cost model" claim and should be fixed for accuracy. |
| 4.4 | README documents the RS structural exit as *"RS Score falls below 5th percentile (0.05)"*. The code (`Position._check_structural_exit`: `if rs < 0.05`) is not a percentile at all — it's a fixed threshold in the same percentage-point units as the entry sweet-spot (`RS_SWEET_SPOT_LOW = 0.1`). The threshold is internally consistent with the entry logic (units match, so it's *not* a functional bug), but the documentation describes a statistical concept that isn't implemented, which will mislead the next engineer who tries to "verify" or retune it against an actual percentile. | `position.py` vs `README.md` | Documentation/mental-model mismatch on a risk-relevant exit rule. |
| 4.5 | `Signal` and `TradeRecord` dataclasses in `models.py` are dead code — `grep` finds zero production call sites; only `tests/test_models.py` constructs a `TradeRecord`. The module's docstring says it "replaces raw dict passing with dataclasses," but only `Features` and `Order` are actually wired through the pipeline; the rest of the system still passes plain dicts. | `models.py` | Partial migration creates false confidence about type safety and adds two classes future maintainers may assume are load-bearing. |
| 4.6 | `BrainBV5` (the Gemini commentary layer) is never instantiated anywhere outside its own file — not in `app_v6.py`, not in `scanner.py`, not in any test. The README's Quick Start prominently mentions configuring `GEMINI_API_KEY` for "the BrainB commentary layer," but that layer is currently unreachable dead code with zero test coverage. | `brain_b_v5.py` | A documented, user-facing setup step (get a Gemini key) currently does nothing, because nothing calls the class that would use it. |

---

## 5. Medium / Lower-Severity Findings

- **`RegimeDetector.get_breadth_score` is O(N) per bar with row-by-row `.loc[timestamp]` lookups** across the entire universe dict, called once per timestamp in the main simulation loop. This is fine at NIFTY-50 scale over 59 days; it will not scale to NIFTY-200+ over a multi-year backtest (which the system needs anyway per §3.2). Recommend vectorizing to a precomputed boolean DataFrame (`Close > EMA_50`) and taking a `.mean(axis=1)` per row.
- **`LiveScanner.on_candle_close` builds a reduced signal dict** (`close`, `atr`, `direction` only) before calling `generate_orders()`, discarding `swing_high/low`, `resistance/support`, and — notably — `kill_score`. Since `generate_orders` defaults missing `kill_score` to `6.0`, every live-mock signal is sized at minimum conviction (0.75x) regardless of how strong the actual setup was. This path is explicitly a mock/demo (`MockFeed`), so the blast radius is limited, but it's a second, independent instance of the "reduced dict silently loses information" pattern that caused §3.3 — worth a lint rule or a shared "build execution signal" helper instead of ad-hoc dict construction at each call site.
- **Trailing-stop tightening has a one-bar lag by construction**: `_check_trailing_stop` is called with the bar's `high`/`low` *after* that same bar's stop/target checks, so a stop tightened intrabar can't itself trigger an exit until the following bar. This is a defensible simplification given OHLC-only (no tick) data, but it's an implicit assumption that should be a code comment, not something a reader has to reconstruct from the call order.
- **`research_mode` flag is threaded through `BacktestEngine` → `PortfolioEngine.__init__` → stored as `self.research_mode`, then never read again.** Every research script (`walk_forward_v6.py`, `feature_logger.py`, `sensitivity_analysis.py`) passes `research_mode=True` as if it changes behavior. It doesn't currently do anything.
- **No CI configuration** (no `.github/workflows`, no `.gitlab-ci.yml`) — the 67 tests are excellent but currently only run when a human remembers to.
- **Runtime dependency on `yfinance`**, an unofficial, frequently-breaking scraper of Yahoo's undocumented endpoints, for both the live dashboard and all backtesting. Fine for a hackathon/personal-project prototype; not something to build a funded desk's data pipeline on without a licensed vendor behind it eventually.

---

## 6. Testing & Coverage Assessment

```
67 passed in 14.18s   (README claims "56 active tests" — stale by 11 tests, harmless)
```

**Well covered:** `Position` lifecycle (stops, gaps, trailing, EOD flatten, max-hold, net-R math), `PortfolioConstraints` (correlation clustering, circuit breakers, sector caps), `ExecutionModel` sizing math, `BrainAV5` technical/RS calculations (including the point-in-time alignment fix), `RegimeDetector`, and one end-to-end integration smoke test for `PortfolioEngine`.

**Not covered at all**, which is precisely where every bug in §3–4 lives: `scanner.py`, `live_bot.py`, `live_scanner.py`, `app_v6.py`, `brain_b_v5.py`, `data/store.py`, `data/db.py`, the `BacktestEngine.run()` enrichment path, and the `research/` tooling itself (`run_wfo.py`, `sensitivity_analysis.py`, `walk_forward_v6.py`). The core simulation *logic* is well-tested; the *glue* that turns that logic into a runnable live process or a trustworthy research report is not tested at all, and it's exactly the glue that's broken.

---

## 7. Scorecard

| Dimension | Score (/10) | Rationale |
|---|---|---|
| Architecture & separation of concerns | 8 | Clean layering (data → regime → signal → execution → constraints → portfolio); `RegimeDetector` and `BacktestEngine`-as-thin-wrapper are genuinely good de-duplication decisions. |
| Code quality / readability | 7 | Consistently commented, self-documented bug history is a real asset; undercut by dead parameters (`target_mult`, `research_mode`) and dead classes (`Signal`, `TradeRecord`, `BrainBV5`). |
| Risk-management design (on paper) | 8 | Conviction/vol/DD-scaled sizing, correlation clustering, breadth veto, gap-risk veto, structural exits — a genuinely sophisticated design. |
| Risk-management design (as actually running) | 5 | Daily-loss breaker never fires (§3.5); everything else in this row does run. |
| Backtest rigor / statistical trustworthiness | 3 | 59-day data ceiling, disabled WFO gate, 11-trade sample as the only populated evidence. This is the load-bearing weakness of the whole system. |
| Cost/execution modeling realism | 8 | Best-in-class among hobby/hackathon systems; one verified 100x constant error (§4.3), conservative direction. |
| Test coverage | 6 | Excellent on core logic, zero on live/glue/research code — which is where all the shipped bugs are. |
| Documentation accuracy | 5 | README is well-written and mostly accurate, but overstates the system's live-trading readiness and misdescribes the RS exit as a percentile. |
| Production / "go-live" readiness | **2** | No real broker integration exists; the one signal-persistence path that does exist is broken; a documented circuit breaker doesn't fire. Not safe to point at real capital as shipped. |
| Security | 9 | No injection/eval/exec/pickle risks found; secrets handled correctly. |

**Overall: a strong prototype / research codebase (7/10 as engineering craftsmanship) that is not currently a validated or safe trading system (2–3/10 as a "system you'd fund").** The gap between those two numbers is the headline finding of this audit.

---

## 8. Recommended Fix Priority (before this touches real capital or is cited as evidence of edge)

1. **Fix the WFO acceptance criteria** — replace the `-999`/`0` "accept everything" thresholds with real minimums, and re-run before trusting any parameter-sweep output.
2. **Source real historical intraday data** (a paid vendor, or at minimum bulk-download and archive daily so the 60-day yfinance window compounds over time into a real multi-regime dataset) before drawing any conclusion about expectancy.
3. **Fix `live_bot.py`'s `current_price` → `close` key mismatch** and add a test that actually exercises `Scanner.scan_market()` end-to-end with a mocked `yf.download`, so this class of bug can't reach production silently again.
4. **Wire `check_daily_loss_limit` into `PortfolioEngine.run()`**, or remove it and update the docstring — a risk control that's tested-but-unwired is worse than no risk control, because it creates false confidence.
5. **Either implement one real broker adapter or rename/re-scope the project** so "Trading Engine" doesn't overpromise to anyone evaluating it (including future you) on what actually executes.
6. Quick, low-risk cleanups: wire `self.target_mult` into the actual target calc, fix the SEBI fee constant, delete or genuinely use `Signal`/`TradeRecord`/`BrainBV5`/`research_mode`, and correct the README's RS-exit description.

---

*This audit reflects a point-in-time read of the uploaded archive; it did not modify any source files.*
