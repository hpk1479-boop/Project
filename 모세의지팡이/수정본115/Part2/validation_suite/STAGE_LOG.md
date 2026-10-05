# Sequential implementation ledger

## 0 — startup (committed before step 1)
Original copied Windows virtual environments reference missing Codex base Python.
Added executable probes, explicit local-Python CMD bootstrap, repair-with-backup,
rollback, dependency pins and diagnostics. Linux mocked/startup tests: 5 PASS.
Actual Windows GUI / MetaTrader5 start: SKIP (Linux host).

## 1 — lowest WATCH timeframe close gate (committed before step 2)
8 gate tests PASS. One-hour callbacks [1m,3m,6m,15m] = [59,19,9,3].
Base row closed; higher rows are current prefix. Future-spike prefix assertion PASS.
1 day and 7 day synthetic 30-second raw gate subsystem benchmarks recorded before
and after, 4 bases x OFF/ON. These are NOT full-strategy real-market measurements.
See validation_outputs/stage1_gate_{before,after}.json.

## 2 — exact retained native band windows (committed before step 3)
52 unit tests PASS. Each family: 16,800 paired native invocations, 0 mismatches
(all buffers, NativeCell bit/provenance fields, metadata and write order).
Both original grid100 and exact interpolation tested; P20 unchanged. Finite seed
fixtures are explicitly NATIVE_STATE_CONDITIONED, not genuine source-only native
initialization. Source-only warmup tested separately with undefined states intact.
7 scenarios x 2 precision modes, 5 independent 240-observation sequences each.
No disk cache. Cold = per-sequence first invocation; warm = subsequent invocations.
Full native paired compute totals (reference -> incremental seconds):
PRICE 55.577932 -> 54.850276 (1.013266x)
RSI   18.224509 -> 16.097646 (1.132123x)
STO   25.763574 -> 24.085565 (1.069669x)
DI    26.265688 -> 25.096705 (1.046579x)
Band-only finite-value P20 12,000-row microbench grid100 ~7.55–8.28x;
exact ~2.19–2.65x. NOT a full-kernel or whole-run speedup.
First unbounded long test timed out at 200 seconds after 3 PRICE scenarios;
partial evidence preserved and bounded independent-sequence suite completed for
all families. Long real raw is unavailable in the supplied archive: NOT tested.
See validation_outputs/stage2_*.json and *.log.

## 3 — ordered OZ array hot path (completed before step 4)
50 unit tests PASS (including nonempty LONG/SHORT alerts in all 6 profiles,
TRUE B0, opposite/one-way/timer cancellation, family expiry, NaNs/equality,
restart gap, retry/future-read guards, dtype fallback and Copy-on-Write).
12,000 observation-level original-stage2 vs final-stage3 traces (6 x 2,000;
650 rows per each of 3 TFs): 0 mismatches in full state and event IDs/order.
`oz_rules.py` SHA-256 exactly unchanged from original. Private readonly column
export has checked public fallback; no cross-observation array caching.
Pandas Series initialization in NORMAL/OZ profiled 150-observation sample:
10,377 -> 450; DataFrame row/column _ixs 16,877 -> 450; vector datetime conversion
3,199 -> 0 (some other trigger profiles retain scalar timestamp conversions).
Wall times and coverage are in validation_outputs/stage3_summary.json. Profiling
timings are separate from unprofiled long-run observe wall-time measurements.
One combined shell invocation timed out at 60 seconds after all 50 tests passed
and 3 profile samples completed; partial log preserved. Standalone rerun completed
all 6 profiles and the full long sequence with returncode 0.
Actual 1d/1w raw and MT5 live/native terminal parity remain untested, not inferred.

### Stage 3 integration correction, before IPC optimization
Compiler smoke test initially FAILED: E_WATCH_SOURCE_DRIFT: oz.py. The existing
local integrity manifest still pinned the original adapter files (including the
step-1 frames.py). Updated *local packaged-file* hashes and added array_frame.py
after exact tests; kept source-origin hashes and original rule hash unchanged.
Integrity verification remains enabled. Old/unreviewed file mutations still fail.

## 4 — isolated-worker IPC (implemented after stages 1–3)
36 unit/integration tests PASS: constructor-template decode cache (fresh object
identity preserved), future guards, ACK/limits, two real subprocess roles with
positive LONG/SHORT outputs, capability denial, batched HMA IEEE-bit equality
for 8 scenarios x 2 capacity settings, cache scope/checksum checks, UI dedup.
Existing persistent workers, DELTA_V1 and local memo LRU were retained, NOT
counted as new optimizations. New pure numerical GET/PUT batches are <=128 items,
ordered/acknowledged; each item counts toward the original 10,000 RPC-op budget.
No observations, callbacks, or strategy decisions are batched or skipped.
First 120-observation actual isolated WATCH / unavailable-native fixture:
cold 2.060516 -> 1.447990 s; warm 1.921500 -> 1.354216 s (observe wall only).
Cold cache RPC messages 2794 -> 140; warm 1397 -> 70. Input/output SHA-256 and
finish diagnostics equal. Empty OZ alerts are explicit, not signal equivalence
proof; positive SIGNAL/ENTRY and OZ tests are separate.

### End-to-end integration corrections (failure logs preserved)
Step-1 result finalization initially failed because results.verify_result still
required legacy fixed-M1 metadata. The new verifier checks the actual lowest TF
and new view contract, and separately continues to verify old M1 bundles without
reinterpreting their cadence. 3m full coordinator/result verification rerun PASS.
Retain one private completed-base slot when a generic strategy explicitly has
lookback 0; readiness and declared requirements remain unchanged, TICK untouched.
The TRADE test fixture first used unsupported outcome basis BID (correct runtime
rejection); corrected the fixture to CHART_PRICE, not the strategy/evaluator.
Full sampled TRADE coordinator/result verification rerun PASS.

## Final sequential integration measurements
153 unit/integration tests PASS on Python3.13.5/NumPy2.3.5/Pandas2.2.3.
80 BAR WATCH full coordinator cases (5 stages x 1d/7d x 4 bases x OFF/ON)
all completed and verified. Step1 -> steps2/3/4 semantic output file hashes:
48 pairs / 192 files, exact match. This BAR fixture does NOT invoke percentile/OZ.
Trade 12 cases completed; original TICK -> final 2 pairs/20 files exact;
step1 -> final TICK/CLOSE 4 pairs/40 files exact (positive LONG/SHORT, SL/TP).
Additional original/final: 24 command outcomes exact; 2,880 4-TF OHLC snapshots
exact; TREND 240 observations/720 TF evaluations exact, 36 nonempty state events;
HMA6/17 TICK 4 cases per root, positive alerts 142/54/142/54, exact file hashes.
Additional evidence comparer: 92 PASS, 0 FAIL.
IPC 3-trial median observe wall: cold 2.053564 -> 1.379013 s;
warm 1.930499 -> 1.350234 s. Startup not improved; no claim of positive OZ here.
Pandas3.0.1 isolated venv created but pip install failed on DNS; alternative
wheel download also failed. Target runtime tests SKIP, original pins unchanged.
Actual broker raw/Windows GUI/MT5/native terminal comparison remain SKIP.
