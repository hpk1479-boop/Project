"""Shared AI settings UI: safe keys, provider refresh, request guards, and reset."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _fixture_script(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    return fixture.SCRIPT


SETTINGS_CASES = r'''
let config={live:[],part2:{},connections:{},ai:{model:'previous:4b',max_new_tokens:777,
 gguf_model_path:'models/gguf/sample file.gguf',llama_server_path:'runtimes/llama/llama-server.exe',
 gguf_context_size:16384,gguf_gpu_layers:0,timeout:90,gemini_model:'selected-gemini-model',
 gemini_api_key_configured:true}};
const calls=[];
if(scenario.startsWith('gemini_'))config.ai.provider='gemini';
if(['provider_switch','inactive_clear'].includes(scenario))config.ai.provider='local_gguf';
if(scenario==='watch_enabled')config.ai.watch_enabled=false;
if(['gemini_new_key','gemini_missing_key'].includes(scenario))config.ai.gemini_api_key_configured=false;
const context=vm.createContext({console,document,window:{dispatchEvent(){},addEventListener(){}},
 Event:class{},Option:Element,setInterval:()=>1,setTimeout:()=>1,clearTimeout(){},
 api:async(route,body)=>{
  calls.push({route,body:body&&JSON.parse(JSON.stringify(body))});
  if(route.startsWith('mo/live/status'))return {modules:{},lines:[]};
  if(route==='ai/models')return {available:true,models:['previous:4b']};
  if(route==='ai/gguf-models')return {directory:'models/gguf',models:[
   {name:'sample file.gguf',path:'models/gguf/sample file.gguf',bytes:1000},
   {name:'replacement.gguf',path:'models/gguf/replacement.gguf',bytes:2000}]};
  // 수정본177: the key saves only through its own [확인], like the bot token.
  if(route==='mo/settings/gemini_key'){
   assert.deepEqual(Object.keys(body).sort(),['key','value']);assert.equal(body.key,'gemini_api_key');
   if(body.value!=='')config.ai.gemini_api_key_configured=body.value!==null;
   return {ok:true,key:'gemini_api_key',configured:config.ai.gemini_api_key_configured,
    message:body.value===null?'저장값을 지웠습니다':body.value?'확인하고 저장했습니다':'확인했습니다'};
  }
  assert.equal(route,'mo/settings');
  if(body){
   assert.equal(body.group,'ai');
   assert.equal(Object.hasOwn(body.changes,'gemini_api_key'),false,'the group save never carries the key');
   Object.assign(config.ai,body.changes);return {ok:true,message:'저장 완료'};
  }
  return JSON.parse(JSON.stringify(config));
 }});
const run=code=>vm.runInContext(code,context),get=id=>document.getElementById(id);
const field=key=>get('settings-ai-fields').querySelector('[data-setting="'+key+'"]');
const writes=()=>calls.filter(x=>x.route==='mo/settings'&&x.body);
const keyCalls=()=>calls.filter(x=>x.route==='mo/settings/gemini_key').map(x=>x.body);
(async()=>{
 run(fs.readFileSync(root+'/unified.js','utf8'));await new Promise(resolve=>setImmediate(resolve));await run('moView("settings")');
 assert.equal(field('gemini_api_key').type,'password');assert.equal(field('gemini_api_key').value,'');
 assert.equal(get('settings-ai-fields').textContent.includes('synthetic-key-74'),false);
 if(scenario==='default'){
  assert.equal(field('provider').value,'gemini');
  assert.equal(field('watch_enabled').value,'true');
  assert.equal(get('ai-gemini-fields').classList.contains('hidden'),false);
  assert.equal(get('ai-credential-fields').classList.contains('hidden'),false);
  assert.equal(calls.filter(x=>x.route==='ai/models').length,0);
  field('timeout').value='120';await run('moSettingsSave("ai")');
  assert.deepEqual(writes().at(-1).body.changes,{timeout:120});
 }else if(['provider_switch','inactive_clear'].includes(scenario))config.ai.provider='local_gguf';
if(scenario==='watch_enabled'){
  assert.equal(field('watch_enabled').value,'false');field('watch_enabled').value='true';
  await run('moSettingsSave("ai")');assert.deepEqual(writes().at(-1).body.changes,{watch_enabled:true});
  field('watch_enabled').value='false';await run('moSettingsSave("ai")');
  assert.deepEqual(writes().at(-1).body.changes,{watch_enabled:false});
 }else if(scenario==='gemini_preserve_key'){
  assert.equal(field('gemini_api_key').required,false);assert.equal(field('gemini_model').required,true);
  field('gemini_model').value='new-selected-model';await run('moSettingsSave("ai")');
  assert.deepEqual(writes().at(-1).body.changes,{gemini_model:'new-selected-model'});
  assert.equal(config.ai.gemini_api_key_configured,true);
 }else if(scenario==='gemini_new_key'){
  const key=field('gemini_api_key');
  assert.equal(key.required,true);assert.equal(key.placeholder,'미설정 · 예: AIza…');key.value='synthetic-key-74';
  // The group save leaves a typed key out, as it does a typed bot token, and asks for the key's [확인].
  await run('moSettingsSave("ai")');assert.equal(writes().length,0);
  assert.equal(document.querySelector('[data-save-status="ai"]').textContent,'변경한 값이 없습니다');
  field('timeout').value='120';
  await assert.rejects(()=>run('moSettingsSave("ai")'),/Gemini API 키를 넣고 옆의 확인/);assert.equal(writes().length,0);
  assert.equal(key.value,'synthetic-key-74');
  await key.confirmSave();
  assert.deepEqual(keyCalls(),[{key:'gemini_api_key',value:'synthetic-key-74'}]);
  assert.equal(key.value,'');assert.equal(key.dataset.configured,'true');assert.equal(config.ai.gemini_api_key_configured,true);
  assert.equal(key.placeholder,'설정됨 · 변경할 때만 입력');assert.equal(get('ai-gemini-key-status').textContent,'확인하고 저장했습니다');
  field('timeout').value='120';await run('moSettingsSave("ai")');assert.deepEqual(writes().at(-1).body.changes,{timeout:120});
 }else if(scenario==='gemini_recheck'){
  // An empty field with a saved key checks that key again and changes nothing.
  await field('gemini_api_key').confirmSave();
  assert.deepEqual(keyCalls(),[{key:'gemini_api_key',value:''}]);assert.equal(config.ai.gemini_api_key_configured,true);
  assert.equal(get('ai-gemini-key-status').textContent,'확인했습니다');assert.equal(writes().length,0);
 }else if(scenario==='gemini_missing_key'){
  field('timeout').value='120';await assert.rejects(()=>run('moSettingsSave("ai")'),/Gemini API 키를 넣고 옆의 확인/);assert.equal(writes().length,0);
  await field('gemini_api_key').confirmSave();assert.deepEqual(keyCalls(),[]);
 }else if(scenario==='gemini_missing_model'){
  field('gemini_model').value='';await assert.rejects(()=>run('moSettingsSave("ai")'),/Gemini 모델명/);assert.equal(writes().length,0);
 }else if(scenario==='gemini_clear_active'){
  field('gemini_api_key').closest('label').querySelector('.mo-secret-clear').checked=true;
  // The group save never deletes the key; the delete mark survives its redraw.
  field('timeout').value='120';await run('moSettingsSave("ai")');assert.deepEqual(writes().at(-1).body.changes,{timeout:120});
  assert.equal(config.ai.gemini_api_key_configured,true);
  assert.equal(field('gemini_api_key').closest('label').querySelector('.mo-secret-clear').checked,true);
  await field('gemini_api_key').confirmSave();assert.deepEqual(keyCalls().at(-1),{key:'gemini_api_key',value:null});
  assert.equal(config.ai.gemini_api_key_configured,false);assert.equal(field('provider').value,'gemini');
  assert.equal(field('gemini_api_key').placeholder,'미설정 · 예: AIza…');
 }else if(scenario==='inactive_clear'){
  field('gemini_api_key').closest('label').querySelector('.mo-secret-clear').checked=true;
  await field('gemini_api_key').confirmSave();assert.deepEqual(keyCalls().at(-1),{key:'gemini_api_key',value:null});
  assert.equal(config.ai.gemini_api_key_configured,false);assert.equal(field('provider').value,'local_gguf');
 }else if(scenario==='provider_switch'){
  field('provider').value='gemini';await field('provider').listeners.change[0]();await new Promise(resolve=>setImmediate(resolve));
  await run('moSettingsSave("ai")');assert.deepEqual(writes().at(-1).body.changes,{provider:'gemini'});
  for(const [key,value] of [['model','previous:4b'],['gguf_model_path','models/gguf/sample file.gguf']])
   assert.equal(config.ai[key],value);
  field('provider').value='local_gguf';await field('provider').listeners.change[0]();await new Promise(resolve=>setImmediate(resolve));
  await run('moSettingsSave("ai")');assert.deepEqual(writes().at(-1).body.changes,{provider:'local_gguf'});
  assert.equal(config.ai.gemini_model,'selected-gemini-model');assert.equal(config.ai.gemini_api_key_configured,true);
 }
 console.log('shared AI settings '+scenario+' PASS');
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
'''


@pytest.mark.parametrize('scenario', [
    'default', 'watch_enabled',
    'gemini_preserve_key', 'gemini_new_key', 'gemini_recheck', 'gemini_missing_key', 'gemini_missing_model',
    'gemini_clear_active', 'inactive_clear', 'provider_switch',
])
def test_shared_ai_settings_browser_events(tmp_path, scenario):
    script = _fixture_script('test_settings_layout68').split('const liveKeys=')[0] + SETTINGS_CASES
    node = shutil.which('node')
    assert node, 'Node.js is required by current web verification'
    result = subprocess.run([node, '-e', script, str(ROOT / 'Part3/web'), scenario],
                            cwd=tmp_path, capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f'shared AI settings {scenario} PASS' in result.stdout


CHAT_CASES = r'''
Element.prototype.setAttribute=function(name,value){this[name]=value;};
Element.prototype.remove=function(){for(const el of elements.values())el.children=el.children.filter(child=>child!==this);};
context.document.querySelectorAll=()=>[];
context.window.dispatchEvent=()=>true;
context.CustomEvent=class{constructor(type,options){this.type=type;this.detail=options.detail;}};
const scenario=process.argv[2];let releaseChat,chatSucceeds=false;
let selectedProvider=['default','provider_switch'].includes(scenario)?'local_gguf':scenario==='disabled'?'disabled':'gemini';
context.fetch=async(path,request={})=>{
 const data=request.body?JSON.parse(request.body):null;calls.push({path,data});
 if(path==='/api/ai/settings')return {ok:true,status:200,json:async()=>({settings:{
  provider:selectedProvider,gguf_model_path:'models/gguf/selected.gguf',gemini_model:'chosen-gemini-model',
  gemini_api_key_configured:scenario!=='gemini_missing_key'}})};
 if(path==='/api/ai/chat')return new Promise(resolve=>releaseChat=()=>resolve({ok:false,status:400,
  ...(chatSucceeds?{ok:true,status:200}:{}),json:async()=>chatSucceeds?
   {kind:'CHAT',message_ko:'공통 설정 변경 후 정상 응답'}:{error:'synthetic shared AI error'}}));
 return {ok:true,status:200,json:async()=>({ok:true})};
};
(async()=>{
 domEvents.DOMContentLoaded();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(calls.filter(x=>x.path==='/api/ai/settings').length,1);
 assert.match(fs.readFileSync(process.argv[1]+'/index.html','utf8'),/id="ai-reset"[^>]*>초기화<\/button>/);
 if(scenario==='disabled'){
  get('ai-text').value='전략 연구';await get('ai-send').events.click();
  assert.equal(get('ai-text').value,'전략 연구');
  assert.match(get('ai-messages').children.at(-1).textContent,/AI 사용이 꺼져/);
  assert.equal(calls.filter(x=>x.path==='/api/ai/chat').length,0);
  console.log('shared AI chat '+scenario+' PASS');return;
 }
 get('ai-text').value='전략 연구';const pending=get('ai-send').events.click();
 assert.equal(get('ai-messages').children.some(x=>x.textContent.includes('GGUF 응답을 기다리는 중')),selectedProvider==='local_gguf');
 releaseChat();await pending;assert.equal(get('ai-send').disabled,false);assert.equal(get('ai-reset').disabled,false);
 assert.equal(get('ai-messages').children.at(-1).textContent,'synthetic shared AI error');
 assert.equal(calls.filter(x=>x.path==='/api/ai/chat').length,1);
 if(scenario==='provider_switch'){
  selectedProvider='disabled';windowEvents['part3-ai-settings-changed']();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(calls.filter(x=>x.path==='/api/ai/settings').length,2);
  get('ai-text').value='차단할 요청';await get('ai-send').events.click();
  assert.equal(get('ai-text').value,'차단할 요청');assert.match(get('ai-messages').children.at(-1).textContent,/AI 사용이 꺼져/);
  assert.equal(calls.filter(x=>x.path==='/api/ai/chat').length,1);
  selectedProvider='gemini';windowEvents['part3-ai-settings-changed']();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(calls.filter(x=>x.path==='/api/ai/settings').length,3);
  chatSucceeds=true;get('ai-text').value='정상 요청';const resumed=get('ai-send').events.click();
  assert.equal(get('ai-messages').children.some(x=>x.textContent.includes('GGUF 응답을 기다리는 중')),false);
  releaseChat();await resumed;assert.equal(get('ai-messages').children.at(-1).textContent,'공통 설정 변경 후 정상 응답');
  assert.equal(calls.filter(x=>x.path==='/api/ai/chat').length,2);
  await get('ai-reset').events.click();
  assert.deepEqual(calls.filter(x=>x.path==='/api/ai/reset').map(x=>x.data),[{session:'current-session',research:true}]);
  assert.equal(get('ai-messages').textContent,'');assert.equal(get('ai-text').value,'');
  assert.equal(get('ai-pending').flags.has('hidden'),true);assert.equal(get('ai-reset').disabled,false);
 }
 console.log('shared AI chat '+scenario+' PASS');
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
'''


@pytest.mark.parametrize('scenario', ['default', 'gemini_configured', 'gemini_missing_key', 'disabled', 'provider_switch'])
def test_shared_ai_chat_provider_refresh_request_guards_and_error_recovery(tmp_path, scenario):
    script = _fixture_script('test_ai_reset72').split('(async()=>{')[0] + CHAT_CASES
    node = shutil.which('node')
    assert node, 'Node.js is required by current web verification'
    result = subprocess.run([node, '-e', script, str(ROOT / 'Part3/web'), scenario],
                            cwd=tmp_path, capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f'shared AI chat {scenario} PASS' in result.stdout
