"""Preset IDs and metadata remain data in live UI, diagnostics and replay."""
from __future__ import annotations

import json
import logging
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Part1/program'),str(ROOT/'Part2'),str(ROOT/'Part3')]


@pytest.fixture
def registry_file(tmp_path,monkeypatch):
    from strategy_recipe import registry
    path=tmp_path/'strategy_registry.json'
    monkeypatch.setattr(registry,'REGISTRY',path)
    def write(names):
        rows=[]
        for name in names:
            rows.append({'id':name,'name':'공통 전략 '+name,'final_time_filters':{'MAIN_LONDON':{'enabled':True}},
                         'recipe':{'schema_version':2,'base':'AI','strategy_intent':{
                             'symbols':['TEST.cash'],'steps':[{'kind':'TREND','tfs':['15m']}],
                             'final':{'kind':'OZ','tfs':['1m'],'validation_mode':'NORMAL','trigger_mode':'OZ'}}}})
        path.write_text(json.dumps({'schema_version':2,'presets':rows},ensure_ascii=False),encoding='utf-8')
    write(['MY_ALPHA'])
    return write


def test_added_and_removed_presets_change_live_choices_metadata_and_status(registry_file,monkeypatch):
    from strategy_recipe import registry
    from lab import unified_live
    import module_diagnostics
    owner={'load_special_settings':lambda:{'MY_ALPHA':{'enabled':True},'removed':{'enabled':True}},
           'load_oz_profiles':lambda:None,'special_code_default_trigger':lambda name:registry.default_settings(name)[0]}
    monkeypatch.setattr(unified_live,'control',lambda:owner)
    queried=[]
    def snapshot(_root,module,**kwargs):
        queried.append((module,kwargs))
        return {'enabled_specials':list(registry.list_presets()),'modules':{
                    name:{'status':'정상','last':1} for name in registry.list_presets()}}
    monkeypatch.setattr(module_diagnostics,'read_snapshot',snapshot)
    first=unified_live.specials()
    assert tuple(first['items'])==('MY_ALPHA',)
    assert first['items']['MY_ALPHA']['name']=='공통 전략 MY_ALPHA'
    assert first['default_time_filters']['MY_ALPHA']=={'MAIN_LONDON':{'enabled':True}}
    assert unified_live.status('MY_ALPHA')['modules']['MY_ALPHA']['preset'] is True
    registry_file(['MY_ALPHA','ANY_NEW_ID'])
    assert tuple(unified_live.specials()['items'])==('MY_ALPHA','ANY_NEW_ID')
    added=unified_live.status('ANY_NEW_ID')
    assert added['modules']['ANY_NEW_ID']['name']=='공통 전략 ANY_NEW_ID'
    assert added['modules']['ANY_NEW_ID']['state']=='연결중'
    registry_file(['ANY_NEW_ID'])
    assert tuple(unified_live.specials()['items'])==('ANY_NEW_ID',)
    assert 'MY_ALPHA' not in unified_live.status()['modules']
    with pytest.raises(ValueError,match='모듈 선택'):unified_live.status('MY_ALPHA')
    assert queried[-1][1]['modules']==(*unified_live.MODULES,'ANY_NEW_ID')


def test_backtest_selection_uses_registry_membership_and_external_plugins(registry_file,monkeypatch):
    from event_backtest.settings import scenario
    import event_selection
    monkeypatch.setattr(event_selection,'SPECIAL_DEPENDENCIES',{'Test_SPECIAL999':('OZ',)})
    assert scenario(strategies=['MY_ALPHA','WATCH'])['enabled_specials']==['MY_ALPHA']
    registry_file(['ANY_NEW_ID'])
    assert scenario(strategies=['ALL'])['enabled_specials']==['ANY_NEW_ID','Test_SPECIAL999']
    assert scenario(strategies=['MY_ALPHA','ANY_NEW_ID'])['enabled_specials']==['ANY_NEW_ID']
    assert event_selection.resolve(['ANY_NEW_ID'],{}).specials==('ANY_NEW_ID',)
    with pytest.raises(ValueError,match='unknown selected strategies'):event_selection.resolve(['MY_ALPHA'],{})


def test_replay_result_metadata_uses_registry_defaults_and_overrides(registry_file):
    from event_backtest.runner import applied_strategy_settings
    data=applied_strategy_settings({'strategies':['ALL']},{'MAIN_LONDON':'1600-1900'})
    assert tuple(data)==('MY_ALPHA',)
    assert data['MY_ALPHA']['time_source']=='preset_default'
    assert data['MY_ALPHA']['final_alert_time_filters']=={'MAIN_LONDON':{'enabled':True}}
    registry_file(['ANY_NEW_ID'])
    override={'MAIN_LONDON':{'enabled':False}}
    data=applied_strategy_settings({'strategies':['ANY_NEW_ID'],'special_time_filters':{'ANY_NEW_ID':override}}, {})
    assert tuple(data)==('ANY_NEW_ID',)
    assert data['ANY_NEW_ID']['time_source']=='override'
    assert data['ANY_NEW_ID']['final_alert_time_filters']==override


def test_backtest_ui_defaults_and_saved_selection_follow_registry(registry_file,tmp_path):
    from event_backtest import ui_model
    path=tmp_path/'backtest_ui.json'
    initial=ui_model.load(path)
    assert tuple(initial['specials'])==('MY_ALPHA',)
    assert initial['specials']['MY_ALPHA']['enabled'] is False
    with pytest.raises(ValueError,match='실행할 전략'):
        ui_model.make_scenario(initial,{'symbol':'TEST.cash','start':'2026-01-01','end':'2026-01-02'})
    initial['specials']['MY_ALPHA']['enabled']=True
    ui_model.save(initial,path)
    before=path.read_bytes()
    registry_file(['MY_ALPHA','ANY_NEW_ID'])
    loaded=ui_model.load(path)
    assert loaded['specials']['MY_ALPHA']['enabled'] is True
    assert loaded['specials']['ANY_NEW_ID']['enabled'] is False
    result=ui_model.make_scenario(loaded,{'symbol':'TEST.cash','start':'2026-01-01','end':'2026-01-02'})
    assert result['strategies']==['MY_ALPHA']
    registry_file(['ANY_NEW_ID'])
    assert tuple(ui_model.load(path)['specials'])==('ANY_NEW_ID',)
    assert path.read_bytes()==before


def test_registry_data_is_included_in_backtest_provenance(tmp_path,monkeypatch):
    from event_backtest import runner
    program=tmp_path/'Part1/program'
    program.mkdir(parents=True)
    (tmp_path/'Part2/event_backtest').mkdir(parents=True)
    native=tmp_path/'Part2/generic_backtest/native_mt5.py'
    native.parent.mkdir()
    native.write_text('# no execution\n',encoding='utf-8')
    registry=tmp_path/'settings/strategy_registry.json'
    registry.parent.mkdir()
    registry.write_text('{"presets": []}',encoding='utf-8')
    monkeypatch.setattr(runner,'PROGRAM',program)
    before=runner.code_hash()
    registry.write_text('{"presets": [{"id": "MY_ALPHA"}]}',encoding='utf-8')
    assert runner.code_hash()!=before


def test_diagnostics_tracks_any_preset_without_registry_reads_per_event(registry_file,tmp_path,monkeypatch):
    from strategy_recipe import registry
    from module_diagnostics import Diagnostics,module_name
    import module_status_state
    sink=Diagnostics(tmp_path)
    sink.configure_specials(['MY_ALPHA'])
    sink.set_detail(True)
    assert module_status_state.display_name('MY_ALPHA')=='공통 전략 MY_ALPHA'
    def unexpected():raise AssertionError('registry read during event logging')
    monkeypatch.setattr(registry,'list_presets',unexpected)
    try:
        sink._activity('MY_ALPHA',{})
        sink.log('MY_ALPHA',logging.INFO,'조건 감시')
        record=logging.LogRecord('test',logging.INFO,'folder/MY_ALPHA.py',1,'파일 추적',(),None)
        assert module_name(record,sink.states)=='MY_ALPHA'
        sink.handle(record)
        data=sink.snapshot('ALL')
        assert data['modules']['MY_ALPHA']['monitoring'] is True
        assert module_status_state.display_state('MY_ALPHA',data)=='연결중'
        assert any('조건 감시' in text for _,text in data['lines'])
        assert any('파일 추적' in text for _,text in data['lines'])
    finally:sink.close()


def test_virtual_entry_all_zero_rows_use_actual_preset_ids(registry_file,tmp_path,monkeypatch):
    # 수정본137: a virtual entry runs one strategy with an explicit entry policy (no automatic mode).
    # A run without alerts still names that preset in every zero row.
    from event_backtest import virtual_entry
    from event_backtest.virtual_contract import immediate_virtual_entry
    monkeypatch.setattr(virtual_entry,'load_alerts',lambda _: ([],0))
    monkeypatch.setattr(virtual_entry,'pricing',lambda *_:{'spread_price':0})
    result=virtual_entry.calculate(tmp_path/'unused.csv',[],tmp_path,
                                   {'symbol':'TEST.cash','start':'2026-01-01','end':'2026-01-02','strategies':['MY_ALPHA'],
                                    'virtual_entry':immediate_virtual_entry()},
                                   {},tmp_path)
    assert {row['strategy'] for row in result['summary']}=={'MY_ALPHA'}
    assert len(result['summary'])==len(virtual_entry.RATIOS)
    assert all(row['entries']==0 and row['total_r']==0 for row in result['summary'])


def test_changed_registry_settings_remain_readable_but_cannot_start_other_presets(registry_file,tmp_path,monkeypatch):
    import importlib.util
    from unittest.mock import Mock
    spec=importlib.util.spec_from_file_location('_registry_control84',ROOT/'Part1/live_control.py')
    control=importlib.util.module_from_spec(spec); spec.loader.exec_module(control)
    path=tmp_path/'special_settings.json'
    monkeypatch.setattr(control,'SPECIAL_SETTINGS_PATH',path)
    start=Mock(return_value=(True,'started'))
    monkeypatch.setattr(control,'start_program',start)
    path.write_text(json.dumps({'specials':{'MY_ALPHA':{'enabled':False}}}),encoding='utf-8')
    before=path.read_bytes()
    assert control.start_live()==(True,'started')
    assert start.call_args.kwargs['enabled_specials']==set()
    start.reset_mock()
    registry_file(['ANY_NEW_ID'])
    assert control.load_special_settings()=={'MY_ALPHA':{'enabled':False}}
    with pytest.raises(control.SpecialSettingsError,match='전략 설정을 확인') as error:
        control.start_live()
    assert error.value.kind=='registry'
    start.assert_not_called()
    assert path.read_bytes()==before
    control.save_special_settings({'ANY_NEW_ID':{'enabled':True}})
    assert control.start_live()==(True,'started')
    assert start.call_args.kwargs['enabled_specials']=={'ANY_NEW_ID'}


def test_unknown_checkpoint_file_is_opaque_without_replacing_valid_oz_state():
    import monitor_OZ
    from durable_protocol import identity
    from oz_engine.runtime import OZRuntime
    name='oz_observed_'+identity('TEST','NORMAL','BREAKER')[:24]+'.json'
    raw=json.dumps({'version':1,'symbol':'TEST','profile':['NORMAL','BREAKER'],'observed':{}})
    extra='unused_saved_state.json'
    untrusted=json.dumps({'symbol':'OTHER','profile':['BLIND','OZ'],'observed':{'sentinel':123}})
    inputs={name:raw,extra:untrusted}
    runtime=OZRuntime(monitor_OZ,{},inputs)
    files=runtime.export_files()
    # Observed profiles are restored lazily when that profile is requested.
    # An unknown filename must remain opaque and leave the valid input intact.
    assert files[name]==raw and files[extra]==untrusted
    assert ('OTHER','BLIND','OZ') not in runtime.profiles
    assert inputs=={name:raw,extra:untrusted}
