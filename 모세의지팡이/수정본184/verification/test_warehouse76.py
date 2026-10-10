"""Only the requested internal/external warehouse and stored-symbol contracts."""
from __future__ import annotations
import json
from pathlib import Path
import shutil
import sys

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part3'), str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]
from lab import storage, unified_settings, unified_backtest, integration, backtest_jobs, unified_live
from event_backtest import settings, ui_model


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / '프로젝트 원래 위치'
    (root / 'Part1/program').mkdir(parents=True)
    (root / 'Part2/event_backtest').mkdir(parents=True)
    (root / 'Part3/projects').mkdir(parents=True)
    shutil.copyfile(ROOT / 'Part2/event_backtest/settings.py', root / 'Part2/event_backtest/settings.py')
    monkeypatch.setattr(storage, 'ROOT', root / 'Part3')
    monkeypatch.setattr(unified_backtest, 'ROOT', root)
    monkeypatch.setattr(unified_settings, 'ROOT', root)
    return root


@pytest.mark.parametrize('location', ['내부 창고', 'nested/창고', '.'])
def test_internal_location_saved_relative_and_resolved_from_project(project, monkeypatch, location):
    monkeypatch.chdir(project.parent)
    expected = (project / location).resolve()
    unified_settings.save_connections({'warehouse': str(expected)})
    data = json.loads((project / 'Part3/projects/connections.json').read_text('utf-8'))
    assert data['warehouse'] == Path(location).as_posix()
    assert str(project) not in data['warehouse'] and '..' not in Path(data['warehouse']).parts
    assert storage.connections()['warehouse'] == str(expected)
    assert unified_backtest.warehouse() == expected
    assert not (expected / 'captures.duckdb').exists()


def test_relative_internal_input_does_not_depend_on_current_directory(project, monkeypatch):
    monkeypatch.chdir(project.parent)
    unified_settings.save_connections({'warehouse': '자료/창고'})
    assert unified_backtest.warehouse() == project / '자료/창고'


def test_external_location_keeps_existing_behavior(project):
    external = project.parent / '외부 창고'
    unified_settings.save_connections({'warehouse': str(external)})
    assert unified_backtest.warehouse() == external
    assert storage.connections()['warehouse'] == str(external)
    assert not external.exists()


def test_internal_project_relocation_keeps_database_and_symbol_choices(project, monkeypatch):
    store = project / '자료 창고'
    store.mkdir()
    db = duckdb.connect(str(store / 'captures.duckdb'))
    db.execute('CREATE TABLE captures(symbol VARCHAR)')
    db.execute("INSERT INTO captures VALUES ('USTEC'),('XAUUSDm')")
    db.close()
    unified_settings.save_connections({'warehouse': str(store)})
    moved = project.parent / '옮긴 프로젝트 한글 공백'
    shutil.copytree(project, moved)
    monkeypatch.setattr(storage, 'ROOT', moved / 'Part3')
    monkeypatch.setattr(unified_backtest, 'ROOT', moved)
    monkeypatch.chdir(project.parent)
    selected = unified_backtest.warehouse()
    assert selected == moved / '자료 창고'
    assert settings.stored_symbols(selected) == ['USTEC', 'XAUUSDm']


@pytest.mark.parametrize('internal', [True, False])
def test_part2_setting_accepts_both_locations_without_changing_defaults(project, internal):
    where = project / 'Part2' / '저장 창고' if internal else project.parent / '외부 창고'
    config = project / 'Part2/event_backtest.json'
    config.write_text(json.dumps({'warehouse': '저장 창고' if internal else str(where), 'cores': 2}), 'utf-8')
    values = settings.settings(config)
    assert Path(values['warehouse']) == where
    assert values['cores'] == 2 and values['overlap_trading_days'] == 3


@pytest.mark.parametrize('internal', [True, False])
def test_generated_execution_accepts_both_roots(project, monkeypatch, internal):
    where = project / '내부 창고' if internal else project.parent / '외부 창고'
    seen = []
    monkeypatch.setattr(unified_backtest, '_scenario', lambda request: {'symbol': request['symbol']})
    monkeypatch.setattr(backtest_jobs, 'start', lambda request: seen.append(request) or {'job_id': 'a' * 32})
    result = integration.backtest({'warehouse': str(where), 'symbol': 'USTEC', 'watch_text': 'fixture', 'action': 'plan'})
    assert result['job_id'] == 'a' * 32
    assert seen[0]['warehouse'] == where and seen[0]['project_root'] == project


def test_relative_escape_is_not_persisted(project):
    with pytest.raises(ValueError, match='상위 폴더'):
        unified_settings.save_connections({'warehouse': '../외부'})
    assert not (project / 'Part3/projects/connections.json').exists()


@pytest.mark.parametrize('symbols', [[], ['GOLDm'], ['USTEC', 'EURUSD', 'USTEC', None]])
def test_options_return_only_real_stored_symbols_read_only(project, monkeypatch, symbols):
    store = project / '저장 창고'
    store.mkdir()
    db = duckdb.connect(str(store / 'captures.duckdb'))
    db.execute('CREATE TABLE captures(symbol VARCHAR)')
    if symbols:
        db.executemany('INSERT INTO captures VALUES (?)', [(symbol,) for symbol in symbols])
    db.close()
    for symbol in {symbol for symbol in symbols if symbol is not None}:
        (store / 'captures' / symbol).mkdir(parents=True)
    path = store / 'captures.duckdb'
    before = path.read_bytes()
    monkeypatch.setattr(unified_backtest, 'warehouse', lambda: store)
    monkeypatch.setattr(ui_model, 'load', lambda: {})
    monkeypatch.setattr(unified_live, 'control', lambda: {'special_code_default_trigger': lambda _: '올존'})
    value = unified_backtest.options()
    expected = sorted({symbol for symbol in symbols if symbol is not None})
    assert value['symbols'] == expected
    # The current selection contract prefers gold, then Nasdaq, then the list.
    assert value['symbol'] == ('USTEC' if 'USTEC' in expected else expected[0] if expected else '')
    assert path.read_bytes() == before
