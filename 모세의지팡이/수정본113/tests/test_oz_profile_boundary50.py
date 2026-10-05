"""Revision50: current-only OZ execution, isolated and non-destructive saved-data reads.

No broker/network I/O. Replay integration uses the actual event engine and MA49 fixtures.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'Part1/program'), str(ROOT/'Part2'), str(ROOT/'Part3')]

import oz_profiles as profiles
from oz_profile_loader import LegacyUnsupportedProfile, load_saved_oz
from test_ma_strategy_chain49 import (EngineRun, OrchestrationRun, CASES, chain,
                                     trigger, strategy, condition, SYMBOL, BASE)
from test_oz_profiles48 import CONFIG, prohibit_network
from watch_ma import parse_ma_expression
from event_engine import Kind
from event_application import create_event_engine
from lab import catalog, storage
from lab.compiler import prepare_manual

LEGACY = ['REGIME', 'SUPER', 'DIVERGENCE_REGIME', 'REGIME_SUPER', 'SUPER_REGIME',
          'DIVERGENCE_SUPER', 'DIVERGENCE_REGIME_SUPER']
CURRENT_FILES = [
    'Part1/program/oz_profiles.py', 'Part1/program/oz_engine/common.py',
    'Part1/program/oz_engine/profile.py', 'Part1/program/oz_engine/controllers.py',
    'Part1/program/oz_engine/runtime.py', 'Part1/program/monitor_OZ.py',
    'Part1/program/event_composer_domain.py', 'Part1/program/command_interpreter.py', 'Part1/program/watch_orchestrator.py',
    'Part1/program/SPECIAL/SPECIAL5.py', 'Part1/program/SPECIAL/SPECIAL7.py',
    'Part3/lab/catalog.py', 'Part3/lab/compiler.py', 'Part3/lab/ai/agent.py',
    'Part3/web/app.js', 'Part3/web/unified.js',
    'Part3/ai_context/MOSES_LANGUAGE.md', 'Part3/ai_context/STRATEGY_LANGUAGE.md',
    'Part3/docs/USER_GUIDE.md', 'Part3/docs/STRATEGY_SCHEMA.md',
    'Part3/docs/SPECIAL_1_TO_7.md', 'Part3/docs/SPECIAL_REVIEW_REFERENCE.html',
]


@pytest.mark.parametrize('path', CURRENT_FILES)
def test_current_code_and_ui_have_no_historical_oz_vocabulary(path):
    text = (ROOT/path).read_text('utf-8-sig')
    assert not re.search(r'\b(?:REGIME|SUPER|DIVERGENCE)(?:_[A-Z]+)*\b|레짐.?올존|슈퍼.?올존|REMOVED_SOURCE_PREFIX|_reject_removed', text)


@pytest.mark.parametrize('name', LEGACY + ['DIVERGENCE', 'UNKNOWN', 'FUTURE_PROFILE'])
def test_new_runtime_profile_has_only_generic_schema_error(name):
    with pytest.raises(profiles.ProfileError) as caught:
        profiles.normalize_profile('NORMAL', name)
    assert type(caught.value) is profiles.ProfileError
    assert '구버전' not in str(caught.value) and '제거된' not in str(caught.value)


@pytest.mark.parametrize('name', LEGACY)
@pytest.mark.parametrize('field', ['trigger_mode', 'oz_mode', 'trigger_type', 'special5_source_trigger_mode'])
def test_loader_rejects_even_masked_unsupported_profile_without_mutation(name, field):
    original = {'watch_id': 'watch50', 'validation_mode': 'NORMAL', 'trigger_mode': 'OZ', field: name}
    before = copy.deepcopy(original)
    with pytest.raises(LegacyUnsupportedProfile) as caught:
        load_saved_oz(original, path='watches[3]')
    assert original == before
    assert name in str(caught.value) and 'watches[3]' in str(caught.value)


@pytest.mark.parametrize('vm', ['NORMAL', 'BLIND'])
@pytest.mark.parametrize('field', ['trigger_mode', 'oz_mode', 'trigger_type'])
def test_saved_breaker_read_is_idempotent_and_serializes_current_only(vm, field):
    payload = {'validation_mode': vm, field: 'BREAKER', 'watch_id': 'watch50', 'expires_at': BASE+3600}
    output = load_saved_oz(payload)
    assert (output['validation_mode'], output['trigger_mode']) == (vm, 'BREAKER')
    assert output == load_saved_oz(output) == profiles.normalize_watch_payload(output)
    assert not {'oz_mode', 'trigger_type'} & output.keys()
    assert 'DIVERGENCE' not in json.dumps(output)
    assert payload[field] == 'BREAKER'


@pytest.mark.parametrize('bad', ['레짐', '슈퍼', '다이버전스', '알수없는단어'])
def test_commands_treat_all_unknown_modifiers_identically_and_register_nothing(bad):
    text = f'골드 1분 {bad} 올존 알려줘'
    with pytest.raises(profiles.ProfileError) as caught:
        profiles.text_trigger_mode(text)
    assert '알 수 없는 트리거 단어' in str(caught.value)
    assert not isinstance(caught.value, LegacyUnsupportedProfile)
    engine = create_event_engine(CONFIG, symbols=(SYMBOL,), selection=['WATCH'], enabled_specials=())
    engine.ingress.post(Kind.COMMAND, source='test', source_seq=1, source_time=BASE*1000,
                        payload={'symbol': SYMBOL, 'strategy': 'WATCH', 'chat_id': 'offline', 'text': text})
    engine.run()
    state = engine.processor_state.get('OZ_STATE', {}).get('runtime')
    assert state is None or not state.watch._watches


@pytest.mark.parametrize('vm,tm', profiles.PROFILE_KEYS)
@pytest.mark.parametrize('tf', ['1분', '1분봉'])
def test_current_command_profiles_reach_real_watch(vm, tm, tf):
    engine = create_event_engine(CONFIG, symbols=(SYMBOL,), selection=['WATCH'], enabled_specials=())
    text = f'골드 {tf} {profiles.profile_label(vm,tm)} 계속 알려줘'
    engine.ingress.post(Kind.COMMAND, source='test', source_seq=1, source_time=BASE*1000,
                        payload={'symbol': SYMBOL, 'strategy': 'WATCH', 'chat_id': 'offline', 'text': text})
    engine.run()
    assert not engine.error_log
    watches = list(engine.processor_state['OZ_STATE']['runtime'].watch._watches.values())
    assert watches and {(w.validation_mode,w.trigger_mode) for w in watches} == {(vm,tm)}


@pytest.mark.parametrize('old', LEGACY)
def test_saved_special_settings_are_excluded_reported_and_cannot_resave_as_defaults(old):
    raw = {'SPECIAL2': {'enabled': True, 'trigger': old},
           'SPECIAL7': {'enabled': True, 'trigger': '브레이커 올존'}}
    loaded = load_saved_oz(raw, kind='specials')
    row = loaded['SPECIAL2']
    assert row['enabled'] is False and row['original'] == raw['SPECIAL2']
    assert old in row['load_error'] and 'trigger' not in row
    assert loaded['SPECIAL7'] == raw['SPECIAL7']
    with pytest.raises(profiles.ProfileError):
        profiles.normalize_special_settings(loaded)
    assert raw['SPECIAL2']['enabled'] is True


def test_arbitrary_ids_and_nonprofile_regime_metrics_do_not_become_oz_legacy():
    payload = {'watch_id':'USER_SUPERMAN:REGIMEN_METRIC', 'trigger_mode':'BREAKER',
               'metrics': {'regime_band': 1.2, 'regime_slope': .4}, 'name':'SUPERTREND comparison'}
    loaded = load_saved_oz(payload)
    assert loaded['watch_id'] == payload['watch_id'] and loaded['metrics'] == payload['metrics']
    source = 'regime_band = 1\nregime_slope = 2\nSUPERTREND = 3\ndef register(manager): pass\n'
    assert load_saved_oz(source, kind='source') == source
    prepare_manual(source, 'Test_SPECIAL888.py')


@pytest.mark.parametrize('expression,step,direction,window', CASES)
def test_saved_timed_chain_compatible_oz_profile_retains_generic_ma_and_live_replay(expression, step, direction, window):
    spec = chain(trigger(expression), final_window_sec=window)
    raw = spec.to_json()
    raw['trigger_mode'] = 'BREAKER'
    files = {'composer_timed_chains.json': json.dumps({'version':2,'chains':[raw]})}
    runs = [EngineRun(files=files, backtest=backtest) for backtest in (False,True)]
    for run in runs:
        run.feed([100.]*400)
        assert run.timed.trigger_mode == 'BREAKER'
        assert run.timed.triggers[0].ma_expression == parse_ma_expression(expression).canonical
        assert run.timed.triggers[0].evaluation_mode == 'CLOSE'
        run.feed([100.]*400)
        run.feed([100.]*400+[step])
        assert not run.oz_watches()
        run.feed([100.]*400+[step,step])
        assert run.oz_watches() and run.oz_watches()[0]['trigger_mode'] == 'BREAKER'
        assert run.timed.triggers[0].ma_expression == parse_ma_expression(expression).canonical
    assert runs[0].signature() == runs[1].signature()


def test_nested_saved_chain_resume_cancel_and_rearm_keep_expressions(tmp_path):
    first, second, cancel = [trigger(row[0]) for row in CASES]
    spec = chain(first, second, invalidation_triggers=(cancel,), final_window_sec=1800)
    raw = spec.to_json()
    for row in [raw, *raw['triggers'], *raw['invalidation_triggers']]:
        row['trigger_mode'] = 'BREAKER'
    from watch_orchestrator import TimedChainSpec
    restored = TimedChainSpec.from_json(load_saved_oz(raw))
    run = OrchestrationRun(tmp_path)
    run.call('add_chain', restored)
    run.hit(restored)
    assert restored.stage == 1
    resumed = OrchestrationRun(tmp_path, run.memory)
    resumed.call('load_state')
    actual = resumed.state.chains[restored.chain_id]
    assert actual.to_json() == restored.to_json()
    resumed.hit(actual, cancel=True)
    assert actual.stage == 0 and resumed.pushes[-1]['ma_expression'] == first.ma_expression
    assert actual.invalidation_triggers[0].ma_expression == cancel.ma_expression
    assert all(t.trigger_mode == 'BREAKER' for t in (*actual.triggers,*actual.invalidation_triggers))


def test_part1_control_load_excludes_only_affected_saved_setting(tmp_path, monkeypatch):
    from lab.unified_live import control
    functions = control()
    load = functions['load_special_settings']
    path = tmp_path/'special_settings.json'
    monkeypatch.setitem(load.__globals__, 'SPECIAL_SETTINGS_PATH', path)
    raw = {'version':1,'specials':{'SPECIAL2':{'enabled':True,'trigger':'SUPER'},
                                  'SPECIAL7':{'enabled':True,'trigger':'BREAKER'}}}
    path.write_text(json.dumps(raw), 'utf-8')
    before = path.read_bytes()
    loaded = load()
    assert loaded['SPECIAL2']['enabled'] is False
    assert 'trigger' not in loaded['SPECIAL2']
    assert loaded['SPECIAL7']['trigger'] == '브레이커 올존'
    assert json.loads(functions['special_trigger_env'](loaded)) == {'SPECIAL7':'브레이커 올존'}
    assert path.read_bytes() == before
    with pytest.raises(ValueError):functions['save_special_settings'](loaded)
    assert path.read_bytes() == before
    loaded['SPECIAL2'] = {'enabled':False,'trigger':'올존'}
    functions['save_special_settings'](loaded)
    assert 'SUPER' not in path.read_text() and 'DIVERGENCE' not in path.read_text()


def test_part2_saved_file_boundary_and_strict_save(tmp_path):
    from event_backtest import ui_model
    path = tmp_path/'ui.json'
    raw = {'target_mode':'SPECIAL','specials':{'SPECIAL2':{'enabled':True,'trigger':'REGIME'},
           'SPECIAL7':{'enabled':True,'trigger':'브레이커 올존'}}, 'watch':{'text':''}}
    path.write_text(json.dumps(raw),'utf-8')
    before = path.read_bytes()
    loaded = ui_model.load(path)
    assert loaded['specials']['SPECIAL2']['enabled'] is False
    assert loaded['specials']['SPECIAL7']['trigger'] == '브레이커 올존'
    with pytest.raises(ValueError):ui_model.save(loaded,path)
    assert path.read_bytes() == before
    del loaded['specials']['SPECIAL2']
    ui_model.save(loaded,path)
    assert ui_model.load(path) == loaded
    loaded['specials']['SPECIAL7']['trigger']='DIVERGENCE'
    with pytest.raises(ValueError):ui_model.save(loaded,path)


@pytest.mark.parametrize('literal', ["FINAL_TRIGGER_MODE={value!r}",
    "params={{'trigger_mode':{value!r}}}", "def setup():\n    api.add(trigger_mode={value!r})",
    "PART3_RECIPE={{'config':{{'FINAL_TRIGGER_MODE':{value!r}}}}}"])
@pytest.mark.parametrize('value', ['DIVERGENCE','REGIME','SUPER'])
def test_new_manual_source_never_saves_historical_profile_and_old_source_loader_isolated(literal, value):
    source = literal.format(value=value) + '\ndef register(manager): pass\n'
    with pytest.raises(ValueError):prepare_manual(source,'Test_SPECIAL888.py')
    with pytest.raises(LegacyUnsupportedProfile):load_saved_oz(source,kind='source')


def test_saved_source_utf8_offsets_and_multiline_literal_only():
    source = '''# 한글 주석 보존\n한글="한글"; FINAL_TRIGGER_MODE="""breaker"""\nREGIME_METRIC=2\ndef register(manager): pass\n'''
    migrated = load_saved_oz(source,kind='source')
    assert migrated == source.replace('"""breaker"""', "'BREAKER'")


def test_part3_saved_recipe_and_draft_breaker_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(storage,'ROOT',tmp_path)
    recipe=catalog.new();recipe['config']['FINAL_TRIGGER_MODE']='BREAKER'
    data={'recipe':recipe,'manual':True,'manual_code':"FINAL_TRIGGER_MODE='BREAKER'\ndef register(manager):pass\n"}
    before=copy.deepcopy(data)
    loaded=storage.load_saved_draft(data)
    assert data==before
    storage.save_draft(loaded)
    text=(tmp_path/'projects/last_draft.json').read_text()
    assert 'DIVERGENCE' not in text
    for invalid in ('DIVERGENCE', 'SUPER'):
        bad=copy.deepcopy(data)
        bad['manual_code']=bad['manual_code'].replace('BREAKER',invalid)
        with pytest.raises(ValueError):storage.save_draft(bad)
        with pytest.raises(LegacyUnsupportedProfile):storage.load_saved_draft(bad)
        assert (tmp_path/'projects/last_draft.json').read_text()==text


def test_external_part1_settings_read_by_catalog_uses_same_loader(tmp_path,monkeypatch):
    monkeypatch.setattr(catalog,'source_path',lambda number,project_root='':ROOT/f'Part1/program/SPECIAL/SPECIAL{number}.py')
    path=tmp_path/'Part1/special_settings.json';path.parent.mkdir()
    for value,accepted in [('브레이커 올존',True),('다이버전스 올존',False),('REGIME',False)]:
        path.write_text(json.dumps({'specials':{'SPECIAL7':{'enabled':True,'trigger':value}}}),'utf-8')
        before=path.read_bytes()
        if accepted:
            recipe=catalog.load(7,str(tmp_path))
            assert recipe['config']['FINAL_TRIGGER_MODE']=='BREAKER'
        else:
            with pytest.raises(ValueError,match='구버전 미지원'):catalog.load(7,str(tmp_path))
        assert path.read_bytes()==before

@pytest.mark.parametrize('value',['DIVERGENCE','REGIME','SUPER'])
def test_new_project_save_does_not_store_historical_profile_in_source_text(tmp_path,monkeypatch,value):
    monkeypatch.setattr(storage,'ROOT',tmp_path)
    recipe=catalog.new();recipe['source_text']=f'FINAL_TRIGGER_MODE={value!r}\n'
    with pytest.raises(ValueError):storage.save_project(recipe)
    with pytest.raises(ValueError):storage.save_draft({'recipe':recipe,'manual':False})
    assert not (tmp_path/'projects').exists()
