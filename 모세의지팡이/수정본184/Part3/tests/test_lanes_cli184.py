"""184: Part3 `cli.py lanes` — one plan file for several strategies × OZ triggers replayed together.

The plan is checked with messages that say what to do next; every strategy × trigger becomes one lane of a
Part2 lanes scenario; a plan run again skips its finished runs; status tells the group and the time left;
report builds the tables from the finished runs. Generated strategies are carried to the job's runner with
each file's digest, and the runner registers every one. No engine, recording or network is used here.
"""
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'Part3'), str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]
from lab import lanes  # noqa: E402

PLAN = {'symbol': 'XAUUSD+', 'start': '2026-01-01', 'end': '2026-02-01', 'mode': 'BAR', 'result_mode': 'VIRTUAL_ENTRY',
        'strategies': ['SPECIAL2', 'SPECIAL5'], 'triggers': ['올존', '무지성 올존'], 'spread_points': 20}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('network forbidden')
    monkeypatch.setattr(socket, 'create_connection', deny)


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    return path


# ---- the plan --------------------------------------------------------------------------------------

def test_a_plan_gets_its_defaults_and_lanes_are_strategy_by_trigger():
    plan = lanes.normalize(dict(PLAN, strategies=['Test_SPECIAL005.py', 'Test_SPECIAL006'], triggers=['올존']))
    assert plan['strategies'] == ['Test_SPECIAL005', 'Test_SPECIAL006'] and plan['generated'] is True
    assert plan['available_only'] is True and plan['max_lanes'] is None
    assert lanes.every_lane(lanes.normalize(PLAN)) == [
        {'strategy': 'SPECIAL2', 'trigger': '올존'}, {'strategy': 'SPECIAL2', 'trigger': '무지성 올존'},
        {'strategy': 'SPECIAL5', 'trigger': '올존'}, {'strategy': 'SPECIAL5', 'trigger': '무지성 올존'}]
    assert lanes.every_lane(lanes.normalize(dict(PLAN, triggers=[]))) == [
        {'strategy': 'SPECIAL2', 'trigger': None}, {'strategy': 'SPECIAL5', 'trigger': None}]


@pytest.mark.parametrize('changes,message', [
    ({'extra': 1}, '모르는 칸'),
    ({'mode': None}, '꼭 필요한 칸'),
    ({'strategies': 'SPECIAL2'}, '전략 이름 목록'),
    ({'strategies': ['SPECIAL2', 'SPECIAL2.py']}, '두 번'),
    ({'strategies': ['SPECIAL2', 'Test_SPECIAL005.py']}, '섞을 수 없습니다'),
    ({'triggers': '올존'}, '트리거 이름 목록'),
    ({'result_mode': 'BOTH'}, 'result_mode'),
    ({'mode': 'TIMER'}, 'mode는'),
    ({'available_only': 'yes'}, 'available_only'),
    ({'spread_points': -1}, 'spread_points'),
    ({'virtual_entries': {'SPECIAL9': {}}}, 'strategies에 없는'),
    ({'max_lanes': 0}, 'max_lanes'),
])
def test_a_wrong_plan_says_what_to_fix(changes, message):
    with pytest.raises(ValueError, match=message):
        lanes.normalize({key: value for key, value in dict(PLAN, **changes).items() if value is not None})


def test_a_strategy_s_screen_settings_give_its_own_trigger_and_trading_time():
    london = {'MAIN_LONDON': {'enabled': True}}
    plan = lanes.normalize(dict(PLAN, triggers=[], special_settings={'SPECIAL2': {'trigger': '무지성 올존', 'time_filters': london}}))
    assert lanes.every_lane(plan) == [{'strategy': 'SPECIAL2', 'trigger': '무지성 올존'}, {'strategy': 'SPECIAL5', 'trigger': None}]
    s, _, _ = lanes.build(plan, lanes.every_lane(plan), ROOT)
    assert s['special_time_filters'] == {'SPECIAL2': london}
    # Listed triggers are the ones compared; the saved trading time still applies.
    plan = lanes.normalize(dict(PLAN, special_settings={'SPECIAL2': {'trigger': '무지성 올존', 'time_filters': london}}))
    assert [lane['trigger'] for lane in lanes.every_lane(plan)] == ['올존', '무지성 올존', '올존', '무지성 올존']
    assert 'special_time_filters' not in lanes.build(lanes.normalize(PLAN), lanes.every_lane(lanes.normalize(PLAN)), ROOT)[0]


@pytest.mark.parametrize('settings,message', [
    ({'SPECIAL9': {'trigger': '올존'}}, 'strategies에 없는'),
    ({'SPECIAL2': {'session': 1}}, 'special_settings는'),
    ({'SPECIAL2': {'trigger': 5}}, 'trigger는'),
    ({'SPECIAL2': {'time_filters': {'MARS': {'enabled': True}}}}, '거래시간'),
])
def test_wrong_screen_settings_are_refused(settings, message):
    with pytest.raises(ValueError, match=message):
        lanes.normalize(dict(PLAN, special_settings=settings))
    with pytest.raises(ValueError, match='시험 전략은 레시피'):
        lanes.normalize(dict(PLAN, strategies=['Test_SPECIAL005'], special_settings={'Test_SPECIAL005': {'trigger': '올존'}}))


def test_registered_strategies_build_one_normal_lanes_request():
    plan = lanes.normalize(dict(PLAN, max_lanes=12, cores=3))
    s, kind, adapter = lanes.build(plan, lanes.every_lane(plan), ROOT)
    assert kind == 'normal' and adapter == {'cores': 3, 'max_lanes': 12}
    assert [lane['strategy'] for lane in s['lanes']] == ['SPECIAL2', 'SPECIAL2', 'SPECIAL5', 'SPECIAL5']
    assert s['strategies'] == ['SPECIAL2'] and s['spread_points'] == {'XAUUSD+': 20.0} and s['available_only'] is True
    assert set(s['virtual_entries']) == {'SPECIAL2', 'SPECIAL5'}


# ---- generated strategies to the job's runner ------------------------------------------------------

def generated_project(tmp_path):
    root = tmp_path / 'project'
    folder = root / 'Part3' / 'TEST_SPECIAL'
    folder.mkdir(parents=True)
    files = []
    for number in (901, 902):
        path = folder / f'Test_SPECIAL{number}.py'
        path.write_text(f'PART3_RECIPE = {{"n": {number}}}\n', encoding='utf-8')
        files.append(path)
    return root, files


def test_the_job_command_carries_every_generated_file_and_its_digest(tmp_path, monkeypatch):
    from lab import backtest_adapters, storage
    root, files = generated_project(tmp_path)
    monkeypatch.setattr(storage, 'generated_path', lambda name: root / 'Part3' / 'TEST_SPECIAL' / name)
    warehouse = tmp_path / 'warehouse'
    job_id = 'e' * 32
    folder = warehouse / 'runs' / job_id
    folder.mkdir(parents=True)
    digests = [hashlib.sha256(path.read_bytes()).hexdigest() for path in files]
    job = {'id': job_id, 'project_root': root, 'warehouse': warehouse, 'folder': folder, 'scenario': {'lanes': []},
           'adapter': {'filename': files[0].name, 'filenames': [path.name for path in files], 'strategy_sha256': digests[0],
                       'strategy_sha256s': digests, 'max_lanes': 12}}
    backtest_adapters.command(job, 'run')
    spec = json.loads((folder / 'generated_request.json').read_text('utf-8'))
    assert spec['strategies'] == [{'path': 'Part3/TEST_SPECIAL/' + path.name, 'sha256': digest} for path, digest in zip(files, digests)]
    assert spec['strategy'] == 'Part3/TEST_SPECIAL/Test_SPECIAL901.py' and spec['max_lanes'] == 12
    files[1].write_text('PART3_RECIPE = {"changed": true}\n', encoding='utf-8')
    with pytest.raises(ValueError, match='변경되었습니다'):
        backtest_adapters.command(job, 'run')


def test_the_runner_registers_every_generated_strategy_of_a_lanes_request(tmp_path, monkeypatch):
    root, files = generated_project(tmp_path)
    recipe = {'schema_version': 2, 'base': 'AI', 'name': 'probe',
              'strategy_intent': {'direction': 'LONG', 'symbols': ['XAUUSD+'], 'order_mode': 'SIMULTANEOUS',
                                  'steps': [{'kind': 'MA_STATE', 'tfs': ['1m'], 'ma_family': 'EMA', 'fast_period': 50,
                                             'slow_period': 200, 'side': 'ABOVE'}],
                                  'final': {'kind': 'OZ', 'tfs': ['1m'], 'validation_mode': 'NORMAL', 'trigger_mode': 'OZ'}}}
    for path in files:
        path.write_text('PART3_RECIPE = ' + repr(recipe) + '\n', encoding='utf-8')
    warehouse = tmp_path / 'warehouse'
    job_id = 'f' * 32
    (warehouse / 'runs' / job_id).mkdir(parents=True)
    monkeypatch.delenv('PART3_BT_REQUEST', raising=False)
    monkeypatch.delenv('MOSES_LOG_DIRECTORY', raising=False)      # initialize sets it; restored after the test
    monkeypatch.setattr(sys, 'path', list(sys.path))               # initialize puts the project's folders first
    monkeypatch.setenv('PART3_BT_PROJECT_ROOT', str(root))
    monkeypatch.setenv('PART3_BT_WAREHOUSE', str(warehouse))
    spec = importlib.util.spec_from_file_location('_backtest_lanes_probe184', ROOT / 'Part3' / 'backtest.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    registered = []
    import event_application
    monkeypatch.setattr(event_application, 'register_strategy_loader',
                        lambda key, loader, dependencies: registered.append((key, loader().PART3_RECIPE['name'])))
    request = {'version': 1, 'project_root': '.', 'warehouse': '.', 'session_id': job_id, 'job_dir': 'runs/' + job_id,
               'strategy': 'Part3/TEST_SPECIAL/' + files[0].name,
               'strategy_sha256': hashlib.sha256(files[0].read_bytes()).hexdigest(),
               'strategies': [{'path': 'Part3/TEST_SPECIAL/' + path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                              for path in files]}
    assert module.initialize(request) == ['Test_SPECIAL901', 'Test_SPECIAL902']
    assert registered == [('Test_SPECIAL901', 'probe'), ('Test_SPECIAL902', 'probe')]
    request['strategies'][1]['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='변경되었습니다'):
        module.initialize(request)


# ---- run again, status, stop -------------------------------------------------------------------------

@pytest.fixture
def stage(tmp_path, monkeypatch):
    """A plan file, a warehouse and fake common jobs (no process is started)."""
    from lab import backtest_jobs
    warehouse = tmp_path / 'warehouse'
    (warehouse / 'runs').mkdir(parents=True)
    plan_file = write(tmp_path / '금_레인.json', PLAN)
    monkeypatch.setattr(lanes, '_context', lambda warehouse_=None: {'warehouse': warehouse, 'project_root': ROOT,
                                                                     'python_executable': sys.executable})
    jobs = {}
    started = []

    def start(request):
        job_id = '%032x' % (len(started) + 1)
        started.append(request)
        jobs[job_id] = {'phase': 'run', 'active': True}
        return {'job_id': job_id, 'kind': request['kind'], 'phase': 'planning'}
    monkeypatch.setattr(backtest_jobs, 'start', start)

    def status(job_id, **context):
        if job_id not in jobs:
            raise ValueError('이 창고에서 실행 기록을 찾지 못했습니다.')
        return {**jobs[job_id], 'message': ''}
    monkeypatch.setattr(backtest_jobs, 'status', status)
    stopped = []
    monkeypatch.setattr(backtest_jobs, 'stop', lambda job_id, **context: stopped.append(job_id))
    return plan_file, warehouse, jobs, started, stopped


def finish(warehouse, job_id, statuses, groups=1, times=()):
    """The runs a Part2 request wrote: its lanes.json and each finished run's result.json."""
    rows = []
    for index, (strategy, trigger, status) in enumerate(statuses):
        run_id = job_id if index == 0 else hashlib.md5(f'{job_id}{index}'.encode()).hexdigest()
        rows.append({'run_id': run_id, 'strategy': strategy, 'trigger': trigger, 'group': 1, 'status': status})
        folder = warehouse / 'runs' / run_id
        folder.mkdir(parents=True, exist_ok=True)
        if status == 'COMPLETE':
            write(folder / 'result.json', {'status': 'COMPLETE', 'alert_statistics': {'total': 3}})
    write(warehouse / 'runs' / job_id / 'lanes.json', {'lead_run_id': job_id, 'groups': groups, 'max_lanes': 36,
                                                       'group_times': list(times), 'runs': rows})
    return rows


def test_a_plan_run_again_skips_its_finished_runs(stage):
    plan_file, warehouse, jobs, started, _ = stage
    first = lanes.start(plan_file)
    assert first['runs'] == 4 and [lane['strategy'] for lane in started[0]['scenario']['lanes']] == ['SPECIAL2'] * 2 + ['SPECIAL5'] * 2
    with pytest.raises(ValueError, match='이미 돌고 있습니다'):
        lanes.start(plan_file)
    # Stopped after its first two runs finished.
    finish(warehouse, first['job_id'], [('SPECIAL2', '올존', 'COMPLETE'), ('SPECIAL2', '무지성 올존', 'COMPLETE'),
                                        ('SPECIAL5', '올존', 'CANCELLED'), ('SPECIAL5', '무지성 올존', 'WAITING')])
    jobs[first['job_id']] = {'phase': 'cancelled', 'active': False}
    second = lanes.start(plan_file)
    assert second['runs'] == 2 and second['skipped'] == 2 and '건너뜀' in second['message']
    assert started[1]['scenario']['lanes'] == [{'strategy': 'SPECIAL5', 'trigger': '올존'}, {'strategy': 'SPECIAL5', 'trigger': '무지성 올존'}]
    record = json.loads(lanes.record_path(plan_file).read_text('utf-8'))
    assert [row['job_id'] for row in next(iter(record['plans'].values()))['requests']] == [first['job_id'], second['job_id']]
    finish(warehouse, second['job_id'], [('SPECIAL5', '올존', 'COMPLETE'), ('SPECIAL5', '무지성 올존', 'COMPLETE')])
    jobs[second['job_id']] = {'phase': 'complete', 'active': False}
    assert lanes.start(plan_file)['job_id'] is None and len(started) == 2
    assert '모두 끝났습니다' in lanes.status(plan_file)['message']


def test_a_removed_request_does_not_block_the_plan(stage):
    import shutil
    plan_file, warehouse, jobs, started, _ = stage
    first = lanes.start(plan_file)
    finish(warehouse, first['job_id'], [('SPECIAL2', '올존', 'COMPLETE'), ('SPECIAL2', '무지성 올존', 'WAITING'),
                                        ('SPECIAL5', '올존', 'WAITING'), ('SPECIAL5', '무지성 올존', 'WAITING')])
    # The user deleted the request's folders from the results list.
    del jobs[first['job_id']]
    shutil.rmtree(warehouse / 'runs' / first['job_id'])
    shown = lanes.status(plan_file)
    assert shown['requests'][0]['phase'] == 'removed' and shown['finished'] == 0 and '0/4' in shown['message']
    assert lanes.stop(plan_file)['job_id'] is None
    assert lanes.start(plan_file)['runs'] == 4 and len(started) == 2


def test_a_changed_plan_is_a_new_plan(stage):
    plan_file, warehouse, jobs, started, _ = stage
    first = lanes.start(plan_file)
    finish(warehouse, first['job_id'], [('SPECIAL2', '올존', 'COMPLETE'), ('SPECIAL2', '무지성 올존', 'COMPLETE'),
                                        ('SPECIAL5', '올존', 'COMPLETE'), ('SPECIAL5', '무지성 올존', 'COMPLETE')])
    jobs[first['job_id']] = {'phase': 'complete', 'active': False}
    write(plan_file, dict(PLAN, spread_points=30))
    assert lanes.start(plan_file)['runs'] == 4


def test_status_tells_the_group_and_the_time_left_and_stop_stops_it(stage):
    plan_file, warehouse, jobs, started, stopped = stage
    first = lanes.start(plan_file)
    now = time.time()
    finish(warehouse, first['job_id'], [('SPECIAL2', '올존', 'COMPLETE'), ('SPECIAL2', '무지성 올존', 'COMPLETE'),
                                        ('SPECIAL5', '올존', 'WAITING'), ('SPECIAL5', '무지성 올존', 'WAITING')],
           groups=2, times=[{'group': 1, 'started': now - 7200, 'ended': now - 600}, {'group': 2, 'started': now - 600, 'ended': None}])
    shown = lanes.status(plan_file)
    assert shown['finished'] == 2 and shown['runs'] == 4
    row = shown['requests'][0]
    assert row['group'] == 2 and row['groups'] == 2 and 5900 <= row['seconds_left'] <= 6000
    assert '묶음 2/2' in shown['message'] and '남은 시간 약 1시간' in shown['message']
    assert lanes.stop(plan_file)['job_id'] == first['job_id'] and stopped == [first['job_id']]


# ---- report ------------------------------------------------------------------------------------------

def trades(folder, rows):
    fields = ['signal_id', 'strategy', 'symbol', 'tf', 'alert_time', 'direction', 'entry_time', 'entry_price', 'stop_price',
              'rr', 'result', 'exit_time', 'r', 'target', 'entry_mode', 'blocked_checks']
    with (folder / 'virtual_trades.csv').open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for alert, rr, result, r in rows:
            writer.writerow({'signal_id': alert, 'strategy': 'S', 'symbol': 'XAUUSD+', 'tf': '1m', 'alert_time': alert,
                             'direction': 'LONG', 'entry_time': alert, 'entry_price': 10, 'stop_price': 9, 'rr': rr,
                             'result': result, 'exit_time': alert + 1, 'r': r, 'target': '', 'entry_mode': 'CONFIRM',
                             'blocked_checks': 0})


def test_the_report_has_its_tables_and_warns_of_a_zero_spread(stage):
    from event_backtest.settings import milliseconds
    plan_file, warehouse, jobs, _, _ = stage
    write(plan_file, dict(PLAN, triggers=['올존'], spread_points=0))
    first = lanes.start(plan_file)
    rows = finish(warehouse, first['job_id'], [('SPECIAL2', '올존', 'COMPLETE'), ('SPECIAL5', '올존', 'COMPLETE')])
    start, end = milliseconds(PLAN['start']), milliseconds(PLAN['end'])
    early, late = start + 1000, end - 1000
    # 40 trades at RR 2 each, the first 20 in the plan's first 70% and the rest in its last 30%.
    # SPECIAL2 wins 3 of every 5 in both halves (+0.80R); SPECIAL5 wins its first 12 only (−0.10R, late half −1R).
    won = {'SPECIAL2': lambda i: i % 5 < 3, 'SPECIAL5': lambda i: i < 12}
    for row in rows:
        folder = warehouse / 'runs' / row['run_id']
        wins = won[row['strategy']]
        sample = [(early if i < 20 else late, 2.0, 'WIN' if wins(i) else 'LOSS', 2.0 if wins(i) else -1.0) for i in range(40)]
        trades(folder, sample)
        write(folder / 'result.json', {'status': 'COMPLETE', 'alert_statistics': {'total': 50},
                                       'virtual_entry': {'trades_csv': f'runs/{row["run_id"]}/virtual_trades.csv',
                                                         'pricing': {'spread_price': 0.0}}})
    jobs[first['job_id']] = {'phase': 'complete', 'active': False}
    text = lanes.report(plan_file)
    assert '스프레드 0으로 돌렸습니다' in text and lanes.report_path(plan_file).read_text('utf-8') == text
    top = text.split('## 평균 R 상위 10')[1].split('##')[0]
    assert top.index('SPECIAL2') < top.index('SPECIAL5')
    # mean 0.8, sample sd 1.488 → t = √40 · 0.8 / 1.488 = 3.40; spread 0, so twice the cost is the same.
    assert '| SPECIAL2 | 올존 | 2 | 40 | +0.80 | +3.40 | 60% | +0.80 → +0.80 | +0.80 |' in top
    assert '| SPECIAL5 | 올존 | 2 | 40 | -0.10 |' in top and '+0.80 → -1.00' in top
    steady = text.split('## 꾸준한 조합 10')[1].split('##')[0]
    assert 'SPECIAL2' in steady and 'SPECIAL5' not in steady


def test_twice_the_cost_takes_the_spread_off_once_more(stage):
    plan_file, warehouse, jobs, _, _ = stage
    write(plan_file, dict(PLAN, strategies=['SPECIAL2'], triggers=['올존'], spread_points=20))
    first = lanes.start(plan_file)
    row, = finish(warehouse, first['job_id'], [('SPECIAL2', '올존', 'COMPLETE')])
    folder = warehouse / 'runs' / row['run_id']
    # The run's R already has the 0.2 spread off (stop 1 away): +1.8 a win, −1.2 a loss.
    trades(folder, [(i, 2.0, 'WIN' if i % 5 < 3 else 'LOSS', 1.8 if i % 5 < 3 else -1.2) for i in range(40)]
           + [(40, 2.0, 'UNCERTAIN', ''), (41, 2.0, 'UNCLOSED', '')])
    write(folder / 'result.json', {'status': 'COMPLETE', 'virtual_entry': {
        'trades_csv': f'runs/{row["run_id"]}/virtual_trades.csv', 'pricing': {'spread_price': 0.2}}})
    jobs[first['job_id']] = {'phase': 'complete', 'active': False}
    _, found = lanes.rows(plan_file)
    assert len(found) == 1 and found[0]['all']['trades'] == 40          # a same-bar or open trade is not counted
    assert found[0]['all']['average_r'] == pytest.approx(0.6) and found[0]['double_cost']['average_r'] == pytest.approx(0.4)
    text = lanes.report(plan_file)
    assert '스프레드 20포인트' in text and '스프레드 0으로' not in text


# ---- Part3's AI ------------------------------------------------------------------------------------

def test_an_ai_plan_is_kept_in_the_warehouse_and_asked_again_skips_its_finished_runs(stage):
    _, warehouse, jobs, started, _ = stage
    first = lanes.start_saved(dict(PLAN))
    kept = sorted((warehouse / 'runs' / 'lanes').glob('*.json'))
    assert [path.name.count('.') for path in kept] == [1, 2]          # <plan>.json and <plan>.lanes.json
    assert json.loads(kept[0].read_text('utf-8'))['strategies'] == PLAN['strategies']
    finish(warehouse, first['job_id'], [('SPECIAL2', '올존', 'COMPLETE'), ('SPECIAL2', '무지성 올존', 'CANCELLED'),
                                        ('SPECIAL5', '올존', 'WAITING'), ('SPECIAL5', '무지성 올존', 'WAITING')])
    jobs[first['job_id']] = {'phase': 'cancelled', 'active': False}
    second = lanes.start_saved(dict(PLAN))
    assert second['runs'] == 3 and second['skipped'] == 1 and len(started) == 2
    # Its report is of the whole plan, by either request's job ID.
    finish(warehouse, second['job_id'], [('SPECIAL2', '무지성 올존', 'COMPLETE'), ('SPECIAL5', '올존', 'COMPLETE'),
                                         ('SPECIAL5', '무지성 올존', 'COMPLETE')])
    jobs[second['job_id']] = {'phase': 'complete', 'active': False}
    text = lanes.job_report(first['job_id'])
    assert '실행 4개 모두 끝남' in text and text == lanes.job_report(second['job_id'])


def test_any_finished_request_has_its_tables_by_job_id(stage):
    _, warehouse, _, _, _ = stage
    scenario = {'symbol': 'XAUUSD+', 'start': '2026-01-01', 'end': '2026-02-01', 'mode': 'BAR', 'result_mode': 'ALERT_ONLY',
                'spread_points': {'XAUUSD+': 20}, 'strategies': ['SPECIAL2'], 'triggers': {}}
    # A lanes request started elsewhere (its own lanes.json), and one ordinary run.
    finish(warehouse, 'a' * 32, [('SPECIAL2', '올존', 'COMPLETE'), ('SPECIAL5', None, 'CANCELLED')])
    write(warehouse / 'runs' / ('a' * 32) / 'result.json', {'status': 'COMPLETE', 'scenario': scenario,
                                                          'alert_statistics': {'total': 7}})
    text = lanes.job_report('a' * 32)
    assert '끝난 실행 1/2' in text and '| SPECIAL2 | 올존 | 7 |' in text
    single = warehouse / 'runs' / ('b' * 32)
    single.mkdir()
    write(single / 'result.json', {'status': 'COMPLETE', 'scenario': scenario, 'alert_statistics': {'total': 5}})
    assert '| SPECIAL2 | 브레이커 올존 | 5 |' in lanes.job_report('b' * 32)      # its recipe's trigger
    write(single / 'result.json', {'status': 'COMPLETE', 'scenario': {**scenario, 'build_only': True}})
    with pytest.raises(ValueError, match='결과 표가 없습니다'):
        lanes.job_report('b' * 32)
    with pytest.raises(ValueError, match='아직 없습니다'):
        lanes.job_report('c' * 32)


def test_the_result_names_each_run_of_the_request_by_its_strategy(tmp_path):
    from lab import unified_backtest
    ids = ['a' * 32, 'b' * 32]
    for run_id, name, trigger in zip(ids, ('SPECIAL2', 'SPECIAL8'), ('무지성 올존', None)):
        (tmp_path / run_id).mkdir()
        write(tmp_path / run_id / 'result.json', {'scenario': {'strategies': [name], 'triggers': {}},
                                                  'tested_with': [other for other in ids if other != run_id],
                                                  'applied_special_settings': {name: {'trigger': trigger}}})
    data = json.loads((tmp_path / ids[0] / 'result.json').read_text('utf-8'))
    assert unified_backtest._tested_strategy(data) == 'SPECIAL2'
    assert unified_backtest._compared_runs({'folder': tmp_path / ids[0]}, data) == [
        {'run_id': ids[1], 'base_frame': None, 'trigger': None, 'strategy': 'SPECIAL8'}]
    assert unified_backtest._tested_strategy({'scenario': {'strategies': ['SPECIAL2', 'SPECIAL8']}}) is None


# ---- the command line and the common job ----------------------------------------------------------

def load_cli():
    spec = importlib.util.spec_from_file_location('_cli_lanes_probe184', ROOT / 'Part3' / 'cli.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_command_line_runs_each_lanes_action_on_the_plan_file(stage, monkeypatch, capsys):
    plan_file, warehouse, jobs, started, _ = stage
    cli = load_cli()
    followed = []
    monkeypatch.setattr(cli, '_follow', lambda job_id, context, approve=False, done_text=None:
                        followed.append((job_id, approve, done_text)) or 0)
    monkeypatch.setattr(lanes, 'preview', lambda plan, warehouse=None: {'runs': 4, 'message': 'preview'})
    monkeypatch.setattr(sys, 'argv', ['cli.py', 'lanes', 'plan', str(plan_file)])
    assert cli.main() == 0 and json.loads(capsys.readouterr().out)['message'] == 'preview' and not started
    monkeypatch.setattr(sys, 'argv', ['cli.py', 'lanes', 'run', str(plan_file)])
    assert cli.main() == 0 and len(started) == 1
    job_id = next(iter(jobs))
    # A recording to build is never approved by the command: its user is asked, or it waits.
    assert followed == [(job_id, False, '끝났습니다. lanes report ' + str(plan_file) + ' 로 결과를 보세요.')]
    assert '실행 4개를 시작했습니다' in capsys.readouterr().out
    monkeypatch.setattr(sys, 'argv', ['cli.py', 'lanes', 'status', str(plan_file)])
    assert cli.main() == 0 and '돌고 있습니다' in json.loads(capsys.readouterr().out)['message']
    finish(warehouse, job_id, [('SPECIAL2', '올존', 'COMPLETE'), ('SPECIAL2', '무지성 올존', 'COMPLETE'),
                               ('SPECIAL5', '올존', 'COMPLETE'), ('SPECIAL5', '무지성 올존', 'COMPLETE')])
    jobs[job_id] = {'phase': 'complete', 'active': False}
    monkeypatch.setattr(sys, 'argv', ['cli.py', 'lanes', 'run', str(plan_file)])
    assert cli.main() == 0 and len(started) == 1 and len(followed) == 1
    assert '모두 끝났습니다' in capsys.readouterr().out


def test_the_common_job_carries_the_group_size_and_names_every_lane(tmp_path):
    from lab import backtest_jobs
    job = {'kind': 'normal', 'python_executable': 'python', 'scenario_path': tmp_path / 'web_scenario.json',
           'warehouse': tmp_path, 'id': 'a' * 32, 'project_root': ROOT, 'adapter': {'max_lanes': 12}}
    args, _, _ = backtest_jobs.command(job, 'run')
    assert args[args.index('--max-lanes') + 1] == '12'
    assert '--max-lanes' not in backtest_jobs.command(dict(job, adapter={}), 'run')[0]
    s = {'strategies': ['SPECIAL2'], 'lanes': lanes.every_lane(lanes.normalize(PLAN))}
    assert backtest_jobs._source({'adapter': {}}, s) == '레인 4개: SPECIAL2, SPECIAL5'
    assert backtest_jobs._source({'adapter': {}}, {'strategies': ['SPECIAL2']}) == 'SPECIAL2'
