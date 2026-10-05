"""Run remaining AI settings interactions after removal, without network or models."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]

UI_CASES = r'''
let config={live:[],part2:{},connections:{},ai:{provider:scenario,
 model:'saved:tag',gemini_model:'saved-gemini',gemini_api_key_configured:true,
 gguf_model_path:'models/gguf/chosen.gguf',gguf_context_size:16384,gguf_gpu_layers:0,
 base_model:'saved/base',adapter_path:'models/saved_lora',load_in_4bit:false,timeout:90,
 groq_model:'discarded-model',groq_api_key_configured:true}};
const calls=[];
const context=vm.createContext({console,document,window:{dispatchEvent(){}},
 Event:class{},Option:function(text,value){const item=new Element('option');item.textContent=text;item.value=value;return item;},
 setInterval:()=>1,setTimeout:()=>1,clearTimeout(){},api:async(route,body)=>{
 calls.push({route,body:body&&JSON.parse(JSON.stringify(body))});
 if(route.startsWith('mo/live/status'))return {modules:{},lines:[]};
 if(route==='ai/models')return {available:true,models:['saved:tag']};
 if(route==='ai/gguf-models')return {directory:'models/gguf',models:[{name:'chosen.gguf',path:'models/gguf/chosen.gguf',bytes:24}]};
 if(route==='ai/gemini-models')return {available:true,models:['saved-gemini','next-gemini']};
 assert.equal(route,'mo/settings');
 if(body){Object.assign(config.ai,body.changes);return {ok:true,message:'저장 완료'};}
 return JSON.parse(JSON.stringify(config));
}});
const run=code=>vm.runInContext(code,context),get=id=>document.getElementById(id);
const ai=()=>get('settings-ai-fields'),field=key=>ai().querySelector('[data-setting="'+key+'"]');
(async()=>{
 run(fs.readFileSync(root+'/unified.js','utf8'));await new Promise(resolve=>setImmediate(resolve));await run('moView("settings")');
 assert.deepEqual(field('provider').options.map(x=>x.value),['gemini','ollama','local_gguf','local_lora','disabled']);
 assert.equal(ai().querySelectorAll('[data-setting]').some(x=>x.dataset.setting.startsWith('groq')),false);
 assert.equal(/groq/i.test(ai().textContent),false);
 assert.equal(get('ai-settings-details'),null);
 assert.equal(run('moSelectedAIProvider(document.getElementById("settings-ai-fields"))'),scenario);
 const views={ollama:'ai-ollama-fields',local_gguf:'ai-local-gguf-fields',local_lora:'ai-local-lora-fields',gemini:'ai-gemini-fields'};
 for(const [provider,id] of Object.entries(views))assert.equal(get(id).classList.contains('hidden'),provider!==scenario);
 if(scenario==='gemini'){
  const choice=get('ai-gemini-model-select');
  assert.deepEqual(choice.options.map(x=>x.value),['saved-gemini','next-gemini']);
  choice.value='next-gemini';choice.onchange();
  assert.equal(field('gemini_model').value,'next-gemini');
  field('gemini_model').value='custom-gemini';field('gemini_model').listeners.input[0]();
 }
 field('timeout').value='120';await run('moSettingsSave("ai")');
 const saved=calls.filter(x=>x.route==='mo/settings'&&x.body).at(-1).body.changes;
 assert.deepEqual(saved,scenario==='gemini'?{gemini_model:'custom-gemini',timeout:120}:{timeout:120});
 assert.equal(calls.some(x=>/groq/i.test(x.route)),false);
 console.log('remaining AI UI '+scenario+' PASS');
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
'''

@pytest.mark.parametrize('scenario', ['ollama','local_gguf','local_lora','gemini'])
def test_remaining_ai_settings_without_removed_provider(tmp_path, scenario):
    spec=importlib.util.spec_from_file_location('settings_dom89',Path(__file__).with_name('test_settings_layout68.py'))
    fixture=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    script=fixture.SCRIPT.split('const liveKeys=')[0]+UI_CASES
    node=shutil.which('node')
    assert node, 'Node.js is required by current web verification'
    result=subprocess.run([node,'-e',script,str(ROOT/'Part3/web'),scenario],cwd=tmp_path,
                          capture_output=True,text=True,encoding='utf-8',timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'remaining AI UI '+scenario+' PASS' in result.stdout
