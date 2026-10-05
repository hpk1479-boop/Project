"""GGUF settings and web behavior without model files, a browser, or MT5."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part3'))
from lab import server, unified_settings
from lab.ai import model_runtime, provider
from common_ai.client import Client


GGUF = {'provider': 'local_gguf', 'gguf_model_path': 'models/strategy.gguf',
        'llama_server_path': 'runtimes/llama/llama-server.exe',
        'gguf_context_size': 16384, 'gguf_gpu_layers': 0, 'timeout': 90}


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    project = tmp_path / 'source_project'
    settings = project / 'settings/ai_settings.json'
    monkeypatch.setattr(server, 'ROOT', project / 'Part3')
    monkeypatch.setattr(server, 'AI_SETTINGS', settings)
    monkeypatch.setattr(server, '_AI_CLIENT', None)
    monkeypatch.setattr(server, 'AI_SESSIONS', {})
    monkeypatch.setattr(server, 'BACKTEST_COMMAND_SESSIONS', {})
    monkeypatch.setattr(server, 'RESEARCH_SESSIONS', {})
    monkeypatch.setattr(model_runtime.RUNTIME, 'invalidate', Mock())
    for method in ('_start', '_request'):
        monkeypatch.setattr(Client, method, Mock(side_effect=AssertionError('No common AI service in this fixture')))
    yield settings
    server.close_ai_client()
    Client._start.assert_not_called()
    Client._request.assert_not_called()


def test_gguf_settings_save_read_and_factory_use_selected_files(isolated_settings):
    unified_settings.save_ai(GGUF)
    current = server.ai_settings()
    saved = json.loads(isolated_settings.read_text('utf-8'))
    for key, value in GGUF.items():
        assert saved[key] == current[key] == value
    from lab.ai.local_gguf import LocalGGUF
    assert isinstance(provider.from_settings(current), LocalGGUF)


def test_provider_switch_preserves_inactive_model_settings(isolated_settings):
    initial = {**GGUF, 'model': 'previous:4b', 'base_model': 'test/base',
               'adapter_path': 'models/adapter', 'load_in_4bit': False}
    unified_settings.save_ai(initial)
    for target in ('ollama', 'local_lora', 'local_gguf'):
        unified_settings.save_ai({'provider': target})
        saved = server.ai_settings()
        assert saved['provider'] == target
        for key, value in initial.items():
            if key != 'provider':
                assert saved[key] == value


@pytest.mark.parametrize('changes', [
    {'gguf_model_path': ''}, {'llama_server_path': '   '},
    {'gguf_model_path': '../outside.gguf'}, {'llama_server_path': 'C:/server.exe'},
    {'gguf_chat_template_path': '/outside/template.jinja'},
    {'gguf_context_size': 0}, {'gguf_gpu_layers': -2}, {'gguf_threads': 0},
])
def test_invalid_gguf_settings_preserve_saved_values_and_sessions(isolated_settings, changes):
    unified_settings.save_ai(GGUF)
    before = isolated_settings.read_bytes()
    session = object()
    server.AI_SESSIONS['preserve'] = session
    with pytest.raises(ValueError):
        unified_settings.save_ai(changes)
    assert isolated_settings.read_bytes() == before
    assert server.AI_SESSIONS['preserve'] is session


def test_saved_gguf_locations_resolve_after_project_move(isolated_settings, tmp_path, monkeypatch):
    from lab.ai.local_gguf import check_files
    old_root = isolated_settings.parents[1]
    for path in (old_root / GGUF['gguf_model_path'], old_root / GGUF['llama_server_path']):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'GGUF' + b'\x00' * 20 if path.suffix == '.gguf' else b'execution fixture')
    unified_settings.save_ai(GGUF)
    before = isolated_settings.read_bytes()
    moved = tmp_path / 'moved_project'
    old_root.rename(moved)
    monkeypatch.setattr(server, 'ROOT', moved / 'Part3')
    monkeypatch.setattr(server, 'AI_SETTINGS', moved / 'settings/ai_settings.json')
    saved = json.loads(server.AI_SETTINGS.read_text('utf-8'))
    model, executable, _ = check_files(saved, root=moved)
    assert model == moved / GGUF['gguf_model_path']
    assert executable == moved / GGUF['llama_server_path']
    assert server.AI_SETTINGS.read_bytes() == before
    assert not any(str(old_root) in str(value) for value in saved.values())
    assert server.ai_settings()['gguf_model_path'] == GGUF['gguf_model_path']
    assert server.ai_agent('moved').provider.client.root == moved.resolve()


def _fixture_script(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    return fixture.SCRIPT


SETTINGS_CASES = r'''
let config={live:[],part2:{},connections:{},ai:{provider:'local_gguf',model:'previous:4b',
 base_model:'test/base',adapter_path:'models/adapter',load_in_4bit:false,
 gguf_model_path:'models/strategy.gguf',llama_server_path:'runtimes/llama/llama-server.exe',
 gguf_context_size:16384,gguf_gpu_layers:0,timeout:90}};
const calls=[];
let catalog=[{name:'Qwen-4B.gguf',path:'models/strategy.gguf',bytes:1000},
 {name:'Qwen-9B.gguf',path:'models/gguf/Qwen-9B.gguf',bytes:2000},
 {name:'replacement model.gguf',path:'models/gguf/replacement model.gguf',bytes:800}];
if(scenario==='no_selection')config.ai.gguf_model_path='';
if(scenario==='selected_missing')config.ai.gguf_model_path='models/custom.gguf';
if(scenario==='safe_filename')catalog[0].name='<img src=x onerror=bad()>-4B.gguf';
const context=vm.createContext({console,document,window:{dispatchEvent(){},addEventListener(){}},
 Event:class{},Option:Element,setInterval:()=>1,setTimeout:()=>1,clearTimeout(){},
 api:async(route,body)=>{
  calls.push({route,body:body&&JSON.parse(JSON.stringify(body))});
  if(route.startsWith('mo/live/status'))return {modules:{},lines:[]};
  if(route==='ai/models')return {available:true,models:['previous:4b']};
  if(route==='ai/gguf-models'){
   if(scenario==='list_failure')throw Error('synthetic inventory failure');
   return {directory:'models/gguf',models:scenario==='empty_list'?[]:catalog,selected:config.ai.gguf_model_path};
  }
  assert.equal(route,'mo/settings');
  if(body){assert.equal(body.group,'ai');Object.assign(config.ai,body.changes);return {ok:true,message:'저장 완료'};}
  return JSON.parse(JSON.stringify(config));
 }});
const run=code=>vm.runInContext(code,context),get=id=>document.getElementById(id);
const fields=()=>get('settings-ai-fields'),field=key=>fields().querySelector('[data-setting="'+key+'"]');
const writes=()=>calls.filter(x=>x.route==='mo/settings'&&x.body);
const switchTo=async provider=>{
 field('provider').value=provider;await field('provider').listeners.change[0]();
 await new Promise(resolve=>setImmediate(resolve));};
(async()=>{
 run(fs.readFileSync(root+'/unified.js','utf8'));await new Promise(resolve=>setImmediate(resolve));await run('moView("settings")');
 if(scenario==='display'){
  assert.equal(get('ai-local-gguf-fields').classList.contains('hidden'),false);
  assert.equal(get('ai-local-lora-fields').classList.contains('hidden'),true);
  assert.equal(get('ai-ollama-fields').classList.contains('hidden'),true);
  assert.equal(calls.filter(x=>x.route==='ai/models').length,0);
  assert.equal(field('gguf_model_path').required,true);assert.equal(field('llama_server_path').required,false);
  assert.equal(field('llama_server_path').closest('details'),null);
  for(const key of ['gguf_model_path','gguf_context_size','gguf_gpu_layers','gguf_threads','gguf_chat_template_path','max_new_tokens','timeout'])
   assert.equal(field(key).closest('details'),null);
  assert.equal(get('ai-gguf-model-select').closest('details'),null);
  assert.deepEqual(get('ai-gguf-model-select').options.slice(0,catalog.length).map(x=>x.textContent),catalog.map(x=>x.name));
  assert.doesNotMatch(get('ai-gguf-model-select').textContent,/저사양용|고사양용|모델 규모 확인 필요/);
  assert.equal(new Set(fields().querySelectorAll('[data-setting]').map(x=>x.dataset.setting)).size,fields().querySelectorAll('[data-setting]').length);
  await run('moSettingsSave("ai")');assert.equal(writes().length,0);
 }else if(scenario==='select_model'){
  const select=get('ai-gguf-model-select');select.value='models/gguf/Qwen-9B.gguf';select.onchange();
  assert.equal(field('gguf_model_path').value,'models/gguf/Qwen-9B.gguf');assert.equal(writes().length,0);
  await run('moSettingsSave("ai")');
  assert.deepEqual(writes().at(-1).body.changes,{gguf_model_path:'models/gguf/Qwen-9B.gguf'});
  assert.equal(calls.some(x=>x.route==='ai/chat'),false);
 }else if(scenario==='no_selection'){
  assert.equal(get('ai-gguf-model-select').value,'__unconfigured_gguf__');assert.equal(field('gguf_model_path').value,'');
  assert.equal(get('ai-gguf-model-select').selectedOptions[0].disabled,true);
  field('timeout').value='120';await assert.rejects(()=>run('moSettingsSave("ai")'),/GGUF 모델 파일 위치/);
  assert.equal(writes().length,0);
 }else if(scenario==='selected_missing'){
  assert.equal(get('ai-gguf-model-select').value,'models/custom.gguf');
  assert.equal(get('ai-gguf-model-select').selectedOptions[0].textContent,'custom.gguf');
  assert.match(get('ai-gguf-list-status').textContent,/현재 설정한 파일은 목록에 없습니다/);
  assert.equal(field('gguf_model_path').value,'models/custom.gguf');assert.equal(writes().length,0);
 }else if(scenario==='safe_filename'){
  assert.match(get('ai-gguf-model-select').textContent,/<img src=x onerror=bad\(\)>/);
  assert.equal(get('ai-local-gguf-fields').querySelectorAll('img').length,0);
 }else if(scenario==='refresh_manual'){
  field('gguf_model_path').value='models/manual.gguf';field('gguf_model_path').listeners.input[0]();
  await get('ai-gguf-model-refresh').onclick();assert.equal(field('gguf_model_path').value,'models/manual.gguf');
  assert.equal(get('ai-gguf-model-select').value,'models/manual.gguf');assert.equal(writes().length,0);
 }else if(scenario==='refresh'){
  catalog.push({name:'new model v3.gguf',path:'models/gguf/new model v3.gguf',bytes:5000});
  await get('ai-gguf-model-refresh').onclick();assert.equal(get('ai-gguf-model-select').options[3].textContent,'new model v3.gguf');
  assert.equal(get('ai-gguf-model-select').value,'models/strategy.gguf');assert.equal(writes().length,0);
 }else if(['empty_list','list_failure'].includes(scenario)){
  assert.match(get('ai-gguf-list-status').textContent,scenario==='empty_list'?/모델이 없습니다/:/조회에 실패/);
  assert.equal(field('gguf_model_path').value,'models/strategy.gguf');
  assert.equal(field('gguf_model_path').closest('label').classList.contains('hidden'),false);
  assert.equal(writes().length,0);
 }else if(scenario==='manual_fallback'){
  field('gguf_model_path').value='models/custom.gguf';
  field('gguf_model_path').listeners.input[0]();assert.equal(get('ai-gguf-model-select').value,'models/custom.gguf');
  await run('moSettingsSave("ai")');assert.deepEqual(writes().at(-1).body.changes,{gguf_model_path:'models/custom.gguf'});
 }else if(scenario==='save_typed'){
  field('gguf_model_path').value='models/changed.gguf';field('gguf_gpu_layers').value='-1';field('gguf_threads').value='8';field('max_new_tokens').value='999';
  await run('moSettingsSave("ai")');
  assert.deepEqual(writes().at(-1).body,{group:'ai',changes:{gguf_model_path:'models/changed.gguf',gguf_gpu_layers:-1,gguf_threads:8,max_new_tokens:999}});
  assert.equal(config.ai.model,'previous:4b');assert.equal(config.ai.adapter_path,'models/adapter');
  field('gguf_threads').value='';field('max_new_tokens').value='';await run('moSettingsSave("ai")');
  assert.deepEqual(writes().at(-1).body.changes,{gguf_threads:null,max_new_tokens:null});
 }else if(scenario==='switch'){
  await switchTo('local_lora');assert.equal(get('ai-local-lora-fields').classList.contains('hidden'),false);
  assert.equal(field('max_new_tokens').closest('#ai-local-lora-fields'),get('ai-local-lora-fields'));
  assert.equal(field('timeout').closest('details'),null);
  await run('moSettingsSave("ai")');assert.deepEqual(writes().at(-1).body.changes,{provider:'local_lora'});
  await switchTo('ollama');assert.equal(field('model').required,true);assert.equal(calls.filter(x=>x.route==='ai/models').length,1);
  await run('moSettingsSave("ai")');assert.deepEqual(writes().at(-1).body.changes,{provider:'ollama'});
  await switchTo('local_gguf');assert.equal(field('gguf_model_path').value,'models/strategy.gguf');
  assert.equal(config.ai.base_model,'test/base');assert.equal(config.ai.gguf_model_path,'models/strategy.gguf');
 }else{
  const errors={missing_model:['gguf_model_path','',/GGUF 모델 파일 위치/],outside:['llama_server_path','..\\server.exe',/상대 위치/],
   zero_context:['gguf_context_size','0',/양의 정수/],negative_threads:['gguf_threads','-1',/양의 정수/],bad_layers:['gguf_gpu_layers','-2',/0 이상의 정수/]};
  const [key,value,message]=errors[scenario];field(key).value=value;
  await assert.rejects(()=>run('moSettingsSave("ai")'),message);assert.equal(writes().length,0);
 }
 console.log('GGUF settings '+scenario+' PASS');
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
'''


@pytest.mark.parametrize('scenario', [
    'display', 'select_model', 'no_selection', 'selected_missing', 'safe_filename', 'refresh_manual',
    'refresh', 'empty_list', 'list_failure', 'manual_fallback',
    'save_typed', 'switch', 'missing_model', 'outside',
    'zero_context', 'negative_threads', 'bad_layers',
])
def test_gguf_settings_browser_events(tmp_path, scenario):
    script = _fixture_script('test_settings_layout68').split('const liveKeys=')[0] + SETTINGS_CASES
    node = shutil.which('node')
    assert node, 'Node.js is required by current web verification'
    result = subprocess.run([node, '-e', script, str(ROOT / 'Part3/web'), scenario],
                            cwd=tmp_path, capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f'GGUF settings {scenario} PASS' in result.stdout


CHAT_CASES = r'''
Element.prototype.setAttribute=function(name,value){this[name]=value;};
Element.prototype.remove=function(){for(const el of elements.values())el.children=el.children.filter(child=>child!==this);};
context.document.querySelectorAll=()=>[];
context.window.dispatchEvent=()=>true;
context.CustomEvent=class{constructor(type,options){this.type=type;this.detail=options.detail;}};
const scenario=process.argv[2];
let releaseChat;
context.fetch=async(path,request={})=>{
 const data=request.body?JSON.parse(request.body):null;calls.push({path,data});
 if(path==='/api/ai/settings')return {ok:true,status:200,json:async()=>({settings:{provider:'local_gguf',
  gguf_model_path:scenario==='unconfigured'?'':'models/strategy.gguf',llama_server_path:'runtimes/llama/llama-server.exe'}})};
 if(path==='/api/ai/chat')return new Promise(resolve=>releaseChat=()=>resolve({ok:false,status:400,
  json:async()=>({error:'GGUF 모델 파일이 없습니다. 설정에서 파일 위치를 확인하세요.'})}));
 return {ok:true,status:200,json:async()=>({ok:true})};
};
(async()=>{
 domEvents.DOMContentLoaded();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(calls.filter(x=>x.path==='/api/ai/settings').length,1);
 assert.match(fs.readFileSync(process.argv[1]+'/index.html','utf8'),/id="ai-reset"[^>]*>초기화<\/button>/);
  get('ai-text').value='전략 연구';const pending=get('ai-send').events.click();
  assert.equal(get('ai-send').disabled,true);assert.equal(get('ai-reset').disabled,true);
  const waiting=get('ai-messages').children.find(x=>x.textContent.includes('GGUF 응답을 기다리는 중'));
  assert(waiting);assert.equal(waiting.role,'status');
  releaseChat();await pending;
  assert.equal(get('ai-send').disabled,false);assert.equal(get('ai-reset').disabled,false);
  assert.match(get('ai-messages').children.at(-1).textContent,/GGUF 모델 파일이 없습니다/);
  assert.equal(get('ai-messages').children.some(x=>x.textContent.includes('GGUF 응답을 기다리는 중')),false);
  assert.equal(get('ai-pending').flags.has('hidden'),true);
  assert.deepEqual(calls.filter(x=>x.path==='/api/ai/chat').map(x=>x.data),[
   {session:'current-session',message:'전략 연구',research:true,mode:'strategy'}]);
 console.log('GGUF chat '+scenario+' PASS');
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
'''


@pytest.mark.parametrize('scenario', ['unconfigured', 'loading_failure'])
def test_gguf_chat_preparation_notice_and_failure_recovery(tmp_path, scenario):
    script = _fixture_script('test_ai_reset72').split('(async()=>{')[0] + CHAT_CASES
    node = shutil.which('node')
    assert node, 'Node.js is required by current web verification'
    result = subprocess.run([node, '-e', script, str(ROOT / 'Part3/web'), scenario],
                            cwd=tmp_path, capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f'GGUF chat {scenario} PASS' in result.stdout
