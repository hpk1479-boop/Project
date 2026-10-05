"""P5: exact block validation of already hash-checked native tick records.

Used only in archive preflight. No TickRecord or market state is published here.
The live iterator remains unchanged and still exposes one prefix at a time.
"""
import numpy as np
from pit.contracts import PitError
from ..contracts import GenericError

NS_PER_MS = 1_000_000
I64 = np.iinfo(np.int64)


def verify_chunk_records(reader, raw_hash, previous_global_ms, block_rows=65536):
    """Return (count,last_ms), retaining old first-error code/ordinal precedence."""
    start = reader.descriptor['coverage_start_ns']
    end = reader.descriptor['coverage_end_ns']
    if type(start) is not int or type(end) is not int:
        # Keep original behavior for unusual legacy descriptor scalar types.
        count = 0
        for tick in reader:
            if tick.time_msc < previous_global_ms:
                raise GenericError('E_CHUNK_CORRUPT', 'global tick order')
            previous_global_ms = tick.time_msc
            raw_hash.update(tick.to_bytes())
            count += 1
        return count, previous_global_ms
    if type(block_rows) is not int or block_rows < 1:
        raise ValueError('positive block_rows required')
    lower_ms = -(-start // NS_PER_MS)
    upper_ms = -(-end // NS_PER_MS)
    rows = reader._rows
    local_last = -1
    for offset in range(0, len(rows), block_rows):
        block = rows[offset:offset + block_rows]
        ms = block['time_msc']
        # At the chunk boundary global validation used to run after both local
        # validations of row zero, but before any errors in later rows.
        first = int(ms[0])
        if offset == 0:
            if first < local_last:
                raise PitError('E_RAW_ORDER_REGRESSION', '1')
            if not start <= first * NS_PER_MS < end:
                raise PitError('E_RAW_HASH', 'record outside declared coverage')
            if first < previous_global_ms:
                raise GenericError('E_CHUNK_CORRUPT', 'global tick order')
        invalid_order = np.empty(len(ms), dtype=bool)
        invalid_order[0] = first < local_last
        invalid_order[1:] = ms[1:] < ms[:-1]
        # Compare in ms without int64 multiplication overflow, including ranges
        # with non-ms-aligned ns boundaries or bounds outside signed int64.
        if lower_ms > I64.max or upper_ms <= I64.min:
            invalid_range = np.ones(len(ms), dtype=bool)
        else:
            invalid_range = np.zeros(len(ms), dtype=bool)
            if lower_ms > I64.min:
                invalid_range |= ms < lower_ms
            if upper_ms <= I64.max:
                invalid_range |= ms >= upper_ms
        invalid = invalid_order | invalid_range
        if invalid.any():
            index = int(np.flatnonzero(invalid)[0])
            if invalid_order[index]:
                raise PitError('E_RAW_ORDER_REGRESSION', str(offset + index + 1))
            raise PitError('E_RAW_HASH', 'record outside declared coverage')
        raw_hash.update(block.tobytes())  # exact <qQQQQqIQ bytes, no float conversion
        local_last = int(ms[-1])
        previous_global_ms = local_last
    return len(rows), previous_global_ms
