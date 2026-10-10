"""Release boundaries using fake terminal files and verified synthetic captures."""
from pathlib import Path
from unittest.mock import patch
from contextlib import contextmanager
import datetime as dt
import json
import shutil
import sys
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part1/program'), str(ROOT / 'Part2')]
from event_backtest import build_plan, recording, terminal_lifecycle, deployment_recovery
from event_backtest.settings import scenario, file_hash, relative_path, digest
from event_backtest.capture_layout import capture_parent, symbol_folder, folder_symbol, check_destination
from event_backtest.calendar import warm_start
from event_backtest.warehouse import Warehouse


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    terminal = tmp_path / 'terminal'
    terminal.mkdir()
    profile = {'data_root': str(terminal), 'executable': str(terminal / 'terminal64.exe')}
    target = terminal / 'MQL5/Experts/THE_STAFF_OF_MOSES.ex5'
    added = terminal / 'MQL5/Experts/new_header.mqh'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'user original')
    warehouse = tmp_path / 'warehouse'
    build = warehouse / 'builds/source'
    build.mkdir(parents=True)
    (build / target.name).write_bytes(b'tester build')
    (build / added.name).write_bytes(b'added header')
    (build / 'ready.json').write_text(json.dumps({'files': {
        p.name: file_hash(p) for p in build.iterdir()}}), encoding='utf-8')
    calls = []
    monkeypatch.setattr(recording, 'deployment_targets', lambda p: [target, added])
    monkeypatch.setattr(recording, 'source_hash', lambda: 'source')
    monkeypatch.setattr(recording.native, '_stop_selected_terminal', lambda *a, **k: calls.append('stop'))
    monkeypatch.setattr(recording.native, '_restart_selected_terminal', lambda *a, **k: calls.append('restart') or {})
    monkeypatch.setattr(terminal_lifecycle, 'wait_closed', lambda *a, **k: None)
    return profile, target, added, warehouse, calls


def install(deployment, name):
    profile, target, added, warehouse, calls = deployment
    return recording.installed_build(profile, warehouse / 'build_runs' / name,
                                     lambda *a: None, lambda: None, build_root=warehouse / 'builds')


@pytest.mark.parametrize('failure', [RuntimeError('close timeout'), OSError('process lookup failed')])
def test_failed_close_retains_original_mapping_and_retry_restores(deployment, failure):
    profile, target, added, warehouse, calls = deployment
    with patch.object(terminal_lifecycle, 'wait_closed', side_effect=[None, failure]):
        with pytest.raises(type(failure)):
            with install(deployment, 'first'):
                assert target.read_bytes() == b'tester build'
                assert added.is_file()
    journal = warehouse / 'build_runs/first/restore_pending.json'
    assert journal.is_file() and calls == ['stop']
    data = json.loads(journal.read_text('utf-8'))
    assert str(warehouse) not in json.dumps(data) and str(profile['data_root']) not in json.dumps(data)
    assert data['files'][0]['target'] == 'MQL5/Experts/THE_STAFF_OF_MOSES.ex5'
    with install(deployment, 'second'):
        assert (warehouse / 'build_runs/second/original_0').read_bytes() == b'user original'
        assert json.loads((warehouse / 'build_runs/second/restore_pending.json').read_text('utf-8'))['files'][1]['backup'] is None
    assert target.read_bytes() == b'user original' and not added.exists()
    assert not journal.exists() and calls == ['stop', 'stop', 'restart']


def test_pending_recovery_survives_warehouse_relocation(deployment, tmp_path):
    with patch.object(terminal_lifecycle, 'wait_closed', side_effect=[None, RuntimeError('timeout')]):
        with pytest.raises(RuntimeError):
            with install(deployment, 'first'):
                pass
    profile, target, added, warehouse, calls = deployment
    relocated = tmp_path / 'relocated'
    assert warehouse.resolve().is_relative_to(tmp_path.resolve())
    assert relocated.resolve().is_relative_to(tmp_path.resolve())
    warehouse.rename(relocated)
    with install((profile, target, added, relocated, calls), 'retry'):
        assert (relocated / 'build_runs/retry/original_0').read_bytes() == b'user original'
    assert target.read_bytes() == b'user original' and not added.exists()


@pytest.mark.parametrize('bad', ['backup_corrupt', 'backup_escape', 'target_escape'])
def test_invalid_recovery_never_overwrites_original_backup(deployment, bad):
    with patch.object(terminal_lifecycle, 'wait_closed', side_effect=[None, RuntimeError('timeout')]):
        with pytest.raises(RuntimeError):
            with install(deployment, 'first'):
                pass
    profile, target, added, warehouse, calls = deployment
    folder = warehouse / 'build_runs/first'
    journal = folder / 'restore_pending.json'
    data = json.loads(journal.read_text('utf-8'))
    if bad == 'backup_corrupt':
        (folder / 'original_0').write_bytes(b'corrupt')
    elif bad == 'backup_escape':
        data['files'][0]['backup'] = '../outside'
    else:
        data['files'][0]['target'] = '../outside'
    journal.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError):
        with install(deployment, 'retry'):
            pytest.fail('invalid pending recovery must block replacement')
    assert journal.exists() and target.read_bytes() == b'tester build'
    assert not (warehouse / 'build_runs/retry/original_0').exists()
    assert 'restart' not in calls


def test_initial_process_lookup_failure_never_touches_installed_files(deployment):
    with patch.object(terminal_lifecycle, 'wait_closed', side_effect=OSError('lookup failed')):
        with pytest.raises(OSError):
            with install(deployment, 'first'):
                pytest.fail('closure must be verified first')
    profile, target, added, warehouse, calls = deployment
    assert target.read_bytes() == b'user original' and not added.exists()
    assert not (warehouse / 'build_runs/first/restore_pending.json').exists()


def test_reusing_completed_captures_still_recovers_pending_installation(deployment, plans, monkeypatch):
    with patch.object(terminal_lifecycle, 'wait_closed', side_effect=[None, RuntimeError('timeout')]):
        with pytest.raises(RuntimeError):
            with install(deployment, 'first'):
                pass
    profile, target, added, warehouse, calls = deployment
    rows = [capture('2026-09-01', '2026-10-01', unit='MONTH')]
    catalog = Catalog(warehouse, rows)
    catalog.close = lambda: None
    monkeypatch.setattr(recording, 'Warehouse', lambda root: catalog)
    monkeypatch.setattr(recording.native, 'run_native_tester', lambda *a, **k: pytest.fail('already captured'))
    request = scenario(start='2026-09-01', end='2026-10-01', strategies=['SPECIAL1'], overlap_trading_days=0)
    assert recording.prepare(request, warehouse, profile=profile) == rows
    assert target.read_bytes() == b'user original' and not added.exists()
    assert calls == ['stop', 'stop', 'restart']


@pytest.mark.parametrize('symbol', ['#USNDAQ100', 'XAU/USD', '../outside', '.', '..',
                                  'CON', 'NUL.txt', 'COM1', 'Gold.', '~CON', '~~2F',
                                  '거래종목', '😀' * 64, ':' * 64, '\\drive', 'XAUUSD+', 'GOLDm'])
def test_logical_symbol_roundtrip_stays_within_warehouse(tmp_path, symbol):
    request = scenario(symbol=symbol, strategies=['SPECIAL1'])
    parent = capture_parent(tmp_path, request['symbol'], 'BAR', '2026-09-01')
    folder = parent.relative_to(tmp_path).parts[1]
    assert parent.resolve().is_relative_to(tmp_path.resolve())
    assert folder_symbol(folder) == symbol
    assert len(folder.encode('utf-16-le')) // 2 <= 255
    # The full path also depends on the chosen warehouse and Windows long-path
    # support. Probe the exact final layout before any MT5 operation.
    try:
        check_destination(tmp_path, symbol, 'BAR', '2026-09-01')
    except ValueError as exc:
        assert '녹화는 시작하지 않았습니다' in str(exc)
    assert not (tmp_path / 'captures').exists()


def test_failed_final_probe_is_cleaned_and_relocation_can_retry(tmp_path, monkeypatch):
    root = tmp_path / 'warehouse'
    root.mkdir()
    marker = root / 'user_file.txt'
    marker.write_text('keep', encoding='ascii')
    read_bytes = Path.read_bytes

    def unavailable(path):
        if path.name == 'tester_tick_evidence.log' and len(path.parent.name) == 97:
            raise OSError('final path unavailable')
        return read_bytes(path)

    with monkeypatch.context() as probe:
        probe.setattr(Path, 'read_bytes', unavailable)
        with pytest.raises(ValueError, match='녹화는 시작하지 않았습니다'):
            check_destination(root, 'XAUUSD+', 'BAR', '2026-09-01')
    assert list(root.iterdir()) == [marker]
    relocated = tmp_path / 'moved'
    root.rename(relocated)
    check_destination(relocated, 'XAUUSD+', 'BAR', '2026-09-01')
    assert list(relocated.iterdir()) == [relocated / marker.name]
    assert (relocated / marker.name).read_text('ascii') == 'keep'


def test_long_final_probe_leaves_no_files_on_success_or_rejection(tmp_path):
    # Use the real OS boundary: the final file exceeds legacy MAX_PATH, while
    # the initial staging path is shorter. Both supported and rejected roots
    # must leave no probe that later prevents verifier/warehouse cleanup.
    root = tmp_path / 'warehouse'
    root.mkdir()
    final = capture_parent(root, 'XAUUSD+', 'BAR', '2026-09-01') / ('0' * 64 + '_' + 'a' * 32) / 'tester_tick_evidence.log'
    if len(str(final.resolve())) <= 260:
        root = root / ('w' * (261 - len(str(final.resolve()))))
        root.mkdir()
    try:
        check_destination(root, 'XAUUSD+', 'BAR', '2026-09-01')
    except ValueError as exc:
        assert '녹화는 시작하지 않았습니다' in str(exc)
    assert not list(root.iterdir())


def test_symbol_escaping_has_no_escape_or_reserved_name_collisions():
    symbols = ['CON', '~CON', '/', '~2F', '~~2F', '..', '~..', 'XAU/USD', 'XAU~2FUSD', 'Gold.', 'Gold~2E']
    assert len({symbol_folder(symbol).casefold() for symbol in symbols}) == len(symbols)
    for bad in ['', 'bad name', 'bad,symbol', '\n', 'x' * 65]:
        with pytest.raises(ValueError):
            symbol_folder(bad)


def test_typed_logical_symbols_do_not_disable_path_validation():
    from event_backtest.portable import validate
    assert validate({'symbol': '../logical', 'tester_symbol': '/broker', 'path': 'captures/safe'})
    for value in ({'path': '../outside'}, {'path': '/outside'}, {'other': '../outside'},
                  {'symbol': {'path': '../outside'}}, {'tester_symbol': 'bad name'}):
        with pytest.raises(ValueError):validate(value)


def test_destination_failure_aborts_before_terminal_or_recording(plans, tmp_path, monkeypatch):
    request = scenario(start='2026-09-01', end='2026-10-01', strategies=['SPECIAL1'], overlap_trading_days=0)
    catalog = Catalog(tmp_path)
    catalog.close = lambda: None
    monkeypatch.setattr(recording, 'Warehouse', lambda root: catalog)
    plan = build_plan.make_plan(request, catalog)
    monkeypatch.setattr(recording, 'check_destination', lambda *a: (_ for _ in ()).throw(ValueError('unsupported destination')))
    monkeypatch.setattr(recording.native, '_stop_selected_terminal', lambda *a, **k: pytest.fail('must not stop MT5'))
    monkeypatch.setattr(recording.native, 'run_native_tester', lambda *a, **k: pytest.fail('must not record'))
    with pytest.raises(ValueError, match='unsupported destination'):
        recording.prepare(request, tmp_path, profile={}, plan=plan, approved_token=plan['approval_token'])


def test_capture_parent_rejects_link_outside_warehouse(tmp_path, monkeypatch):
    # Resolve is the platform-independent guard also used for Windows junctions.
    real_resolve = Path.resolve
    root = tmp_path / 'warehouse'
    escaped = root / 'captures/XAUUSD+/BAR/2026/09'
    outside = tmp_path / 'outside'
    monkeypatch.setattr(Path, 'resolve', lambda self, *a, **k: outside if self == escaped else real_resolve(self, *a, **k))
    with pytest.raises(ValueError, match='escape'):
        capture_parent(root, 'XAUUSD+', 'BAR', '2026-09-01')


class Catalog:
    def __init__(self, root, rows=(), broken=()):
        self.root, self.rows, self.broken = root, list(rows), set(broken)
    def available(self, *args):
        return self.rows
    def find_capture(self, key):
        return next((row for row in self.rows if row['capture_id'] == key and key not in self.broken), None)


def capture(start, end=None, **updates):
    day = dt.date.fromisoformat(start)
    return {'capture_id': start, 'start': start,
            'end': end or (day + dt.timedelta(days=1)).isoformat(), 'unit': 'DAY',
            'ea_build_hash': 'test-ea', 'schema_id': 50, 'timer_ms': 1000,
            'storage': 'MSD2', 'reconstruction_verified': True, 'observed_days': [start],
            **updates}


@pytest.fixture
def plans(monkeypatch):
    monkeypatch.setattr(build_plan, 'current_build', lambda root: 'test-ea')
    monkeypatch.setattr(build_plan, 'schema_id', lambda: 50)


def test_month_rollover_reuses_all_verified_days(plans, tmp_path):
    rows = [capture(f'2026-09-{day:02d}') for day in range(1, 31)]
    request = scenario(start='2026-09-01', end='2026-10-01', strategies=['SPECIAL1'], overlap_trading_days=0)
    plan = build_plan.make_plan(request, Catalog(tmp_path, rows), today=dt.date(2026, 10, 3))
    assert not plan['record'] and plan['reuse'] == rows


@pytest.mark.parametrize('problem', ['missing', 'corrupt', 'build', 'schema', 'timer', 'history'])
def test_month_rollover_records_only_unusable_day(plans, tmp_path, problem):
    rows = [capture(f'2026-09-{day:02d}') for day in range(1, 31)]
    broken = []
    if problem == 'missing':rows.pop(14)
    if problem == 'corrupt':broken = ['2026-09-15']
    if problem == 'build':rows[14]['ea_build_hash'] = 'unknown'
    if problem == 'schema':rows[14]['schema_id'] = 99
    if problem == 'timer':rows[14]['timer_ms'] = 2000
    if problem == 'history':rows[14]['history_missing'] = ['gap']
    request = scenario(start='2026-09-01', end='2026-10-01', strategies=['SPECIAL1'], overlap_trading_days=0)
    plan = build_plan.make_plan(request, Catalog(tmp_path, rows, broken), today=dt.date(2026, 10, 3))
    assert plan['record'] == [{'start': '2026-09-15', 'end': '2026-09-16', 'unit': 'DAY'}]
    assert len(plan['reuse']) == 29


def test_monthly_capture_supersedes_contained_daily_alternatives(plans, tmp_path):
    month = capture('2026-09-01', '2026-10-01', capture_id='month', unit='MONTH')
    rows = [capture(f'2026-09-{day:02d}') for day in range(1, 31)] + [month]
    request = scenario(start='2026-09-01', end='2026-10-01', strategies=['SPECIAL1'], overlap_trading_days=0)
    plan = build_plan.make_plan(request, Catalog(tmp_path, rows), today=dt.date(2026, 10, 3))
    assert plan['reuse'] == [month] and not plan['record']


def test_partial_overlaps_fail_before_duplicating_any_input(plans, tmp_path):
    rows = [capture('2026-09-01', '2026-09-20'), capture('2026-09-15', '2026-10-01')]
    request = scenario(start='2026-09-01', end='2026-10-01', strategies=['SPECIAL1'], overlap_trading_days=0)
    with pytest.raises(ValueError, match='겹치는 부분'):
        build_plan.make_plan(request, Catalog(tmp_path, rows), today=dt.date(2026, 10, 3))


def test_observed_holiday_shortage_expands_then_requires_new_approval(plans, tmp_path):
    request = scenario(start='2026-12-28', end='2026-12-29', strategies=['SPECIAL1'], overlap_trading_days=3)
    catalog = Catalog(tmp_path)
    today = dt.date(2026, 12, 30)
    first = build_plan.make_plan(request, catalog, today=today)
    assert first['record'][0]['start'] == '2026-12-23'
    catalog.rows = [capture(row['start'], row['end'], observed_days=[row['start']] if row['start'] in
                            ('2026-12-23', '2026-12-24', '2026-12-28') else []) for row in first['record']]
    second = build_plan.make_plan(request, catalog, today=today)
    assert second['record'] == [{'start': '2026-12-22', 'end': '2026-12-23', 'unit': 'DAY'}]
    with pytest.raises(build_plan.ConfirmationRequired):
        build_plan.require_approval(second, first['approval_token'])
    build_plan.require_approval(second, second['approval_token'])
    # Another holiday extends further instead of repeatedly asking for the same day.
    catalog.rows.append(capture('2026-12-22', observed_days=[]))
    third = build_plan.make_plan(request, catalog, today=today)
    assert third['record'][0]['start'] == '2026-12-21'
    catalog.rows.append(capture('2026-12-21'))
    final = build_plan.make_plan(request, catalog, today=today)
    assert not final['record']
    assert warm_start(request['start'], 3, final['reuse']) == '2026-12-21'


def test_seven_day_market_keeps_actual_weekend_warmup(plans, tmp_path):
    request = scenario(start='2026-12-28', end='2026-12-29', strategies=['SPECIAL1'], overlap_trading_days=3)
    rows = [capture(f'2026-12-{day}') for day in range(23, 29)]
    plan = build_plan.make_plan(request, Catalog(tmp_path, rows), today=dt.date(2026, 12, 30))
    assert not plan['record'] and warm_start(request['start'], 3, plan['reuse']) == '2026-12-25'


def test_explicit_rebuild_also_expands_observed_holiday_shortage(plans, tmp_path):
    request = scenario(start='2026-12-28', end='2026-12-29', strategies=['SPECIAL1'], overlap_trading_days=3)
    rows = [capture(f'2026-12-{day}', observed_days=[f'2026-12-{day}'] if day in (23, 24, 28) else [])
            for day in range(23, 29)]
    plan = build_plan.make_plan(request, Catalog(tmp_path, rows), rebuild=True, today=dt.date(2026, 12, 30))
    assert plan['rebuild'] is True and not plan['reuse']
    assert plan['record'][0]['start'] == '2026-12-22'
    assert plan['warmup_extension']['required_days'] == 3


@pytest.mark.parametrize('rebuild', [False, True])
def test_prepare_requests_only_newly_discovered_warmup_with_new_approval(plans, tmp_path, monkeypatch, rebuild):
    warehouse = tmp_path / 'w'
    common = tmp_path / 'c'
    observed = {'2026-12-22', '2026-12-23', '2026-12-24', '2026-12-28'}
    recorded = []
    operations = []
    fragments = build_plan.fragments
    monkeypatch.setattr(build_plan, 'fragments', lambda start, end, today=None: fragments(start, end, dt.date(2026, 12, 30)))
    # This fixture simulates December; date clipping and partitioning share its clock.
    from event_backtest import periods
    limit_end=periods.limit_end
    monkeypatch.setattr(periods,'limit_end',lambda s,today=None:limit_end(s,dt.date(2026,12,30)))
    monkeypatch.setattr(recording, 'source_hash', lambda: 'test-source')
    monkeypatch.setattr(recording, 'deployment_targets', lambda profile: [])
    monkeypatch.setattr(recording, 'journal_positions', lambda profile: {})
    monkeypatch.setattr(recording, 'tick_evidence', lambda *a: {'actual': 'REAL_TICKS', 'journal_lines': ['XAUUSD+ real ticks used']})
    monkeypatch.setattr(recording.native, 'common_files_root', lambda: common)

    @contextmanager
    def installed(*a, **k):
        operations.append('install')
        try:yield 'test-ea'
        finally:operations.append('restore')

    def launch(profile, symbol, start_ns, end_ns, work, **kwargs):
        start = dt.datetime.fromtimestamp(start_ns / 1e9, dt.timezone.utc).date().isoformat()
        recorded.append(start)
        source = common / 'MosesDataBuild' / start
        source.mkdir(parents=True)
        return {'export': str(source), 'session': start, 'elapsed_seconds': 0}

    def convert(source, parent, key, **kwargs):
        dest = parent / (key + '_' + uuid.uuid4().hex[:6])
        dest.mkdir(parents=True)
        (dest / 'complete.txt').write_text('verified', encoding='ascii')
        return dest, {'storage': 'MSD2', 'reconstruction_verified': True}

    monkeypatch.setattr(recording, 'installed_build', installed)
    monkeypatch.setattr(recording.native, 'run_native_tester', launch)
    monkeypatch.setattr(terminal_lifecycle, 'launch_once_retry', lambda fn, *a, **k: fn())
    monkeypatch.setattr('event_backtest.storage.convert', convert)
    monkeypatch.setattr('event_backtest.calendar.capture_calendar', lambda source: {
        'observed_days': [source.name] if source.name in observed else []})
    request = scenario(start='2026-12-28', end='2026-12-29', strategies=['SPECIAL1'], overlap_trading_days=3)
    catalog = Warehouse(warehouse)
    if rebuild:
        # Stale metadata promises enough warmup even though its old files no
        # longer exist. Rebuild must not read them, and must check fresh days.
        for piece in fragments('2026-12-23', request['end'], dt.date(2026, 12, 30)):
            identity = {**piece, 'symbol': request['symbol'], 'mode': 'BAR',
                        'ea_build_hash': 'test-ea', 'schema_id': 50, 'timer_ms': 1000, 'tester_model': 4}
            catalog.register({**capture(piece['start'], piece['end']), **identity,
                              'capture_id': digest(identity), 'path': 'old_missing/' + piece['start'],
                              'files': {}, 'stored_bytes': 0, 'recorded_at': '2026-01-01T00:00:00+00:00',
                              'tick_evidence': {'actual': 'REAL_TICKS'}})
        monkeypatch.setattr(Warehouse, 'find_capture', lambda *a, **k: pytest.fail('rebuild read old files'))
    first = build_plan.make_plan(request, catalog, rebuild=rebuild)
    catalog.close()
    with pytest.raises(build_plan.ConfirmationRequired) as caught:
        recording.prepare(request, warehouse, profile={}, plan=first, approved_token=first['approval_token'], rebuild=rebuild)
    second = caught.value.plan
    assert second['record'][0] == {'start': '2026-12-22', 'end': '2026-12-23', 'unit': 'DAY'}
    assert len(second['record']) == (7 if rebuild else 1)
    assert recorded == [f'2026-12-{day}' for day in range(23, 29)]
    assert operations == ['install', 'restore']
    with pytest.raises(build_plan.ConfirmationRequired):
        recording.prepare(request, warehouse, profile={}, approved_token=first['approval_token'], rebuild=rebuild)
    assert operations == ['install', 'restore']
    result = recording.prepare(request, warehouse, profile={}, plan=second, approved_token=second['approval_token'], rebuild=rebuild)
    assert recorded[6:] == ([f'2026-12-{day}' for day in range(22, 29)] if rebuild else ['2026-12-22'])
    assert warm_start(request['start'], 3, result) == '2026-12-22'
    assert operations == ['install', 'restore', 'install', 'restore']


def test_encoded_symbol_catalog_survives_warehouse_move(tmp_path):
    root = tmp_path / 'warehouse'
    symbol = '#USNDAQ100'
    folder = capture_parent(root, symbol, 'BAR', '2026-09-01') / 'piece'
    folder.mkdir(parents=True)
    complete = folder / 'complete.txt'
    complete.write_text('verified', encoding='ascii')
    row = capture('2026-09-01', symbol=symbol, mode='BAR', path=relative_path(root, folder),
                  files={'complete.txt': file_hash(complete)}, stored_bytes=8,
                  recorded_at='2026-10-03T00:00:00+00:00', tick_evidence={'actual': 'REAL_TICKS'})
    db = Warehouse(root)
    db.register(row)
    db.close()
    relocated = tmp_path / 'relocated'
    shutil.copytree(root, relocated)
    db = Warehouse(relocated)
    try:
        assert db.find_capture(row['capture_id']) == row
        assert folder_symbol(Path(row['path']).parts[1]) == symbol
    finally:
        db.close()
