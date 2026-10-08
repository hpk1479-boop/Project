"""163: another base frame of a running request is held until the whole request has ended.

Its result.json is written before the whole request has ended, so meanwhile it is not listed,
not deletable or stoppable on its own, and reads as running.
"""
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

PART3 = Path(__file__).resolve().parents[1]
ROOT = PART3.parent
sys.path[:0] = [str(PART3), str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]
from lab import backtest_jobs as jobs

SCENARIO = {'symbol': 'XAUUSD+', 'start': '2026-09-01', 'end': '2026-10-01', 'strategies': ['SPECIAL9'],
            'result_mode': 'VIRTUAL_ENTRY', 'virtual_bases': ['1m', '2m']}


@pytest.fixture
def request_runs(tmp_path, monkeypatch):
    runs = tmp_path / '창고' / 'runs'
    lead, lane = runs / ('a' * 32), runs / ('b' * 32)
    lead.mkdir(parents=True)
    lane.mkdir()
    jobs._atomic(lead / 'web_scenario.json', SCENARIO)
    jobs._write(lead, {'version': jobs.VERSION, 'id': lead.name, 'kind': 'normal', 'phase': 'run',
        'scenario': SCENARIO, 'scenario_file': 'web_scenario.json', 'adapter': {},
        'result_path': 'runs/' + lead.name + '/result.json', 'cancel_requested': False,
        'supervisor': {'pid': 123, 'created': '5', 'run_id': 'f' * 32}, 'process': {'pid': 124, 'created': '6'},
        'created_at': 1, 'updated_at': 1})
    (lane / 'lane.json').write_text(json.dumps({'lead_run_id': lead.name}), encoding='utf-8')
    jobs._atomic(lane / 'result.json', {'status': 'COMPLETE', 'result_mode': 'VIRTUAL_ENTRY',
                                        'scenario': {**SCENARIO, 'virtual_bases': []}})
    alive = {123: '5', 124: '6'}
    monkeypatch.setattr(jobs, 'process_identity',
                        lambda pid, *a, **k: {'pid': pid, 'created': alive[pid]} if pid in alive else None)
    monkeypatch.setattr(jobs, '_closing', False)
    context = {'warehouse': tmp_path / '창고', 'project_root': ROOT, 'python_executable': sys.executable}
    return lead, lane, alive, context


def test_a_finished_lane_waits_while_its_request_runs(request_runs):
    lead, lane, _, context = request_runs
    assert [item['job_id'] for item in jobs.recent(20, **context)['items']] == [lead.name]
    state = jobs.status(lane.name, **context)
    assert state['phase'] == 'run' and state['active'] and not state['result_ready']
    assert state['message'] == jobs.LANE_RUNNING
    result = jobs.delete_selected([lane.name], **context)
    assert not result['deleted'] and '함께 시험 중' in result['errors'][0]['message']
    for force in (False, True):
        with pytest.raises(ValueError, match='대표 실행에서 정지'):
            jobs.stop(lane.name, force=force, **context)
    assert (lane / 'result.json').is_file() and not (lane / 'stop.request').exists()
    assert [job['id'] for job in jobs.active_jobs(**context)] == [lead.name]  # the close inventory is the request


def test_an_unknown_process_state_keeps_the_lane_held(request_runs, monkeypatch):
    _, lane, _, context = request_runs
    monkeypatch.setattr(jobs, 'process_identity', Mock(side_effect=OSError('isolated identity unavailable')))
    assert jobs.status(lane.name, **context)['phase'] == 'run'
    assert not jobs.delete_selected([lane.name], **context)['deleted'] and lane.is_dir()


def test_the_lane_opens_once_the_request_has_ended(request_runs):
    lead, lane, alive, context = request_runs
    alive.clear()  # the supervisor and its Part2 run have exited
    assert {item['job_id'] for item in jobs.recent(20, **context)['items']} == {lead.name, lane.name}
    state = jobs.status(lane.name, **context)
    assert state['phase'] == 'complete' and not state['active'] and state['result_ready']
    assert jobs.delete_selected([lane.name], **context)['deleted'] == [lane.name] and not lane.exists()
