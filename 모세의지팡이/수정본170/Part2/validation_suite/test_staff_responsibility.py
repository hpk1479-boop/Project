"""S0 recorder must detect changes instead of silently blessing them."""
from __future__ import annotations

import sys
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from staff_golden.contracts import Recorder, compare, frame_digest
from staff_golden.scenario import INDICATORS, MA_CASES, indicator_combinations


def sample():
    df = pd.DataFrame({'value': [1.0, np.nan, 3.0], 'flag': [True, False, True]})
    df.attrs = {'source_epoch': 'a' * 32 + ':1', 'indicator_validity': {'PRICE': True}}
    return df


@pytest.mark.parametrize('change', ['value', 'dtype', 'order', 'index', 'attrs', 'epoch', 'zero_sign'])
def test_digest_detects_contract_changes(change):
    a, b = sample(), sample()
    if change == 'value':
        b.loc[0, 'value'] = np.nextafter(1.0, 2.0)
    elif change == 'dtype':
        b['flag'] = b['flag'].astype('int64')
    elif change == 'order':
        b = b[['flag', 'value']]
    elif change == 'index':
        b.index = pd.Index([3, 4, 5])
    elif change == 'attrs':
        b.attrs['indicator_validity']['PRICE'] = False
    elif change == 'epoch':
        b.attrs['source_epoch'] = 'a' * 32 + ':2'
    elif change == 'zero_sign':
        a.loc[0, 'value'], b.loc[0, 'value'] = 0.0, -0.0
    assert frame_digest(a) != frame_digest(b)


def test_random_epoch_prefix_only_is_ignored():
    a, b = sample(), sample()
    b.attrs['source_epoch'] = 'b' * 32 + ':1'
    assert frame_digest(a) == frame_digest(b)


def test_recorder_exact_roundtrip_and_negative_control(tmp_path):
    paths = [tmp_path / (name + '.sqlite') for name in ('first', 'repeat', 'changed')]
    for i, path in enumerate(paths):
        recorder = Recorder(path)
        df = sample()
        if i == 2:
            df.loc[0, 'value'] = np.nextafter(1.0, 2.0)
        recorder.add('request', {'1m': df})
        recorder.add('unavailable', {'error': 'FEED_NOT_READY'})
        recorder.close()
    assert compare(*paths[:2]) == {'equal': True, 'left_cases': 2, 'right_cases': 2,
                                   'exact_frames_checked': 1, 'different_cases': []}
    assert compare(paths[0], paths[2])['different_cases'] == ['request']
    with pytest.raises(FileExistsError):
        Recorder(paths[0])


def test_exhaustive_finite_request_domain():
    combinations = list(indicator_combinations())
    assert len(set(combinations)) == 2 ** len(INDICATORS) == 256
    assert combinations[0] == () and combinations[-1] == INDICATORS
    assert len(MA_CASES) == 6


def test_missing_evidence_is_not_a_pass(tmp_path):
    path = tmp_path / 'broken.sqlite'
    recorder = Recorder(path)
    recorder.add('request', {'1m': sample()})
    recorder.close()
    with sqlite3.connect(path) as db:
        db.execute('DELETE FROM frames')
    with pytest.raises(AssertionError, match='missing referenced'):
        compare(path, path)
    with pytest.raises(FileNotFoundError):
        compare(path, tmp_path / 'missing.sqlite')
