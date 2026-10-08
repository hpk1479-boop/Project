"""Single live/Telegram symbol setting, migration and input registration."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import shutil
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = ROOT / 'Part1/program'
sys.path[:0] = [str(PROGRAM), str(ROOT / 'Part3'), str(ROOT / 'Part2')]

from command_interpreter import CommandInterpreter
from event_host import HostInputs
from symbol_settings import configured_symbols, migrate_symbol_lines
from lab import unified_settings


@pytest.mark.parametrize('config,expected', [
    ({}, ()),
    ({'STAFF_ALLOWED_SYMBOLS': 'NAS100, BTCUSD', 'TARGET_SYMBOLS': 'BTCUSD,US30.cash'},
     ('NAS100', 'BTCUSD', 'US30.cash')),
    ({'SYMBOLS': 'GER40, NAS100,GER40', 'TARGET_SYMBOLS': 'BTCUSD'}, ('GER40', 'NAS100')),
    ({'SYMBOLS': '', 'STAFF_ALLOWED_SYMBOLS': 'BTCUSD'}, ()),
])
def test_current_key_is_authoritative_and_old_values_are_preserved(config, expected):
    assert configured_symbols(config) == expected


def test_read_is_read_only_and_save_removes_both_former_keys(tmp_path, monkeypatch):
    config = tmp_path / 'config.txt'
    before = ('# user note\r\nSTAFF_ALLOWED_SYMBOLS=NAS100, BTCUSD\r\n'
              'TARGET_SYMBOLS=BTCUSD,US30.cash\r\nWONBI_SIGMA=3\r\n').encode('utf-8')
    config.write_bytes(b'\xef\xbb\xbf' + before)
    monkeypatch.setattr(unified_settings, 'LIVE_CONFIG', config)
    lines, bom = unified_settings._live_lines()
    assert bom
    assert unified_settings._live_values(lines) == {'SYMBOLS': 'NAS100,BTCUSD,US30.cash', 'WONBI_SIGMA': '3'}
    assert config.read_bytes() == b'\xef\xbb\xbf' + before
    assert unified_settings.save_live({'SYMBOLS': 'GER40, NAS100,GER40'})['ok']
    saved = config.read_bytes()
    assert saved.startswith(b'\xef\xbb\xbf')
    assert saved.decode('utf-8-sig') == '# user note\r\nSYMBOLS=GER40,NAS100\r\nWONBI_SIGMA=3\r\n'
    assert configured_symbols(unified_settings._live_values(unified_settings._live_lines()[0])) == ('GER40', 'NAS100')


@pytest.mark.parametrize('value', ['', 'BAD NAME', 'A\nB'])
def test_invalid_symbol_edits_leave_file_intact(tmp_path, monkeypatch, value):
    config = tmp_path / 'config.txt'
    config.write_text('SYMBOLS=NAS100\n', encoding='utf-8')
    before = config.read_bytes()
    monkeypatch.setattr(unified_settings, 'LIVE_CONFIG', config)
    with pytest.raises(ValueError):
        unified_settings.save_live({'SYMBOLS': value})
    assert config.read_bytes() == before


def test_migration_keeps_one_line_and_preserves_explicit_current_selection():
    lines = ['TARGET_SYMBOLS=BTCUSD\n', 'SYMBOLS=GER40\n', 'STAFF_ALLOWED_SYMBOLS=NAS100\n']
    assert migrate_symbol_lines(lines) == ['SYMBOLS=GER40\n']
    assert migrate_symbol_lines(migrate_symbol_lines(lines)) == ['SYMBOLS=GER40\n']


def test_live_commands_use_same_list_and_ea_symbols_remain_available():
    config = {'SYMBOLS': 'NAS100,US30.cash', 'TELEGRAM_CHAT_ID': 'user'}
    interpreter = CommandInterpreter(config)
    assert interpreter.allowed_symbols() == ['NAS100', 'US30.cash']
    posted = []
    engine = SimpleNamespace(ingress=SimpleNamespace(post=lambda *args, **kwargs: posted.append(kwargs)))
    output = SimpleNamespace(reply_symbol=lambda *_: None, logical_reply_id=lambda *args: args[-1])
    host = HostInputs(engine, config, interpreter, output)
    host.symbol_source = lambda: ('BTCUSD', 'NAS100')
    assert host.symbols == ('NAS100', 'US30.cash', 'BTCUSD')
    assert interpreter.allowed_symbols() == ['NAS100', 'US30.cash', 'BTCUSD']
    assert host.telegram_update({'update_id': 1, 'message': {'chat': {'id': 'user', 'type': 'private'},
                                'text': 'BTCUSD 1분 올존 알려줘', 'date': 1, 'message_id': 1}})
    assert posted[0]['payload']['symbol'] == 'BTCUSD'


def test_composer_uses_common_list_without_losing_registered_watch_symbols():
    from event_composer_domain import ComposerManager
    manager = SimpleNamespace(config={'SYMBOLS': 'NAS100', 'TARGET_SYMBOLS': 'ignored'},
                              _lock=threading.RLock(), official_specs={}, official_chain_specs={},
                              manual_specs={'watch': SimpleNamespace(symbol='BTCUSD')})
    assert ComposerManager._allowed_symbols(manager) == ['NAS100', 'BTCUSD']


def test_backtest_selected_symbol_uses_the_common_key(tmp_path, monkeypatch):
    from event_backtest import runner
    program = tmp_path / 'Part1/program'
    program.mkdir(parents=True)
    (program / 'config.txt').write_text('SYMBOLS=US30.cash\n', encoding='utf-8')
    monkeypatch.setattr(runner, 'PROGRAM', program)
    config = runner.runtime_config({'symbol': 'GER40'})
    assert configured_symbols(config) == ('GER40',)
    assert not {'TARGET_SYMBOLS', 'STAFF_ALLOWED_SYMBOLS'} & config.keys()


def test_symbol_config_survives_project_relocation(tmp_path, monkeypatch):
    source = tmp_path / 'project/config.txt'
    source.parent.mkdir()
    source.write_text('SYMBOLS=NAS100,US30.cash\n', encoding='utf-8')
    moved = tmp_path / 'moved/config.txt'
    moved.parent.mkdir()
    shutil.copyfile(source, moved)
    monkeypatch.setattr(unified_settings, 'LIVE_CONFIG', moved)
    unified_settings.save_live({'SYMBOLS': 'US30.cash'})
    assert configured_symbols(unified_settings._live_values(unified_settings._live_lines()[0])) == ('US30.cash',)
    assert source.read_text('utf-8') == 'SYMBOLS=NAS100,US30.cash\n'
