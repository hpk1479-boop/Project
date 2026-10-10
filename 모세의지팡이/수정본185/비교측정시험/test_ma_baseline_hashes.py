"""WATCH MA arrays against recorded hashes, run only on request (moved out of Part1/watch_ma_validation in 수정본180).

The hashes were recorded with one NumPy/pandas version; other versions skip. The calculation itself is
checked against the formulas in tests/test_watch_ma_contract.py.
"""
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part1/program'))
import watch_ma as ma
import indicator_facts as math_source


def sample_frame(size=600):
    x = np.arange(size, dtype='float64')
    return pd.DataFrame({'open': 100. + np.sin(x * .37) * 9. + x * .013,
                         'close': 99. + np.cos(x * .23) * 6. + x * .017})


def functions():
    return {family: getattr(math_source, family.lower()) for family in ('SMA', 'WMA', 'EMA', 'HMA')}


def test_recorded_before_after_hashes():
    fixture = json.loads((Path(__file__).parent / 'baseline_ma_values.json').read_text())
    if fixture['numpy'] != np.__version__ or fixture['pandas'] != pd.__version__:
        pytest.skip('ENVIRONMENT_LIMITATION: recorded hashes require the baseline NumPy/pandas versions')
    frame = sample_frame()
    for name, record in fixture['general'].items():
        actual = ma.MAFeatureCache(functions()).calculate(frame, [name])[name].to_numpy()
        assert hashlib.sha256(actual.tobytes()).hexdigest() == record['sha256'], name
        assert np.isnan(actual).tolist() == record['nan_mask'], name
        valid = np.flatnonzero(~np.isnan(actual))
        assert (int(valid[0]) if len(valid) else None) == record['first_valid'], name
        assert int(actual.view(np.uint64)[-1]) == record['last_bits'], name
