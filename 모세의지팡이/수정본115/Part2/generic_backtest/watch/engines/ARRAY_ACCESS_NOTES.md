# OZ array access invariant

The public DataFrame/token/availability validation remains unchanged. The
complete original retry signature (including all columns, dtypes, attrs, signed
zeros, NaN payload bytes) is computed before executing any state transitions.

`oz_rules.py` is byte-for-byte identical to the supplied original:
SHA-256 `5cc327d9a4feda20fde885e4174cde4262c6f6b3af0faa416452ab1796d151`.

For ordinary Pandas DataFrames with native numeric columns and naive datetime64
bar times, each observation acquires fresh column views. A readonly scalar row
adapter replaces repeated Pandas `.iloc` -> Series allocations. The only two
specialized rules are timestamp-to-bar-distance and the last-three-closed-candle
one-way check. Both retain original inequalities, missing-value behavior and
infinity behavior. Strict timestamp uniqueness/RangeIndex was already validated.

No DataFrame is cached across observations by this adapter; Copy-on-Write or
column/block replacement cannot produce stale columns. Arrays use independent
readonly view flags and never mutate the owning DataFrame or its flags.

The `_get_column_array(i)` readonly hook is present in the upstream pandas
2.2.3 and 3.0.1 source. This is a private optimization, NOT a new public dependency
contract: shape/dtype are checked, unsupported managers/hooks fall back to public
`Series.to_numpy`. Object/extension/timezone columns or DataFrame subclasses
retain original Pandas rules. Tests explicitly cover fallback, Copy-on-Write,
column replacement and retry/future-read rejection.

`HistoricalOZEngine(..., use_numpy=False)` retains the reference path for audits.
No WATCH syntax, rule version, event-ID expression, transition ordering,
checkpoint state field, REGIME or DIVERGENCE_REGIME definition was changed.
