# Offline paper account

`PaperLedger` records simulated entries, synchronized marks, and explicit full
exits. There is no network client, broker API, alert sending, or real order
submission. Signals and quotes are supplied by the caller. It does not yet run
the scanner, evaluate structural exits, process stop/target touches, recover a
feed, handle partial fills, or reconcile with a broker.

## Try the synthetic example

```powershell
.\.venv\Scripts\python.exe run_paper_account.py --ledger research/data/paper_demo.db --events examples/paper_events.jsonl
.\.venv\Scripts\python.exe run_paper_account.py --ledger research/data/paper_demo.db
```

The example uses invented prices solely to demonstrate accounting. It is not
historical market data. Run the first command again: the same event IDs return
the original results without creating another trade. An existing ID with a
different payload is rejected. Every successfully applied line commits
independently, so after an interruption the same file can be replayed. Rejected
entry decisions also retain their IDs; new decisions need new IDs.

Use a different database for a new account or strategy configuration. Reopening
an account preserves initial equity, realized PnL, positions, current liquidation
equity, loss halts, and timestamps. Configuration changes require explicit
migration or a new account; the application does not reset an existing account
silently. A transaction writes the event and the account checkpoint together.

## Input contract

Each JSONL row contains `action` (`enter`, `mark`, or `exit`), a unique `event_id`,
and the parameters shown in the example. Timestamps are interpreted as IST when
naive; timezone-aware inputs are converted to IST. Events must be chronological.

For an entry:

- `signal` is the completed-bar BrainA result. It must be JSON serializable and
  include ticker, direction, score, close, and ATR. Structural levels and
  features can be included.
- `signal_time` is its candle-start time, and `timestamp` is the immediately
  following five-minute candle's opening time in the same session.
- `price` is the supplied opening quote. The account rechecks the gap, score,
  risk/reward, available cash, position budget, and portfolio constraints.
- `authorized: true` is an explicit upstream regime/direction decision. The
  ledger cannot independently establish breadth or authorization from a lone
  signal; a missing/false decision rejects entry.
- `quotes` must contain a positive price and matching event timestamp for every
  existing position. Missing/stale marks fail without mutating account state.

An exit supplies `position_id` (the entry event ID), price, timestamp, and reason.
Quotes are required for other open positions at that timestamp. Exits remain
available during a halt. The ledger does not itself force flattening; operators
or a future paper strategy runner must submit the appropriate exits.

## Risk and accounting scope

The paper account shares order generation, fee estimation, cash allocation, and
portfolio constraints with the simulator. Both longs and shorts reserve full
entry notional; no leverage or broker-margin model is implied. The default
per-position capital limit is 20% of liquidation equity including estimated
round-trip fees, alongside 1% trade and 5% aggregate initial stop-risk caps.
Sector and position-count limits can prevent filling all five slots.

No return history is attached to this foundational ledger: correlation uses
the existing sector proxy, and sizing uses the target-volatility fallback.
It is therefore not a substitute for the simulator's full historical context.
Daily-loss halts persist through recovery until the next session; drawdown halts
remain latched. Marking a loss halts entries but does not manufacture an exit.

The last snapshot is only as current as its last applied event. The caller owns
feed completeness and timeliness. The weekday/time gate still lacks a full NSE
holiday/special-session calendar. A stored 2026 calendar snapshot was found in
the other project but is not automatically activated or treated as complete.

SQLite transactions and duplicate IDs address local restart/replay failures;
they do not constitute distributed broker reconciliation, operational monitoring,
or production certification.
