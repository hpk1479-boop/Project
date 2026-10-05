"""Bundled GGUF runner and direct LoRA selection, without loading models."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'Part3'))
from common_ai.local_gguf import DEFAULT_LLAMA_SERVER_PATH, LocalGGUF, check_files
from common_ai.provider import configured_settings
from lab import server, unified_settings


@pytest.mark.parametrize('runner', [None, ''])
def test_runner_defaults_without_choosing_a_model(runner):
    source = {'provider': 'local_gguf', 'llama_server_path': runner}
    value = configured_settings(source)
    assert value['llama_server_path'] == DEFAULT_LLAMA_SERVER_PATH
    assert not value.get('gguf_model_path')
    assert source == {'provider': 'local_gguf', 'llama_server_path': runner}
    with pytest.raises(ValueError, match='모델 파일'):
        LocalGGUF(value)


def test_model_only_settings_use_bundled_runner():
    value = LocalGGUF({'provider': 'local_gguf', 'gguf_model_path': 'models/gguf/chosen.gguf'})
    assert value.settings['llama_server_path'] == DEFAULT_LLAMA_SERVER_PATH
    assert value.model == 'models/gguf/chosen.gguf'


@pytest.mark.parametrize('runner', ['runtime/custom/llama-server.exe', 'runtime\\custom\\llama-server.exe'])
def test_explicit_runner_override_is_preserved(runner):
    value = configured_settings({'provider': 'local_gguf', 'llama_server_path': runner})
    assert value['llama_server_path'] == 'runtime/custom/llama-server.exe'


def test_default_runner_follows_project_relocation(tmp_path):
    root = tmp_path / 'project'
    model = root / 'models/gguf/chosen.gguf'
    model.parent.mkdir(parents=True)
    model.write_bytes(b'GGUF' + struct.pack('<IQQ', 3, 0, 0))
    runner = root / DEFAULT_LLAMA_SERVER_PATH
    runner.parent.mkdir(parents=True)
    runner.write_bytes(b'test executable; never run')
    source = {'provider': 'local_gguf', 'gguf_model_path': 'models/gguf/chosen.gguf'}
    before = check_files(source, root)
    moved = tmp_path / '다른 위치'
    root.rename(moved)
    after = check_files(source, moved)
    assert [p.relative_to(root).as_posix() if p else None for p in before] == [
        p.relative_to(moved).as_posix() if p else None for p in after]
    assert all(p is None or p.is_file() for p in after)
    assert 'llama_server_path' not in source
    (moved / DEFAULT_LLAMA_SERVER_PATH).unlink()
    with pytest.raises(ValueError, match='GGUF 실행기가 없습니다'):
        check_files(source, moved)


def test_save_model_only_and_reset_custom_runner(tmp_path, monkeypatch):
    settings = tmp_path / 'settings/ai_settings.json'
    monkeypatch.setattr(server, 'AI_SETTINGS', settings)
    monkeypatch.setattr(server, 'AI_SESSIONS', {})
    monkeypatch.setattr(server, 'BACKTEST_COMMAND_SESSIONS', {})
    from common_ai.client import Client
    from common_ai.model_runtime import RUNTIME
    monkeypatch.setattr(Client, 'invalidate', Mock())
    monkeypatch.setattr(RUNTIME, 'invalidate', Mock())
    unified_settings.save_ai({'provider': 'local_gguf', 'gguf_model_path': 'models/gguf/chosen.gguf'})
    assert json.loads(settings.read_text('utf-8'))['llama_server_path'] == DEFAULT_LLAMA_SERVER_PATH
    unified_settings.save_ai({'llama_server_path': 'runtime/custom/llama-server.exe'})
    assert server.ai_settings()['llama_server_path'] == 'runtime/custom/llama-server.exe'
    server.AI_SESSIONS['dialogue'] = object()
    unified_settings.save_ai({'llama_server_path': ''})
    saved = json.loads(settings.read_text('utf-8'))
    assert saved['llama_server_path'] == DEFAULT_LLAMA_SERVER_PATH
    assert saved['gguf_model_path'] == 'models/gguf/chosen.gguf'
    assert not server.AI_SESSIONS


UI_CASES = r'''
let config={live:[],part2:{},connections:{},ai:{provider:'local_gguf',timeout:90,
 gguf_model_path:'models/gguf/chosen.gguf',gguf_context_size:16384,gguf_gpu_layers:0,
 base_model:'saved/base',adapter_path:'models/saved_lora',load_in_4bit:false,
 model:'saved:tag',gemini_model:'saved-gemini',gemini_api_key_configured:true}};
if(scenario==='saved_lora')config.ai.provider='local_lora';
if(scenario==='reset_runner')config.ai.llama_server_path='runtime/custom/llama-server.exe';
if(scenario==='lora_validation')delete config.ai.adapter_path;
const calls=[];
const context=vm.createContext({console,document,window:{dispatchEvent(){}},
 Event:class{},Option:Element,setInterval:()=>1,setTimeout:()=>1,clearTimeout(){},
 api:async(route,body)=>{
  calls.push({route,body:body&&JSON.parse(JSON.stringify(body))});
  if(route.startsWith('mo/live/status'))return {modules:{},lines:[]};
  if(route==='ai/models')return {available:true,models:['saved:tag']};
  if(route==='ai/gguf-models')return {directory:'models/gguf',models:[
   {name:'chosen.gguf',path:'models/gguf/chosen.gguf',bytes:24},
   {name:'next.gguf',path:'models/gguf/next.gguf',bytes:24}]};
  assert.equal(route,'mo/settings');
  if(body){Object.assign(config.ai,body.changes);return {ok:true,message:'저장 완료'};}
  return JSON.parse(JSON.stringify(config));
 }});
const run=code=>vm.runInContext(code,context),get=id=>document.getElementById(id);
const field=key=>get('settings-ai-fields').querySelector('[data-setting="'+key+'"]');
const writes=()=>calls.filter(x=>x.route==='mo/settings'&&x.body);
const active=()=>run('moSelectedAIProvider(document.getElementById("settings-ai-fields"))');
const selectProvider=async value=>{field('provider').value=value;await field('provider').listeners.change[0]();await new Promise(resolve=>setImmediate(resolve));};
(async()=>{
 run(fs.readFileSync(root+'/unified.js','utf8'));await new Promise(resolve=>setImmediate(resolve));await run('moView("settings")');
 assert.deepEqual(field('provider').options.map(x=>x.value),['gemini','ollama','local_gguf','local_lora','disabled']);
 assert.equal(get('ai-settings-details'),null);
 assert.equal(get('ai-settings-details'),null);
 assert.equal(field('llama_server_path').parentElement.parentElement.id,'ai-gguf-advanced-fields');
 assert.equal(field('llama_server_path').required,false);
 assert.equal(field('base_model').closest('#ai-settings-main-row'),get('ai-settings-main-row'));
 if(scenario==='model_only'){
  get('ai-gguf-model-select').value='models/gguf/next.gguf';
  await get('ai-gguf-model-select').onchange();
  await run('moSettingsSave("ai")');
  assert.deepEqual(writes().at(-1).body.changes,{gguf_model_path:'models/gguf/next.gguf'});
 }else if(scenario==='reset_runner'){
  field('llama_server_path').value='';await run('moSettingsSave("ai")');
  assert.deepEqual(writes().at(-1).body.changes,{llama_server_path:''});
 }else if(scenario==='lora_switch'){
  await selectProvider('local_lora');
  assert.equal(active(),'local_lora');assert.equal(field('provider').disabled,false);
  assert.equal(get('ai-local-lora-fields').classList.contains('hidden'),false);assert.equal(writes().length,0);
  await run('moSettingsSave("ai")');
  assert.deepEqual(writes().at(-1).body.changes,{provider:'local_lora'});
  assert.equal(field('provider').value,'local_lora');assert.equal(get('ai-settings-details'),null);
  await selectProvider('ollama');
  assert.equal(field('provider').disabled,false);
  field('provider').value='ollama';await field('provider').listeners.change[0]();await new Promise(resolve=>setImmediate(resolve));
  await run('moSettingsSave("ai")');
  assert.deepEqual(writes().at(-1).body.changes,{provider:'ollama'});
  assert.equal(config.ai.base_model,'saved/base');assert.equal(config.ai.adapter_path,'models/saved_lora');
  assert.equal(config.ai.gguf_model_path,'models/gguf/chosen.gguf');
 }else if(scenario==='saved_lora'){
  assert.equal(active(),'local_lora');assert.equal(field('provider').value,'local_lora');
  assert.equal(get('ai-local-lora-fields').classList.contains('hidden'),false);
  await run('moSettingsSave("ai")');assert.equal(writes().length,0);
  field('timeout').value='120';await run('moSettingsSave("ai")');
  assert.deepEqual(writes().at(-1).body.changes,{timeout:120});assert.equal(config.ai.provider,'local_lora');
 }else if(scenario==='lora_validation'){
  await selectProvider('local_lora');await assert.rejects(()=>run('moSettingsSave("ai")'),/LoRA 학습 파일/);
  assert.equal(writes().length,0);
 }else if(scenario==='no_details'){
  assert.equal(get('ai-lora-enabled'),null);
  run('moCollapseSettingsDetails()');
  assert.equal(get('ai-settings-details'),null);assert.equal(get('ai-settings-details'),null);
  assert.equal(field('gguf_model_path').value,'models/gguf/chosen.gguf');
 }else if(scenario==='invalid_runner'){
  field('llama_server_path').value='../outside.exe';
  await assert.rejects(()=>run('moSettingsSave("ai")'),/프로젝트 안/);assert.equal(writes().length,0);
 }
 console.log('settings75 '+scenario+' PASS');
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
'''


@pytest.mark.parametrize('scenario', ['model_only', 'reset_runner', 'lora_switch', 'saved_lora',
                                    'lora_validation', 'no_details', 'invalid_runner'])
def test_advanced_settings_browser_events(tmp_path, scenario):
    path = Path(__file__).with_name('test_settings_layout68.py')
    spec = importlib.util.spec_from_file_location('settings_dom75', path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    script = fixture.SCRIPT.split('const liveKeys=')[0] + UI_CASES
    node = shutil.which('node')
    assert node, 'Node.js is required by current web verification'
    result = subprocess.run([node, '-e', script, str(ROOT / 'Part3/web'), scenario], cwd=tmp_path,
                            capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f'settings75 {scenario} PASS' in result.stdout
