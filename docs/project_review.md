# Project review — updated 26 October 2026

The project has a useful separation between market data, deterministic scoring,
execution sizing, portfolio controls, and optional AI commentary. Its main
weakness was that passing component tests did not establish that the full
simulation generated signals or accounted for risk correctly. This review
prioritized those behaviors before any parameter optimization.

## Findings and implemented improvements

| Finding | Change and validation |
| --- | --- |
| The engine supplied 151 precomputed bars to analysis, which recalculated a 200-bar EMA and discarded the entire slice. | Explicitly reuse prepared indicators. A regression test requires a real signal from a 151-bar prepared window, rather than accepting `None`. Prefix/full-history comparison verifies indicator causality. |
| Loss gates used incomplete accounting; open PnL was absent from equity. | Mark opening quotes and settle opening exits before entry; then process intrabar exits and mark close equity before generating signals. Include estimated liquidation costs and test realized/unrealized losses. |
| The constraint peak started at ₹100,000 regardless of requested capital. | Seed each run from its actual starting equity; test ₹25,000 and ₹250,000 accounts. Trigger the breaker at the configured boundary. |
| Direct repeated runs retained portfolio state. | Reset accounting, price/correlation/breadth caches, and history while preserving tuned strategy objects and configured limits. Repeated results must match. |
| A long signal could enter while only short scanning was authorized. | Check the actual signal direction against its own authorization flag. |
| Entries could reopen at/after the 15:15 flatten cutoff, outside market hours, or on weekends. | Gate new entries to weekdays 09:15–15:14 IST. Convert aware timestamps to IST. Existing positions still receive updates. |
| Remaining positions were omitted from final PnL and could survive a circuit breaker. | Liquidate at the last observed price on data exhaustion or a breaker; assert final equity equals initial equity plus reported net PnL. These are modeled closeout fills. |
| Five-minute equity changes were annualized as daily returns. | Compute volatility from session-level equity, with the configured target fallback until 20 daily returns exist. |
| Engine trailing thresholds used ATR even when the actual stop distance differed. | Supply initial entry-to-stop distance as the engine's R unit. |
| Time exits used a bar midpoint, and short costs assigned BUY/SELL to the wrong legs. | Use the cutoff opening quote for engine EOD exits, observed closes for max-hold exits, and apply each cost leg to its actual side. Fee rates themselves were not revised. |
| Absolute reward distance could approve a target on the wrong side of entry. | Validate positive finite inputs, directions, and directional reward/risk. Size against rounded execution prices. |
| Data loading could silently download when a local file was missing, mutate shared cached frames, or accept incompatible data. | Add explicit offline mode, read-only external search roots, independent returned frames, synchronized LRU caching, atomic cache updates, and shared OHLCV validation. |
| A Streamlit demo fetched Yahoo data during test collection. | Guard manual tools with `main()` and test import safety. Block live sockets during automated tests; keep explicit live runners separate. |
| Windows cp1252 console streams emitted logging tracebacks for emoji and the rupee symbol. | Escape unrepresentable characters for the actual stream encoding; retain Unicode on UTF-8 streams. Tests cover both encodings. |
| The regime breadth threshold duplicated a literal instead of using configuration. | Read `config.BREADTH_THRESHOLD`. |

## Other-folder data assessment

Inspected local datasets and metadata under `scanner-nifty500`,
`Finding Multibagger NSE`, `Multibagger-claude`, and `tori-scanner`. SQLite
metadata was opened read-only; opaque cache blobs were not deserialized.

The audited `tori-scanner` snapshot contains 20 daily OHLC CSVs, typically from
7 October 2021 through 23 October 2026. All 20 lack Volume, and the observed
median interval is one day. They cannot validate the five-minute strategy's
intraday volume, gap, EOD, and holding-period behavior. The snapshot's source
README also states that adjustment provenance and historical publication
vintages are not certified. Its present-day selected cohort would introduce
selection bias in historical strategy research.

`research/local_data_audit.json` records individual paths, SHA-256 hashes,
coverage, row counts, intervals, and rejection reasons. The new audit CLI can
evaluate other folders without changing them. The new local backtest CLI can
consume compatible five-minute OHLCV files and an aligned Nifty benchmark
without contacting Yahoo. No historical bars, volumes, or performance results
were fabricated from the daily snapshot. No strategy thresholds were tuned.

## Second-pass hardening

| Finding | Resolution and regression evidence |
| --- | --- |
| A completed candle could trade at its own close; intrabar proceeds could fund an earlier order. | Queue signals and execute only at the next contiguous open, before intrabar exits. Reprice risk/reward and shares at that open. Tests prohibit later profits from financing an opening fill. |
| Signal orders could survive missing bars or a session boundary. | Expire them; require the immediately following five-minute execution candle and valid session. Record rejection reasons. |
| Opening target gaps were treated as if a later stop happened first. | Handle known opening gaps before the unknown intrabar path; closing twice cannot rewrite the first exit. |
| Sizing and concentration controls used nominal budgets/count proxies. | Cap per-trade and aggregate initial stop-distance risk at 1%/5% of equity by default; check actual sector/correlation risk and reserve modeled fees as well as notional cash. Retain position-count limits. |
| Daily loss recovery could reactivate entries; its budget ignored equity-capped sizing. | Latch the halt for the session, reset at the next day, and use twice the smaller of the base budget and start-of-day equity-capped budget. |
| Walk-forward simulation ran beyond the requested window and filtered trades afterwards. | Enforce an exclusive end before indicator calculation, close remaining positions inside the window, and remove posthoc filtering. The WFO date API converts inclusive end dates to the next midnight. Future-price perturbation leaves in-window results unchanged. |
| Scanner paths could use forming/stale candles or insufficient breadth coverage. | Validate aligned, completed, fresh OHLCV snapshots; require 80% universe coverage; apply intraday regime/breadth and actual direction permission in both scanners. Keep zero-volume index prices usable. |
| Fifteen-minute confirmation included partial/gapped groups. | Require three completed five-minute candles for each confirmation group. Tests change excluded partial candles without changing the confirmed result. |
| Streaming callbacks could lose/duplicate bars, accept late ticks, or leak tasks after failures. | Commit candle state before callbacks, reject out-of-order/invalid ticks, bound history, retry symbols once breadth/context is available, and disconnect/cancel tasks on connect, subscription, or stream failures. |
| Mock signals could reach persistence/alerts; real alerts omitted order fields. | Explicit simulated-feed isolation, opt-in alerts for real feeds, full order payloads, and per-bar symbol deduplication. |
| Configuration assertions disappeared under optimized Python and accepted NaN. | Explicit finite/type/range validation, with a subprocess test under `python -O`. Constructor defaults read current configuration. |
| Reports lacked execution timestamps/rejection history. | Export signal/entry/exit and quote timestamps, direction, quantities, initial stop, net PnL, and engine decision events. Exercise the real local-file CLI with nonzero trades and PnL reconciliation. |
| Shared host dependencies did not meet the project requirements. | Align the explicit pandas floor with pandas-ta, install requirements in the project `.venv`, and add `pip check` to CI. Use the isolated interpreter for release verification. |

## Verification

### Capital, diagnostics, paper ledger, and database follow-up

The per-position entry-capital ceiling is now 20% of equity by default, including
the estimated round-trip fee reserve. It can be varied per offline run, including
1.0 for comparison with previous cash-only sizing. Both longs and shorts remain
unlevered. Position-count, sector, correlation, and signal gates still apply;
the cap creates room for diversification but does not force five positions.

Reports now separate gross PnL, modeled costs, net PnL, and initial stop risk;
summaries group results by exit reason and direction, report cost per R and
holding bars, capital-resizing decisions, largest position, and maximum open
positions. Fixed-fill 1.5x/2x cost scenarios are explicitly labelled accounting
sensitivity. Structural-exit and short-scoring rules remain unchanged.

The new offline paper account persists simulated entries, marks, exits, account
state, and loss halts. Event IDs are idempotent; conflicting reuse is rejected.
SQLite commits the event and state checkpoint atomically. Tests cover restarts,
concurrent duplicate events, checkpoint rollback, stale/missing marks, short
PnL, configuration changes, and persistent loss halts. A real BrainA signal,
including pandas/numpy values, is accepted by the ledger. Replaying the three
synthetic CLI example events twice leaves exactly three events and one closed
position lifecycle. See `docs/paper_account.md` for the deliberately limited
execution contract; this is not an automated paper strategy or broker gateway.

**Correction to the data assessment:** the earlier statement about daily bars
without volume applies to the 20-file tori-scanner snapshot, not all local
databases. Read-only queries of Finding Multibagger NSE found:

- `lite.db/prices`: 617,654 daily OHLCV rows, 635 symbols, 2021-10-07 through
  2026-10-23; volume is non-null in all rows (not a complete value-quality audit).
- `runtime/fyers-data.db/candles`: 152,894 daily records across 638 symbols.
- Additional archived daily stores, including 1,001,552 rows in the stocks
  history store; these overlap other sources and must not be summed as unique data.
- Daily Nifty benchmark closes and a hash-verified saved 2026 NSE CM holiday
  response. The calendar snapshot is retained as evidence but not activated as
  a complete holiday/special-session calendar.

Date fields in the inspected OHLCV stores are date-only. The generic opaque
cache is empty. No five-minute stock/benchmark dataset was identified in the
inspected production stores. Source databases were opened with `mode=ro`; no
provider credentials were read, no source code was imported/executed, and no
source data was modified. `research/multibagger_data_audit.json` records counts,
date ranges, inspected stores, scope, and calendar provenance. It does not
claim exhaustive validation of every archived blob or synthetic test fixture.

### Follow-up to the supplied review

The review's 87-test baseline describes the earlier source. Direction-specific
breadth gating, volatility units, signed reward, initial R, next-open execution,
and loss accounting were addressed in the preceding passes. Further checks
identified and resolved the remaining breakout-target problem:

- Removed unused EMA200 and replaced the 200/249-bar warm-up assumptions with
  110 raw bars (60 rolling-return warm-up bars plus 50 usable analysis bars).
- Shifted the 20-bar support/resistance windows by one bar. Levels broken by the
  completed signal close are represented as absent execution barriers; their
  raw values remain in features. Unbroken barriers remain fixed across a gap.
  Missing barriers use the ATR target, without an invented percentage haircut.
- Added a local-file integration test that requires nonempty trades using the
  real scoring, regime, sizing, constraints, exits, and cost model. It does not
  mock signals or orders. A separate test requires a valid signal and order
  from 151 raw bars.
- Renamed the misleading ADV settings to `MIN_AVG_5M_VOLUME` and
  `MIN_AVG_5M_TURNOVER`. Their numerical values are unchanged. The rubric now
  states that these are five-minute averages and a score penalty, not daily
  liquidity measures or an observed spread. The turnover floor is ₹2 crore.
- Moved the polling alert threshold into configuration, made the version and
  universe count dynamic, removed import-time console mutation, and rechecked
  market hours after sleeping. The bot identifies itself as a signal service.
- Removed stale fixed test counts from the repository navigation instructions.

Short scoring remains asymmetric; changing its RS bonuses requires strategy
research. Structural-exit churn, fee sensitivity, and the restrictive liquidity
floors also require market-data validation. Capital is explicitly unlevered and
risk budgets are ceilings, so cash-limited sizing is expected. No leverage or
new strategy thresholds were introduced to manufacture trades.

The old sensitivity/WFO CSVs were not overwritten with synthetic results.
Regenerating market-performance reports requires compatible, vetted five-minute
stock and benchmark histories; the audited daily files cannot supply them.

## Sector coverage and runtime follow-up

The 23-entry map pooled 179 configured symbols into `OTHER`, causing the
two-per-sector position limit to bind across unrelated businesses. Rebuilt the
map from the saved NSE constituent CSV in the other project's
`review/universe-expansion-20261015/nifty500.csv`. It now has 501 symbols in 20
sectors and covers 199 of 201 configured tickers. Source path and SHA-256 are in
`data/nse_sectors.provenance.json`; `scripts/build_sector_map.py` reproduces it
without downloads. Other-project files were not changed.

`DUMMYHDLVR.NS` and `LTIM.NS` have no exact match in that snapshot and retain
conservative shared-`OTHER` treatment. No symbol aliases were inferred. The
consistent taxonomy groups banks and other financial firms as `Financial
Services`, changing concentration groupings from the prior mixed labels.
This is current snapshot metadata, not historical point-in-time classification.
Malformed maps now raise explicit errors, and local reports include coverage.
Tests permit five positions in distinct classified sectors while still rejecting
a third financial-sector position. Cash, risk and correlation gates still apply.
Existing paper databases pin their original sector map and require a new account
or explicit migration; they are not silently rewritten.

Profiling identified repeated pandas 15-minute resampling and RS calculations
as the main cost. Array aggregation now computes only observed complete groups;
aligned price arrays replace repeated joins and shifted return Series. The same
rolling-window 15-minute EMA is retained. Irregular/nonfinite resampling inputs
fall back to the original pandas routine. Scoring thresholds and exits did not
change.

On the same eight-ticker, five-session synthetic workload, an unprofiled run
fell from 12.497 seconds to 4.637 seconds (2.70x faster). All 15 trade records
and all decision events match exactly. `research/runtime_comparison.json`
records the comparison; `scripts/benchmark_backtest.py` reproduces the workload.
Regression tests compare the optimized calculations with the original pandas
operations across gaps, partial windows, aware timestamps and irregular inputs,
then compare complete real-engine executions. This is a single local timing
comparison, not a 200-ticker scalability or profitability claim.

Cost sensitivity and structural-exit churn still need real intraday data.
Short scoring remains unchanged, no compatible five-minute histories were
found, and the paper ledger still requires explicit events from its caller.

The original baseline had 87 passing tests. The first pass expanded that to 141;
the second pass reached 223, the review follow-up reached 230, and the current
suite passes **268 tests**. Verification
completed across 25–26 October 2026 and
used the project `.venv` (Python 3.12.10, pandas 2.3.3, NumPy 2.2.6, pandas-ta
0.4.71b0, pytest 8.4.2):

- `python scripts/verify_project.py`: syntax, configuration, all 268 offline tests,
  and simulation entry-point imports passed.
- `python -m pip check`: no broken requirements in the project environment.
- `git diff --check`: no whitespace errors.

The shared host Python installation still has unrelated dependency conflicts;
its packages were not changed.

The tests use synthetic/provider-stub data and block outbound Python socket
connections, while allowing literal loopback sockets for asyncio IPC. Manual
network runners are import-safe. The CLI integration requires nonempty trades,
serializable timestamps, reconciled net PnL, and unchanged source hashes. The
other-folder audit used actual saved daily files. No live market simulation or
outbound alert was performed.

`python scripts/verify_project.py` checks syntax, configuration, the offline
tests, and simulation entry-point imports. CI also checks installed dependency
compatibility. Live simulations remain explicit opt-in commands.

## Remaining production and research requirements

- **Broker operations:** no connected account state, real order submission,
  partial-fill handling, or broker reconciliation. The new persistent paper
  ledger requires explicit entries/marks/exits and is not wired to the scanner.
  Scanner outputs remain indicative; restarting loses scanner deduplication.
- **Market data:** vetted five-minute stocks and benchmark histories,
  point-in-time universes, corporate-action provenance, and feed-quality SLAs
  are still required. The daily snapshot from the other project is unsuitable.
- **Calendar and operational controls:** weekday/time gates do not model NSE
  holidays, special sessions, halts, auctions, or symbol-specific availability.
  Persistent monitoring, recovery drills, operator controls, and controlled
  paper trading are required before deployment.
- **Execution assumptions:** opening quotes are assumed executable. When both
  stop and target are touched after an open between those levels, stop takes
  precedence. Trailing changes apply to later bars. OHLC cannot reveal the true
  intrabar path, queue position, latency, liquidity, or partial fills.
- **Accounting marks:** terminal/breaker/EOD liquidations at stale last-observed
  quotes are explicitly labelled assumptions, not evidence of obtainable exits.
  Timestamp fields use candle-start labels for intrabar events, not tick-level
  fill timestamps. Structural exits are evaluated at the observed close.
- **Risk calibration:** nominal stop-distance caps do not bound gap or fee
  losses and are not a covariance/margin/stress model. Unknown sectors share
  OTHER. Daily loss halts prevent new entries; existing positions retain their
  normal exit rules unless the drawdown breaker triggers.
- **Dependencies and costs:** fee rates were not independently revalidated;
  dependency ranges are not a hash-locked deployment artifact. Live provider and optional
  AI-service compatibility were not exercised. Use the project environment
  rather than the conflicting shared host installation.
- **Performance:** no audited performance or profitability conclusion follows
  from passing tests. Historical results from the previous signal-close model
  must be regenerated under next-open execution before comparison.

## Architecture map

A code-only graph was generated locally in ignored `graphify-out/`, with a
report, JSON graph, and interactive HTML. Its main hubs are BrainAV5,
PortfolioEngine, LiveScanner, and ExecutionModel. It covers this repository, not the
other repositories. Structural extraction used no model tokens or API calls.
Inferred relationships are navigation aids; the findings above were confirmed
against source and regression tests.
