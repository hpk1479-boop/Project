"""Inline AI selection and real disabled-call boundary; no model or remote API."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part3')]
from common_ai.client import Client
from common_ai.process_identity import identity
from common_ai.provider import configured_settings, from_settings
from common_ai.service import Service
from common_ai.settings import read_settings, masked_settings
from lab import server, unified_settings
from lab.ai.shared_provider import SharedProvider
from test_settings_layout68 import SCRIPT as DOM


def store(root, value):
    path = root / 'settings/ai_settings.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


@pytest.mark.parametrize('data', [{}, {'model': None}, {'gguf_model_path': 'models/gguf/saved.gguf'}])
def test_default_gemini_does_not_choose_model_or_key(data):
    result = configured_settings(data)
    assert result['provider'] == 'gemini'
    assert not result.get('gemini_model') and not result.get('gemini_api_key')
    assert all(result[key] == value for key, value in data.items())


def test_legacy_ollama_model_only_settings_keep_existing_selection():
    assert configured_settings({'model': 'saved:tag'})['provider'] == 'ollama'


@pytest.mark.parametrize('provider', ['gemini', 'ollama', 'local_gguf', 'disabled'])
def test_explicit_selection_survives_relocation(tmp_path, provider):
    first = tmp_path / 'portable'
    store(first, {'provider': provider, 'model': 'saved:tag',
                  'gguf_model_path': 'models/gguf/saved.gguf'})
    before = read_settings(first)
    moved = tmp_path / 'moved'
    first.rename(moved)
    assert read_settings(moved) == before
    assert before['provider'] == provider


@pytest.mark.parametrize('role', ['watch', 'strategy', 'backtest'])
def test_disabled_client_does_not_start_service(tmp_path, monkeypatch, role):
    store(tmp_path, {'provider': 'disabled'})
    client = Client(tmp_path)
    connect = Mock(side_effect=AssertionError('disabled must not connect'))
    monkeypatch.setattr(client, '_connect', connect)
    with pytest.raises(ValueError, match='AI 사용이 꺼져'):
        client.chat([{'role': 'user', 'content': '전략'}], [], role=role)
    connect.assert_not_called()
    assert not (tmp_path / 'runtime').exists()
    client.close()


def test_disabled_adapter_can_be_constructed_for_manual_table(tmp_path):
    store(tmp_path, {'provider': 'disabled'})
    selected = SharedProvider({'provider': 'disabled'}, root=tmp_path)
    assert selected.settings['provider'] == 'disabled'
    with pytest.raises(ValueError, match='AI 사용이 꺼져'):
        selected.chat([{'role': 'user', 'content': '전략'}], [])
    assert not (tmp_path / 'runtime').exists()
    selected.close()
    with pytest.raises(ValueError, match='AI 사용이 꺼져'):
        from_settings({'provider': 'disabled'})


def test_disable_save_preserves_models_key_and_invalidates_sessions(tmp_path, monkeypatch):
    settings_path = tmp_path / 'settings/ai_settings.json'
    initial = {'provider': 'gemini', 'gemini_model': 'saved-gemini',
               'gemini_api_key': 'synthetic.secret94', 'model': 'saved:tag',
               'gguf_model_path': 'models/gguf/saved.gguf', 'watch_enabled': False}
    store(tmp_path, initial)
    monkeypatch.setattr(server, 'ROOT', tmp_path / 'Part3')
    monkeypatch.setattr(server, 'AI_SETTINGS', settings_path)
    for name in ('AI_SESSIONS', 'RESEARCH_SESSIONS', 'BACKTEST_COMMAND_SESSIONS'):
        monkeypatch.setattr(server, name, {'existing': object()})
    invalidate = Mock()
    monkeypatch.setattr(Client, 'invalidate', invalidate)
    from common_ai.model_runtime import RUNTIME
    release = Mock()
    monkeypatch.setattr(RUNTIME, 'invalidate', release)
    from common_ai import gemini
    check = Mock(side_effect=AssertionError('No API call while disabling'))
    monkeypatch.setattr(gemini, 'check_api_key', check)
    unified_settings.save_ai({'provider': 'disabled'})
    saved = json.loads(settings_path.read_text('utf-8'))
    assert saved['provider'] == 'disabled'
    assert all(saved[key] == value for key, value in initial.items() if key != 'provider')
    assert not any((server.AI_SESSIONS, server.RESEARCH_SESSIONS, server.BACKTEST_COMMAND_SESSIONS))
    invalidate.assert_called_once()
    release.assert_called_once()
    check.assert_not_called()
    assert 'synthetic.secret94' not in json.dumps(masked_settings(saved))
    unified_settings.save_ai({'provider': 'ollama'})
    assert read_settings(tmp_path)['model'] == 'saved:tag'
    assert read_settings(tmp_path)['gemini_api_key'] == 'synthetic.secret94'


def test_service_rejects_direct_disabled_call_and_releases_old_model(tmp_path):
    store(tmp_path, {'provider': 'ollama', 'model': 'saved:tag'})
    runtime = SimpleNamespace(generation=0, loaded=True)
    def release():
        runtime.generation += 1
        runtime.loaded = False
    runtime.invalidate = release
    factory = Mock(side_effect=AssertionError('No provider may load'))
    service = Service(tmp_path, provider_factory=factory, runtime=runtime)
    service.clients['test-client'] = {'pid': os.getpid(), 'created': identity(os.getpid())}
    service.provider = object()
    try:
        store(tmp_path, {'provider': 'disabled'})
        service._sync_settings()
        assert runtime.loaded is False and service.provider is None
        payload = {'client_id': 'test-client', 'generation': runtime.generation,
                   'messages': [{'role': 'user', 'content': '전략'}], 'tools': [], 'role': 'strategy'}
        with pytest.raises(ValueError, match='AI 사용이 꺼져'):
            service.chat(payload)
        factory.assert_not_called()
        assert not service.turn_lock.locked()
    finally:
        service.server.server_close()


UI = r'''
const Option=(text,value)=>{const item=new Element('option');item.textContent=text;item.value=value;return item;};
let config={live:[],part2:{},connections:{},ai:{provider:scenario==='default'?undefined:scenario,
 model:'saved:tag',gemini_model:'saved-gemini',gemini_api_key_configured:true,
 gguf_model_path:'models/gguf/saved.gguf',
 timeout:90,gguf_context_size:16384,gguf_gpu_layers:0}};
const calls=[];
const context=vm.createContext({console,document,Option:class {constructor(text,value){return Option(text,value);}},
 window:{dispatchEvent(){},addEventListener(){}},Event:class{},setInterval:()=>1,setTimeout:()=>1,clearTimeout(){},
 api:async(route,body)=>{
 calls.push({route,body});
 if(route.startsWith('mo/live/status'))return {modules:{},lines:[]};
 if(route==='ai/gemini-models')return {available:true,models:['saved-gemini','new-gemini']};
 if(route==='ai/models')return {available:true,models:['saved:tag','new:tag']};
 if(route==='ai/gguf-models')return {models:[{name:'saved.gguf',path:'models/gguf/saved.gguf'}]};
 assert.equal(route,'mo/settings');if(body){Object.assign(config.ai,body.changes);return {message:'저장 완료'};}
 return JSON.parse(JSON.stringify(config));
 }});
const run=code=>vm.runInContext(code,context),get=id=>document.getElementById(id);
const field=key=>get('settings-ai-fields').querySelector('[data-setting="'+key+'"]');
(async()=>{
 run(fs.readFileSync(root+'/unified.js','utf8'));await run('moView("settings")');
 const expected=scenario==='default'?'gemini':scenario;
 const row=get('ai-settings-main-row');
 assert.equal(field('provider').value,expected);
 assert.deepEqual(field('provider').options.map(x=>x.value),['gemini','ollama','local_gguf','disabled']);
 assert.equal(field('provider').closest('#ai-settings-main-row'),row);
 assert.equal(field('gemini_api_key').closest('#ai-settings-main-row'),row);
 assert.equal(get('ai-credential-fields').classList.contains('hidden'),expected!=='gemini');
 const slots={gemini:['ai-gemini-fields','gemini_model'],ollama:['ai-ollama-fields','model'],
  local_gguf:['ai-local-gguf-fields','gguf_model_path']};
 for(const [name,[id,key]] of Object.entries(slots)){
  assert.equal(get(id).classList.contains('hidden'),name!==expected);
  assert.equal(field(key).closest('#ai-settings-main-row'),row);
 }
 assert.equal(field('gemini_api_key').value,'');
 if(expected==='disabled'){
  // 수정본138 removed the explanatory "disabled" note; no AI route is called.
  assert.equal(calls.filter(x=>x.route.startsWith('ai/')).length,0);
 }else if(expected==='gemini'){
  const select=get('ai-gemini-model-select');select.value='new-gemini';select.onchange();
  assert.equal(field('gemini_model').value,'new-gemini');
  await run('moSettingsSave("ai")');assert.equal(config.ai.gemini_model,'new-gemini');
 }else if(expected==='ollama'){
  const select=get('ai-model-select');select.value='new:tag';select.onchange();
  assert.equal(field('model').value,'new:tag');
  field('model').value='manual:tag';field('model').listeners.input[0]();
  await run('moSettingsSave("ai")');assert.equal(config.ai.model,'manual:tag');
 }
 field('provider').value='disabled';await field('provider').listeners.change[0]();await new Promise(resolve=>setImmediate(resolve));
 await run('moSettingsSave("ai")');assert.equal(config.ai.provider,'disabled');
 assert.equal(config.ai.gguf_model_path,'models/gguf/saved.gguf');
 assert.equal(config.ai.gemini_api_key_configured,true);
 console.log('PASS settings94 '+scenario);
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
'''


@pytest.mark.parametrize('scenario', ['default', 'gemini', 'ollama', 'local_gguf', 'disabled'])
def test_inline_settings_selection_saving_and_disabled(scenario, tmp_path):
    script = DOM.split('const liveKeys=')[0] + UI
    result = subprocess.run([shutil.which('node'), '-e', script, str(ROOT / 'Part3/web'), scenario],
                            cwd=tmp_path, capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
