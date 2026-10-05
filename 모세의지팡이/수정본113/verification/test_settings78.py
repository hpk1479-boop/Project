"""Shipped settings UI: collapsed backtest controls and inline common AI options."""
from pathlib import Path
import shutil
import subprocess

import pytest
from test_symbol_input76 import fixture

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = r'''
const clone=x=>JSON.parse(JSON.stringify(x));
const provider=scenario.startsWith('provider_')?scenario.slice('provider_'.length):'local_gguf';
const config={live:[],connections:{warehouse:'fixture',live_records:'fixture',python_executable:''},
 part2:{cores:null,work_size:'AUTO',overlap_trading_days:3,capture_start:'keyframe',oz_evaluation:'selected'},
 ai:{provider,watch_enabled:true,model:'installed:model',base_url:'http://127.0.0.1:11434',timeout:90,
 gguf_model_path:'models/gguf/current.gguf',llama_server_path:'',gguf_context_size:16384,gguf_gpu_layers:0,
 gguf_threads:null,gguf_chat_template_path:'',max_new_tokens:null,
 base_model:'saved/base',adapter_path:'models/saved_lora',load_in_4bit:false,
 gemini_model:'saved-gemini',gemini_api_key_configured:true}};
if(scenario==='auto_save'){config.part2.cores=8;config.part2.work_size='MONTH';}
const calls=[];
const context=vm.createContext({console,document,Option,Event:class{constructor(type){this.type=type;}},
 window:{dispatchEvent(){},addEventListener(){}},setInterval:()=>1,setTimeout:()=>1,clearTimeout(){},
 api:async(route,body)=>{
  calls.push({route,body:body&&clone(body)});
  if(route.startsWith('mo/live/status'))return {modules:{},lines:[]};
  if(route==='ai/models')return {available:scenario!=='ollama_fallback',models:['installed:model']};
  if(route==='ai/gguf-models')return {directory:'models/gguf',models:[{name:'current.gguf',path:'models/gguf/current.gguf',bytes:30}]};
  assert.equal(route,'mo/settings');
  if(body){Object.assign(config[body.group],body.changes);return {ok:true,message:'저장 완료'};}
  return clone(config);
 }});
const run=x=>vm.runInContext(x,context),get=id=>document.getElementById(id);
const field=(group,key)=>get('settings-'+group+'-fields').querySelector('[data-setting="'+key+'"]');
const writes=()=>calls.filter(x=>x.route==='mo/settings'&&x.body);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function changeProvider(value){field('ai','provider').value=value;await field('ai','provider').listeners.change[0]();await tick();}
(async()=>{
 run(fs.readFileSync(root+'/unified.js','utf8'));await run('moView("settings")');
 const ai=get('settings-ai-fields'),back=get('settings-part2-fields');
 assert.equal(back.querySelectorAll('details').length,1);assert.equal(ai.querySelectorAll('details').length,0);
 assert.equal(get('settings-part2-details').open,false);assert.equal(get('ai-settings-details'),null);
 assert.equal(get('ai-lora-enabled'),null);
 assert.equal(get('ai-lora-details'),null);assert.equal(get('ai-gguf-details'),null);
 for(const key of ['cores','work_size'])assert.equal(field('part2',key).closest('details'),get('settings-part2-details'));
 for(const key of ['overlap_trading_days','capture_start','oz_evaluation'])assert.equal(field('part2',key).closest('details'),null);
 assert.deepEqual(field('part2','work_size').options.map(x=>x.value),['AUTO','MONTH','FORTNIGHT','WEEK','DAY']);
 assert.equal(field('part2','work_size').options[0].textContent,'자동');
 assert.match(field('part2','cores').closest('label').textContent,/물리 코어 수/);
 for(const key of ['base_model','adapter_path','llama_server_path','gguf_model_path','timeout'])
  assert.equal(field('ai',key).closest('details'),null);
 assert.equal(new Set(ai.querySelectorAll('[data-setting]').map(x=>x.dataset.setting)).size,ai.querySelectorAll('[data-setting]').length);
 assert.equal(field('ai','gemini_api_key').value,'');
 if(scenario==='auto_save'){
  field('part2','cores').value='';field('part2','work_size').value='AUTO';
  await run('moSettingsSave("part2")');
  assert.deepEqual(writes().at(-1).body,{group:'part2',changes:{cores:null,work_size:'AUTO'}});
  assert.equal(get('settings-part2-details').open,false);assert.equal(field('part2','work_size').value,'AUTO');
 }else if(scenario==='manual_save'){
  get('settings-part2-details').open=true;field('part2','cores').value='24';field('part2','work_size').value='FORTNIGHT';
  await run('moSettingsSave("part2")');
  assert.deepEqual(writes().at(-1).body.changes,{cores:24,work_size:'FORTNIGHT'});assert.equal(get('settings-part2-details').open,false);
 }else if(scenario==='collapse'){
  get('settings-part2-details').open=true;
  await run('moView("live")');await run('moView("settings")');
  assert.equal(get('settings-part2-details').open,false);assert.equal(get('ai-settings-details'),null);
  assert.equal(field('part2','work_size').value,'AUTO');
 }else if(scenario==='timeout_save'){
  field('ai','timeout').value='120';await run('moSettingsSave("ai")');
  assert.deepEqual(writes().at(-1).body.changes,{timeout:120});assert.equal(get('ai-settings-details'),null);
  assert.equal(config.ai.gguf_model_path,'models/gguf/current.gguf');assert.equal(config.ai.adapter_path,'models/saved_lora');
 }else if(scenario==='gguf_manual'){
  assert.equal(field('ai','gguf_model_path').closest('label').classList.contains('hidden'),false);
  field('ai','gguf_model_path').value='models/gguf/user.gguf';await run('moSettingsSave("ai")');
  assert.deepEqual(writes().at(-1).body.changes,{gguf_model_path:'models/gguf/user.gguf'});
 }else if(scenario==='lora_select'){
  await changeProvider('local_lora');
  assert.equal(field('ai','provider').disabled,false);
  assert.equal(get('ai-local-lora-fields').classList.contains('hidden'),false);
  assert.equal(get('ai-gguf-advanced-group').classList.contains('hidden'),true);
  await run('moSettingsSave("ai")');assert.deepEqual(writes().at(-1).body.changes,{provider:'local_lora'});
  assert.equal(get('ai-settings-details'),null);assert.equal(config.ai.gguf_model_path,'models/gguf/current.gguf');
 }else if(scenario==='ollama_fallback'){
  await changeProvider('ollama');assert.equal(field('ai','model').closest('label').classList.contains('hidden'),false);
  assert.equal(get('ai-settings-details'),null);
 }else if(scenario.startsWith('provider_')){
  assert.equal(get('ai-gguf-advanced-group').classList.contains('hidden'),provider!=='local_gguf');
  assert.equal(get('ai-ollama-advanced-fields').classList.contains('hidden'),provider!=='ollama');
  assert.equal(get('ai-local-lora-fields').classList.contains('hidden'),provider!=='local_lora');
  assert.equal(field('ai','provider').disabled,false);
  await run('moSettingsSave("ai")');assert.equal(writes().length,0);
 }else{
  assert.equal(field('part2','cores').value,'');assert.equal(field('part2','work_size').value,'AUTO');
  await run('moSettingsSave("part2")');assert.equal(writes().length,0);
 }
 assert(calls.every(x=>!x.route.includes('chat')&&!x.route.includes('start')));
 console.log('PASS settings78 '+scenario);
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
'''


@pytest.mark.parametrize('scenario', ['default', 'auto_save', 'manual_save', 'collapse', 'timeout_save',
    'gguf_manual', 'lora_select', 'ollama_fallback', 'provider_ollama', 'provider_local_gguf', 'provider_local_lora', 'provider_gemini'])
def test_current_settings_ui_and_saving(scenario):
    result = subprocess.run([shutil.which('node'), '-e', fixture() + SCRIPT, str(ROOT / 'Part3/web'), scenario],
                            capture_output=True, text=True, encoding='utf-8', timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr
