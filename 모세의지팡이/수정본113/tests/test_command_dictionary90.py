"""One portable language source; deterministic command meanings stay intact."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = ROOT / 'Part1/program'
sys.path.insert(0, str(PROGRAM))
import command_interpreter as commands
import moses_language as shared_language
from moses_language import command_language, language_path, load_dictionary, public_vocabulary


def test_shared_default_has_no_second_alias_source():
    interpreter = commands.CommandInterpreter({'SYMBOLS': 'XAUUSD+,USTEC'})
    assert interpreter.aliases_path == language_path()
    assert interpreter.language() == command_language()
    assert commands.DEFAULT_COMMAND_LANGUAGE == command_language()
    assert not (PROGRAM / 'command_aliases.json').exists()
    # AI receives the same public lexical entries, without execution or keys.
    assert interpreter.language()['cross'] == public_vocabulary()['aliases']['cross']
    assert interpreter.language()['symbols'] == public_vocabulary()['aliases']['symbols']


@pytest.mark.parametrize('text,expected', [
    ('gold 1분 lower wonbi 감시해줘', 'gold 1분 하단 원비 알려줘'),
    ('골드 5분 하단 원비 touch 알람', '골드 5분 하단 원비 터치 알려줘'),
    ('골드 1분 EMA21 골든 크로스 알람', '골드 1분 EMA20 골크 알려줘'),
])
def test_existing_normalization_meaning_is_preserved(text, expected):
    interpreter = commands.CommandInterpreter({})
    # Symbol parsing is deliberately separate from phrase normalization.
    actual = interpreter.normalize_command_text(text)
    assert actual == expected


def test_symbol_alias_uses_current_broker_symbol_and_rejects_conflicting_direction():
    interpreter = commands.CommandInterpreter({'SYMBOLS': 'XAUUSD+,NAS100'})
    assert interpreter.parse_symbol('골드 15분 알려줘') == 'XAUUSD+'
    assert interpreter.parse_symbol('나스닥 1시간 알려줘') == 'NAS100'
    assert interpreter.parse_oz_direction('골드 1분 매수 올존 알려줘') == 'LONG'
    with pytest.raises(ValueError, match='동시에'):
        interpreter.parse_oz_direction('골드 1분 매수 올존 매도 올존 알려줘')


def test_explicit_relative_override_merges_and_hot_reloads_last_valid(tmp_path):
    program = tmp_path / 'project/Part1/program'
    program.mkdir(parents=True)
    override = program / 'custom_terms.json'
    override.write_text(json.dumps({'phrase_aliases': {'터치': ['닿거든']}}), encoding='utf-8')
    config = {'COMMAND_ALIASES_FILE': 'custom_terms.json', 'COMMAND_ALIASES_RELOAD_SEC': '1'}
    resolved = commands.resolve_command_language_path(config, program)
    assert resolved == override
    interpreter = commands.CommandInterpreter(config, resolved)
    assert '터치' in interpreter.normalize_command_text('원비 닿거든 알려줘')
    assert interpreter.default_tf() == command_language()['defaults']['timeframe']
    assert interpreter.language()['cross'] == command_language()['cross']
    override.write_text(json.dumps({'phrase_aliases': {'터치': ['닿거든', '짚으면']}}), encoding='utf-8')
    os.utime(override, (100, 100))
    assert interpreter.reload_if_needed(10)
    assert '터치' in interpreter.normalize_command_text('원비 짚으면 알려줘')
    previous = interpreter.language()
    override.write_text('{invalid', encoding='utf-8')
    os.utime(override, (101, 101))
    assert not interpreter.reload_if_needed(20)
    assert interpreter.language() is previous
    assert '터치' in interpreter.normalize_command_text('원비 짚으면 알려줘')


def test_nested_dictionary_is_read_as_command_data_and_stays_private(tmp_path):
    relocated = tmp_path / 'language.json'
    raw = json.loads(language_path().read_text('utf-8'))
    raw['command_language']['phrase_aliases']['터치'].append('살짝닿으면')
    raw['command_language']['symbols'].pop('ETHUSD')
    relocated.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    interpreter = commands.CommandInterpreter({}, relocated)
    assert '터치' in interpreter.normalize_command_text('원비 살짝닿으면 알려줘')
    assert '살짝닿으면' not in command_language()['phrase_aliases']['터치']
    assert 'ETHUSD' not in interpreter.language()['symbols']
    assert 'examples' not in interpreter.language()


def test_shared_hot_reload_preserves_previous_valid_dictionary(tmp_path, monkeypatch):
    relocated = tmp_path / 'language.json'
    raw = json.loads(language_path().read_text('utf-8'))
    relocated.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(commands, 'language_path', lambda: relocated)
    interpreter = commands.CommandInterpreter({'COMMAND_ALIASES_RELOAD_SEC': '1'})
    try:
        raw['command_language']['phrase_aliases']['터치'].append('살짝닿으면')
        relocated.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
        os.utime(relocated, (100, 100))
        assert interpreter.reload_if_needed(10)
        assert '살짝닿으면' in interpreter.language()['phrase_aliases']['터치']
        previous = interpreter.language()
        relocated.write_text('{invalid', encoding='utf-8')
        os.utime(relocated, (101, 101))
        assert not interpreter.reload_if_needed(20)
        assert interpreter.language() is previous
    finally:
        load_dictionary.cache_clear()


def test_live_dictionary_reload_updates_terms_and_invalid_reload_keeps_kim_ai_views(tmp_path, monkeypatch):
    from common_ai.reference_pack import load_pack
    from common_ai.external_prompt import prepare
    from domain_clock import event_scope
    from kim_secretary import KimSecretary
    relocated = tmp_path / 'language.json'
    raw = json.loads(language_path().read_text('utf-8'))
    relocated.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(shared_language, 'language_path', lambda: relocated)
    monkeypatch.setattr(commands, 'language_path', lambda: relocated)
    load_dictionary.cache_clear()
    interpreter = commands.CommandInterpreter({'SYMBOLS': 'XAUUSD+', 'COMMAND_ALIASES_RELOAD_SEC': '1'})
    secretary = KimSecretary(interpreter)
    assert not interpreter.is_trend_score_query('골드 15분 합산점수 몇 점?')
    try:
        raw['grammar_terms']['trend_score_words'].append('합산점수')
        raw['command_language']['phrase_aliases']['조회'].append('점검해')
        relocated.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
        os.utime(relocated, (100, 100))
        assert interpreter.reload_if_needed(10)
        assert interpreter.is_trend_score_query('골드 15분 합산점수 몇 점?')
        assert interpreter.normalize_command_text('골드 추세 점검해') == '골드 추세 조회'
        with event_scope(1790380680000, 'reload90', {}):
            expected = secretary.parse('골드 15분 합산점수 몇 점?', 'user')
        assert expected.kind == 'QUERY' and expected.value['purpose'] == 'TREND_SCORE_QUERY'
        dictionary = load_dictionary()
        reference = load_pack()
        vocabulary = public_vocabulary()
        messages = [{'role': 'user', 'content': '골드 15분 상승추세에 1분 올존 알려줘'}]
        schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}},
                  'required': ['ok'], 'additionalProperties': False}
        ai_body = prepare(messages, [], schema, {'role': 'strategy'})
        assert '점검해' in vocabulary['aliases']['phrase_aliases']['조회']
        current = interpreter.language()
        relocated.write_text('{invalid', encoding='utf-8')
        os.utime(relocated, (101, 101))
        assert not interpreter.reload_if_needed(20)
        assert interpreter.language() is current
        assert load_dictionary() is dictionary
        assert load_pack() is reference
        assert public_vocabulary() == vocabulary
        assert prepare(messages, [], schema, {'role': 'strategy'}) == ai_body
        with event_scope(1790380680000, 'reload90', {}):
            assert secretary.parse('골드 15분 합산점수 몇 점?', 'user') == expected
    finally:
        load_dictionary.cache_clear()


def test_command_import_after_project_relocation_does_not_import_part3(tmp_path):
    moved = tmp_path / 'moved/project'
    program = moved / 'Part1/program'
    program.mkdir(parents=True)
    for name in ('command_interpreter.py', 'symbol_settings.py', 'domain_clock.py'):
        shutil.copyfile(PROGRAM / name, program / name)
    shutil.copytree(ROOT / 'moses_language', moved / 'moses_language', ignore=shutil.ignore_patterns('__pycache__'))
    script = '''
import importlib.abc,sys
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1])/'Part1/program'))
class Boundary(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0] in ('Part3','lab'):
            raise AssertionError('Part3 import')
sys.meta_path.insert(0,Boundary())
from command_interpreter import CommandInterpreter
from moses_language import language_path
interpreter=CommandInterpreter({'SYMBOLS':'XAUUSD+,NAS100'})
assert interpreter.parse_symbol('골드')=='XAUUSD+'
assert interpreter.aliases_path==language_path()
assert language_path().is_relative_to(Path(sys.argv[1]))
assert not any(name=='Part3' or name.startswith('lab.') for name in sys.modules)
'''
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    run = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', script, str(moved)],
                         cwd=tmp_path, env=env, capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
