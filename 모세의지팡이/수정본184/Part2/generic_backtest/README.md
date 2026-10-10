# Generic Backtest V1

This is a parallel research path. LIVE `program/`, the legacy Replay/Backtest
engine, the old PIT job and its `TEST_PIT_HMA_PRICE_V1` are unchanged. Generic
does not create OZ carriers or send Telegram messages.

## User workflow

Open `BACKTEST CONTROL.pyw`: only **Generic Backtest V1** is displayed.
Engine/plugin approval receipts are no longer runtime prerequisites. Existing
receipts remain historical records and are never updated automatically.

1. Put a plugin in `backtest_specials/`. Discovery reads its literal metadata.
2. Select a strategy and confirm once: **전략을 변경하시겠습니까?**
   Current source hashes/version/environment/dataset identity are recorded in
   every result. A changed source hash alone does not block execution.
3. **MT5 연결 / 심볼 가져오기** discovers running terminal processes. A single
   non-LIVE terminal is selected automatically; multiple terminals show a picker
   with observed LIVE terminals first. LIVE use requires one confirmation per
   process/account identity in the current GUI session. Declining offers another
   running terminal or cancels. No manual terminal/profile path entry is required.
4. Identity comes from fresh STAFF pipe-peer + EA status receipts when available,
   otherwise whitelisted saved Common Login/Server and data-folder origin metadata.
   Missing identity, duplicate executable processes or a post-connect mismatch
   stop the query. An unknown LIVE association is explicitly confirmed, not
   silently labelled LIVE OFF. The history child rechecks the running PID before
   initialize and validates terminal path/data root/server/login immediately after.
   It never calls login, symbol_select, order APIs or terminal/profile changes.
5. Choose UTC start/end, display timezone and explicitly select
   `UTC_GRID_RESEARCH_V1`. End is exclusive. This is not broker-native calendar
   parity. Broker mode requires separate historical evidence and review.
6. Select `ALERT_ONLY`, or `TRADE` with explicit Stop/Target variants. No N3/N10
   or RR is selected implicitly. Direct targets do not receive an extra RR.
7. **데이터 가져오기 → 실행** plans per-role real-tick warmup, reuses a verified
   cache or fetches it, then starts a separate compute child. The user does not
   select `.npy`, descriptor or seed files. Warmup uses actual earlier ticks;
   future final bars are never injected as forming state.
8. The Dashboard opens only after child exit and Generic bundle verification.
   Cancel ACK is not completion. A cancelled run has no successful handoff.

## One-file strategy API

Use `backtest_specials/GENERIC_EXAMPLE_V1.py` as the SDK example. It is explicitly
a test fixture, not a market strategy. Put a new `.py` alongside it and give its
literal `BACKTEST_PLUGIN` a `plugin_id` equal to the filename stem. The dropdown
discovers it without editing runner/core/registry code.

Metadata: `api_version`, `plugin_id`, `display_name`, `plugin_version`,
`supported_modes`, `parameters_schema`, `risk_anchors`, optional `capabilities`.
The schema accepts integer/number/string/boolean, minimum/maximum/enum/default.

- `requirements(params, selection)` returns signal/entry `StrategyRequirements`.
  Each declares TFs, completed history limits, real-tick warmup duration and
  optional features. SIGNAL and ENTRY start/window state are independent.
- `create_alert_strategy(params)` returns an object with
  `on_observation(ctx) -> tuple[AlertIntent, ...]`.
- `create_entry_strategy(params)` returns an independent object with
  `on_observation(ctx, current_alerts) -> tuple[EntryIntent, ...]`. It is never
  constructed in `ALERT_ONLY`. No Stop/TP/RR/outcome feedback is sent to either
  strategy role.
- Optional `on_warmup(ctx)` returns None. Optional `on_finish(ctx)` returns only
  diagnostics. Neither emits retrospective events.

Each occurrence needs its own explicit key. Identical retries are idempotent;
conflicting keys are rejected. There is no shared one-shot or position-count
limit. Strategies own direction and final Entry price/time. Time/token must be
the current observation. `OBSERVED_QUOTE` references bid/ask/last exactly;
`STRATEGY_MODEL_PRICE` requires model_version and description and is labelled
hypothetical, never broker fill. Anchors must be declared in metadata.

`ctx.quote`, `ctx.bars(tf)` and `ctx.feature(name)` are read-only current-prefix
capabilities. Only declared TF windows are transmitted. Same-ms ticks each get
a distinct token and callback. No future reader, dataset path, risk settings or
LIVE modules are present. SDK imports are `generic_backtest.contracts`' intent,
requirements and token classes, plus math/statistics/decimal/fractions/collections.
Strategies may implement conventional indicators from their bounded history.
Those are not automatically Moses parity implementations.

Optional features: `HMA_OPEN` (source HMA6/17 kernel, arbitrary symbol) and
`MOSES_PERCENTILE` (existing provider, source-defined-only seed). They must be
declared with name/timeframe and period or family/params. Existing Percentile
seed/readiness/parity restrictions and `RSI_RAW_GATE=BLOCKED /
UNVERIFIED_NATIVE_RSI` are preserved. No feature is mandatory.

## Trade policies and outcomes

Stops: N_COMPLETED_EXTREME, PRICE_DISTANCE, TICK_DISTANCE, PERCENT_DISTANCE,
ABSOLUTE_LEVEL, STRATEGY_ANCHOR. Targets: RR_MULTIPLE, the distance/level/anchor
choices, or explicit NONE. Resolution occurs once per Entry/Stop, then targets
expand it. N always excludes forming bars and never shortens insufficient
history. Tick distance uses trade_tick_size, not point.

Generic outcomes consume subsequent ordered quote points. They never reuse
the entry candle's accumulated high/low. Newly accepted Entries activate after
their current raw tick. Already-crossed barriers at acceptance are
NOT_EVALUATED. Known acquisition gaps censor open trades; observed empty query
intervals alone are not proof of a missing price path. Explicit unordered
whole-post-entry envelopes can yield AMBIGUOUS. EOF never forces liquidation.

Statistics use confirmed gross R, not money/equity. Same-exit-order outcomes
are grouped for drawdown; mixed simultaneous win/loss ordering is not invented.
Alert buckets use display timezone and retain UTC timestamps, unknown coverage,
the requested-wall-hour denominator and adjacent-event intervals excluding
censored endpoint tails.

## Isolation and approvals

V1 is `PROCESS_CAPABILITY_V1`, not a hostile-code OS sandbox. SIGNAL/ENTRY have
separate module instances and subprocesses, JSON pipes, restricted SDK imports
and builtins, and an audit deny policy for file/network/child-process access.
Coordinator owns archive handles. Only explicitly reviewed source may run.
AppContainer is a separate hardening stage, not a V1 prerequisite; adversarial
native-code containment is not claimed.

The old operational/runtime manifests are never updated. Generic approvals live
in `generic_baseline/` and `generic_approvals/`. Test-only approvals are scoped to
disposable acceptance outputs, and do not approve the production engine/plugin.
Runtime drift fails closed. Numeric and MT5 environments are separately recorded.

## Storage and verification

- `generic_cache/raw/`: immutable canonical 60-byte tick arrays, full unknown
  flags and duplicate same-ms ticks preserved. No value dedup or downsampling.
- `.partial`: resume verifies committed chunk hashes; corrupt chunks fail.
  Orphan pre-receipt files are preserved and are never reused as committed data.
- `generic_runs/`: new per-run bundles. `seed_plan.json` records actual-prefix
  initialization and per-role cutoff; no fabricated seed data.
- `ALERT_ONLY`: events, alerts, Dashboard, manifest and completion, no trade files.
- `TRADE`: additionally entries, rejected decisions, stops, targets, outcomes,
  comparison. Legacy verifier is never used for a Generic bundle.

Raw broker completeness is UNVERIFIED absent independent evidence. Empty
intervals/errors are surfaced. Native endpoint/precision, actual terminal/data
root and actual XAU/NAS acquisition require the separate real-terminal smoke
test; fixture tests are not substituted for this evidence.

CLI examples (explicit approved environment):

```powershell
.venv-generic/Scripts/python.exe -B -m generic_backtest discover
.venv-generic/Scripts/python.exe -B -m generic_backtest run --config JOB.json --output generic_runs/NEW
.venv-generic/Scripts/python.exe -B -m generic_backtest verify --input-dir generic_runs/NEW
.venv-generic/Scripts/python.exe -B -m generic_backtest rescore --input-dir generic_runs/NEW --output generic_runs/NEW_RR --rr '1,1.5,2'
```

Rescore reads frozen entries/stops and the source archive, calls no strategy,
Entry search or Stop resolver, and writes a separate result. Source/input
identity and exact activation tick are retained.

MT5 API references: [initialize](https://www.mql5.com/en/docs/python_metatrader5/mt5initialize_py)
and [copy_ticks_range](https://www.mql5.com/en/docs/python_metatrader5/mt5copyticksrange_py).

## Project relocation

The launcher, interpreters and Generic storage resolve from the current project
ROOT, independently of the shell working directory. New saved internal paths
are relative; old Generic cache/profile references are rebound to this ROOT
without rewriting historical evidence. MT5 executable/data-root settings remain
external user settings. Copying the project on the same Windows installation was
tested, including the copied compute venv. Standard Python venvs still depend
on their base Python installation (and compute currently inherits base NumPy).
This is not a self-contained installer for another PC.

Engine/plugin approval gates are removed; input/result integrity, current-token
checks, terminal isolation and broker-calendar evidence checks remain. They
protect data/clock semantics and are not engine/plugin source approvals.

## LIVE identity observer deployment

The STAFF EA includes `STAFF_Identity_Status.mqh` and publishes only identity
fields to `Terminal/Common/Files/StaffOfMosesStatus/`. The Python STAFF records its
actual existing pipe client's PID/image and heartbeat under
`program/logs/mt5_status/`. Generic only reads and correlates these files. An old
running EA/STAFF does not gain this capability merely because source was edited.
The implementation task did not replace the running EX5 or restart LIVE. Existing
approved source manifests are untouched; baseline hash checks detect the observer
addition and require a separate reviewed baseline decision.

MetaTrader5 initialize has no documented attach-only flag. V1 uses the explicitly
accepted practical running-process precheck + post-connect identity validation;
there is still a process-exit race between precheck and the API call.
