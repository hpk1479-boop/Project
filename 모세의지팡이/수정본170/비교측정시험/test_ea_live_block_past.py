"""The EA live block against older revisions, run only on request (moved out of the regular tests in 수정본162).

MOSES_COMPARE_ROOT names the folder holding the older revision folders (수정본23, 수정본34, ...).
Without it these checks are skipped; the project does not read outside its own root by default.
"""
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EA = 'Part1/program/MT5/THE_STAFF_OF_MOSES.mq5'


def past_source(revision):
    base = os.environ.get('MOSES_COMPARE_ROOT')
    if not base:
        pytest.skip('MOSES_COMPARE_ROOT를 지정하면 옛 수정본과 비교합니다.')
    path = Path(base) / revision / EA
    if not path.is_file():
        pytest.skip(revision + ' EA 원본이 없습니다.')
    return path.read_text('utf-8-sig')


@pytest.mark.parametrize('revision', ['수정본23', '수정본34'])
def test_ea_live_block_matches_past_revision(revision):
    current = (ROOT / EA).read_text('utf-8-sig')
    old = past_source(revision)
    assert current[current.index('int OnInit()'):] == old[old.index('int OnInit()'):]


def test_recording_paths_stay_behind_their_switches():
    current = (ROOT / EA).read_text('utf-8-sig')
    assert 'if(record) PipeCaptureSecond' in current
    assert 'if(g_native_export_enabled && RefreshBacktestInput' in current
