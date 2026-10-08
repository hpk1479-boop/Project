"""158: a broken SPECIAL file is left out with a one-line reason; the other strategies still start.

The reason reaches the live start result (screen), the strategy settings popups and one Telegram
notice per engine start. Saved settings of a left-out strategy are kept until its file is fixed.
"""
import copy
import csv
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part1/program'), str(ROOT / 'Part3'), str(ROOT / 'Part2')]
from strategy_recipe import registry, special_files, user_catalog


@pytest.fixture
def project(tmp_path, monkeypatch):
    for part in ('Part1/program', 'Part2', 'Part3', 'settings'):
        (tmp_path / part).mkdir(parents=True)
    target = tmp_path / 'settings/strategy_registry.json'
    target.write_bytes(registry.REGISTRY.read_bytes())
    monkeypatch.setattr(registry, 'REGISTRY', target)
    folder = tmp_path / special_files.SPECIAL_DIRECTORY
    folder.mkdir(parents=True)
    original = json.loads(target.read_text('utf-8-sig'))['presets']
    for entry in original:
        user_catalog.write_json(folder / (entry['id'] + '.recipe.json'), entry)
    return NS(root=tmp_path, folder=folder, original=original, ids={row['id'] for row in original})


def bad_intent(original):
    entry = copy.deepcopy(original[0])
    entry['id'] = 'SPECIAL8'
    entry['recipe']['strategy_intent']['final'] = {'kind': 'NOT_A_FINAL'}
    return json.dumps(entry, ensure_ascii=False)


DEFECTS = [
    ('SPECIAL8.recipe.json', lambda original: '{"id": "SPECIAL8",', 'SPECIAL8', 'JSON 형식 오류 1행'),
    ('SPECIAL8.py', lambda original: 'PART3_RECIPE = {\n', 'SPECIAL8', '파이썬 문법 오류'),
    ('SPECIAL8.py', lambda original: 'x = 1\n', 'SPECIAL8', 'PART3_RECIPE 선언'),
    ('SPECIAL8.recipe.json', lambda original: b'\xff\xfe{', 'SPECIAL8', 'UTF-8'),
    ('SPECIALX.recipe.json', lambda original: json.dumps({**original[0], 'id': 'SPECIALX'}), None, 'SPECIAL1~SPECIAL999'),
    ('SPECIAL0.recipe.json', lambda original: json.dumps({**original[0], 'id': 'SPECIAL0'}), 'SPECIAL0', 'SPECIAL1~SPECIAL999'),
    # An ID that is not its file name never replaces the strategy it names.
    ('SPECIAL8.recipe.json', lambda original: json.dumps(original[0]), 'SPECIAL8', '이름·중복·Recipe'),
    ('SPECIAL8.recipe.json', bad_intent, 'SPECIAL8', '최종 행동'),
]


def write(path, content):
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding='utf-8')
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))


@pytest.mark.parametrize('filename,content,identifier,reason', DEFECTS)
def test_broken_file_is_left_out_and_the_others_load(project, filename, content, identifier, reason):
    write(project.folder / filename, content(project.original))
    entries = registry.builtin_entries()
    assert set(entries) == project.ids
    assert entries[project.original[0]['id']] == project.original[0]
    assert set(registry.list_presets('Part1')) == set(registry.list_presets('Part2')) == project.ids
    assert set(registry.load_plugins({'SYMBOLS': 'XAUUSD+'})) == project.ids
    [skipped] = registry.skipped_builtins()
    assert skipped['file'] == filename and skipped['id'] == identifier
    assert skipped['notice'].startswith(filename + ' 제외 · ') and reason in skipped['notice']
    assert '\n' not in skipped['notice'] and len(skipped['notice']) < 260
    # One line for the screen and Telegram: no machine path.
    assert str(project.root) not in skipped['notice'] and not re.search(r'[A-Za-z]:[\\/]', skipped['notice'])


def test_fixed_file_returns_and_its_notice_clears(project):
    path = project.folder / 'SPECIAL8.recipe.json'
    write(path, '{')
    assert 'SPECIAL8' not in registry.builtin_entries() and registry.skipped_builtins()
    fixed = {**copy.deepcopy(project.original[0]), 'id': 'SPECIAL8'}
    write(path, json.dumps(fixed, ensure_ascii=False))
    assert registry.builtin_entries()['SPECIAL8'] == fixed
    assert registry.skipped_builtins() == []


def test_two_files_for_one_number_both_stay_out(project):
    first = project.original[0]['id']
    write(project.folder / (first + '.py'), 'PART3_RECIPE = ' + repr(project.original[0]['recipe']) + '\n')
    assert set(registry.builtin_entries()) == project.ids - {first}
    assert sorted(row['file'] for row in registry.skipped_builtins()) == [first + '.py', first + '.recipe.json']


def test_without_a_special_folder_nothing_is_skipped(project):
    for path in project.folder.iterdir():
        path.unlink()
    project.folder.rmdir()
    assert registry.skipped_builtins() == [] and len(registry.builtin_entries()) == len(project.original)


# ---- live start (Part1 controller) ----------------------------------------------------------------

def load_control():
    spec = importlib.util.spec_from_file_location('live_control158', ROOT / 'Part1/live_control.py')
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    return owner


NOTICE = 'SPECIAL3.py 제외 · 파이썬 문법 오류 2행'


@pytest.fixture
def control(tmp_path, monkeypatch):
    module = load_control()
    monkeypatch.setattr(module, 'SPECIAL_SETTINGS_PATH', tmp_path / 'special_settings.json')
    monkeypatch.setattr(module, 'discover_live_specials', lambda: ['SPECIAL1', 'SPECIAL2'])
    monkeypatch.setattr(module, 'skipped_special_files',
                        lambda: [{'file': 'SPECIAL3.py', 'id': 'SPECIAL3', 'notice': NOTICE}])
    monkeypatch.setattr(module, 'start_program', Mock(return_value=module.LiveStartResult(True, 'started', warnings=['restart'])))
    monkeypatch.setattr(module.subprocess, 'Popen', Mock(side_effect=AssertionError('real engine start forbidden')))
    return module


def test_saved_settings_of_a_skipped_file_do_not_block_live_start(control):
    times = {'MAIN_ASIA': {'enabled': True, 'start': '09:00', 'end': '2400'}}
    rows = {'SPECIAL1': {'enabled': True, 'trigger': '브레이커 올존', 'time_filters': times},
            'SPECIAL3': {'enabled': True, 'trigger': '무지성 브레이커 올존', 'time_filters': times}}
    control.SPECIAL_SETTINGS_PATH.write_text(json.dumps({'specials': rows}, ensure_ascii=False), encoding='utf-8')
    before = control.SPECIAL_SETTINGS_PATH.read_bytes()
    result = control.start_live()
    arguments = control.start_program.call_args.kwargs
    assert arguments['enabled_specials'] == {'SPECIAL1', 'SPECIAL2'}
    assert json.loads(arguments['special_triggers']) == {'SPECIAL1': '브레이커 올존'}
    assert json.loads(arguments['special_times']) == {'SPECIAL1': times}
    assert result[0] is True and NOTICE in result[1]
    assert result.warnings == ['restart', NOTICE]
    assert control.SPECIAL_SETTINGS_PATH.read_bytes() == before


def test_a_failed_start_still_reports_the_skipped_file(control):
    control.start_program.return_value = (False, '실행 실패')
    result = control.start_live()
    assert result[0] is False and result.warnings == [NOTICE]


def test_a_name_that_is_not_a_skipped_file_still_blocks_start(control):
    control.SPECIAL_SETTINGS_PATH.write_text(json.dumps({'specials': {'SPECIAL9': {'enabled': True}}}), encoding='utf-8')
    with pytest.raises(control.SpecialSettingsError, match='미등록 항목: SPECIAL9'):
        control.start_live()
    control.start_program.assert_not_called()


def test_without_skipped_files_the_start_result_is_unchanged(control, monkeypatch):
    monkeypatch.setattr(control, 'skipped_special_files', lambda: [])
    expected = control.start_program.return_value
    assert control.start_live() is expected


# ---- strategy settings screen (Part3) -------------------------------------------------------------

@pytest.fixture
def bridge(monkeypatch):
    from lab import unified_live
    saved = {'SPECIAL1': {'enabled': True, 'trigger': None, 'time_filters': None},
             'SPECIAL3': {'enabled': False, 'trigger': '브레이커 올존', 'time_filters': {'MAIN_ASIA': False}}}
    owner = {'save_special_settings': Mock(), 'list_live_engines': Mock(return_value=[]),
             'load_special_settings': Mock(return_value=saved)}
    monkeypatch.setattr(unified_live, '_closing', False)
    monkeypatch.setattr(unified_live, 'control', lambda: owner)
    monkeypatch.setattr(unified_live, 'specials', lambda: {'items': {'SPECIAL1': {}}, 'trigger_choices': []})
    monkeypatch.setattr(registry, 'skipped_builtins',
                        lambda: [{'file': 'SPECIAL3.py', 'id': 'SPECIAL3', 'notice': NOTICE}])
    return unified_live, owner, saved


def test_saving_keeps_the_settings_of_a_skipped_strategy(bridge):
    unified_live, owner, saved = bridge
    items = {'SPECIAL1': {'enabled': False, 'trigger': None, 'time_filters': None}}
    assert unified_live.save_specials(items)['ok']
    owner['save_special_settings'].assert_called_once_with({**items, 'SPECIAL3': saved['SPECIAL3']})


def test_an_unreadable_trigger_of_a_skipped_strategy_is_kept_off(bridge):
    unified_live, owner, saved = bridge
    saved['SPECIAL3'] = {'enabled': False, 'load_error': 'old', 'original': {'enabled': True, 'trigger': 'OLD'}}
    items = {'SPECIAL1': {'enabled': True, 'trigger': None, 'time_filters': None}}
    unified_live.save_specials(items)
    owner['save_special_settings'].assert_called_once_with({**items, 'SPECIAL3': {'enabled': False}})


def test_a_recovery_save_writes_only_the_submitted_strategies(bridge, monkeypatch):
    unified_live, owner, _ = bridge
    monkeypatch.setattr(unified_live, 'specials', lambda: {'items': {'SPECIAL1': {}}, 'trigger_choices': [],
                                                           'needs_recovery': True})
    items = {'SPECIAL1': {'enabled': True, 'trigger': None, 'time_filters': None}}
    unified_live.save_specials(items, recover=True)
    owner['save_special_settings'].assert_called_once_with(items)
    owner['load_special_settings'].assert_not_called()


def test_live_strategy_popup_lists_the_skipped_file_first(monkeypatch):
    from lab import unified_live
    owner = {'load_special_settings': lambda: None, 'load_oz_profiles': lambda: None,
             'special_code_default_trigger': lambda name: None}
    monkeypatch.setattr(unified_live, 'control', lambda: owner)
    monkeypatch.setattr(registry, 'skipped_builtins',
                        lambda: [{'file': 'SPECIAL3.py', 'id': 'SPECIAL3', 'notice': NOTICE}])
    data = unified_live.specials()
    assert data['catalog_errors'][0] == NOTICE
    assert list(data['items']) == list(registry.list_presets('Part1'))


# ---- Telegram notice at engine start --------------------------------------------------------------

@pytest.fixture
def host(tmp_path):
    from event_engine.engine import EventEngine
    from event_engine.ingress import IngressSequencer
    from event_host import EventHost
    from live_alert_recording import LiveAlertRecorder
    calls = []
    def transport(data):
        calls.append(data)
        return NS(status_code=200, json=lambda: {'ok': True, 'result': {'message_id': len(calls)}})
    config = {'TELEGRAM_CHAT_ID': 'ROOM'}
    recorder = LiveAlertRecorder(config, root_provider=lambda: {'path': str(tmp_path), 'selection_id': 'test'})
    host = EventHost(EventEngine(IngressSequencer(), strategies=()), config, NS(), transport=transport, recorder=recorder)
    logged = []
    diagnostics = NS(log=lambda module, level, message, *args: logged.append((module, level, message % args)))
    return NS(host=host, calls=calls, records=tmp_path / 'alerts', diagnostics=diagnostics, logged=logged)


def test_engine_start_notice_reaches_telegram_once_per_start(host, monkeypatch):
    import logging
    from event_host import announce_skipped_specials
    lines = [NOTICE, 'SPECIALX.recipe.json 제외 · 스페셜 파일명은 SPECIAL1~SPECIAL999 형식이어야 합니다']
    monkeypatch.setattr(registry, 'skipped_builtins', lambda: [{'file': 'SPECIAL3.py', 'id': 'SPECIAL3', 'notice': lines[0]},
                                                               {'file': 'SPECIALX.recipe.json', 'id': None, 'notice': lines[1]}])
    assert announce_skipped_specials(host.host, host.diagnostics, 1_790_000_000_000) == lines
    first = host.host.outputs.get_nowait()
    host.host.outputs.task_done()
    # The same start delivered twice is one message; the next start is a new one.
    for event in (first, first):
        host.host.outputs.put(event)
    announce_skipped_specials(host.host, host.diagnostics, 1_790_000_060_000)
    host.host.drain_outputs()
    assert [call['chat_id'] for call in host.calls] == ['ROOM', 'ROOM']
    assert host.calls[0]['text'] == '[라이브 시작] ' + lines[0] + '\n[라이브 시작] ' + lines[1]
    # The engine log keeps one line per file without turning the module status into a delay.
    assert host.logged == [('ENGINE', logging.INFO, line) for line in lines] * 2
    # Not a strategy alert: the LIVE record does not read it as SPECIALn.
    assert not re.match(r'^\[(\d)[.]', host.calls[0]['text'])
    [day] = host.records.iterdir()
    with day.open(encoding='utf-8-sig', newline='') as handle:
        assert {row['strategy'] for row in csv.DictReader(handle)} == {'HOST_NOTICE'}


def test_nothing_is_sent_when_every_file_loads(host, monkeypatch):
    from event_host import announce_skipped_specials
    monkeypatch.setattr(registry, 'skipped_builtins', lambda: [])
    assert announce_skipped_specials(host.host, host.diagnostics) == []
    host.host.drain_outputs()
    assert host.calls == [] and host.logged == []
