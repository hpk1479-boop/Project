"""167: the settings screen's 최적화 button beside the backtest worker count, in the real unified.js.

Before a measurement the line says the PC is not measured; the button asks to close other programs
first, shows the measuring progress, and redraws the settings with the saved count and the measured
throughputs; a refusal (LIVE or a backtest running) is shown on the same line.
"""
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]
from test_settings_layout68 import SCRIPT as SETTINGS_DOM

TUNED = {'workers': 20, 'measured_at': '2026-10-08T12:00:00+00:00', 'rows': [
    {'workers': 14, 'bundles_per_second': 653.2}, {'workers': 17, 'bundles_per_second': 700.6},
    {'workers': 20, 'bundles_per_second': 757.0}]}

SCENARIO = r'''
const tuned=JSON.parse(process.argv[3]);
let saved=null,confirmAnswer=true,posts=0,gets=0,refuse=null;
const settings=()=>({live:[],part2:{cores:null,overlap_trading_days:3},part2_cores:saved?saved.workers:14,part2_tuned:saved,
 connections:{warehouse:'synthetic warehouse',python_executable:''},ai:{provider:'disabled',timeout:90}});
const context=vm.createContext({console,document,
 Option:class {constructor(text,value){const item=new Element('option');item.textContent=text;item.value=value;return item;}},
 window:{dispatchEvent(){},addEventListener(){},confirm:()=>confirmAnswer},Event:class{},
 setInterval:()=>1,setTimeout:fn=>{fn();return 1;},clearTimeout(){},
 api:async(route,body)=>{
  if(route==='mo/settings')return JSON.parse(JSON.stringify(settings()));
  if(route.startsWith('mo/live/status'))return {modules:{},lines:[]};
  if(route.startsWith('ai/'))return {available:false,models:[]};
  if(route==='mo/backtest/tune'&&body!==undefined){posts++;if(refuse)throw Error(refuse);
   return {running:true,step:1,steps:3,workers:14};}
  if(route==='mo/backtest/tune'){gets++;
   if(gets===1)return {running:true,step:2,steps:3,workers:17};
   saved=tuned;return {running:false,result:{workers:20},error:null};}
  throw Error('Unexpected API '+route);
 }});
const run=code=>vm.runInContext(code,context),get=id=>document.getElementById(id);
const cores=()=>get('settings-part2-fields').querySelector('[data-setting="cores"]');
(async()=>{
 run(fs.readFileSync(root+'/unified.js','utf8'));
 await run('moView("settings")');
 assert.equal(get('settings-tune-status').textContent,'이 PC는 최적화 전');
 assert.equal(cores().placeholder,'14');
 assert.equal(cores().closest('details').id,'settings-part2-details');
 confirmAnswer=false;await get('settings-tune').onclick();
 assert.equal(posts,0);                                        // cancelled: nothing measured
 confirmAnswer=true;await get('settings-tune').onclick();
 assert.equal(posts,1);assert.equal(gets,2);
 assert.equal(get('settings-tune-status').textContent,'이 PC 최적화: 20개 (14개 653, 17개 701, 20개 757 묶음/초)');
 assert.equal(cores().placeholder,'20');
 assert.equal(get('settings-tune').disabled,false);
 refuse='실시간을 끈 뒤 다시 누르세요.';await get('settings-tune').onclick();
 assert.equal(get('settings-tune-status').textContent,refuse);
 console.log('PASS');
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
'''


def test_tuning_button_measures_then_shows_the_saved_count(tmp_path):
    import json
    script = SETTINGS_DOM.split('const liveKeys=')[0] + SCENARIO
    result = subprocess.run([shutil.which('node'), '-e', script, str(ROOT / 'Part3/web'), 'tune', json.dumps(TUNED)],
                            cwd=tmp_path, capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS' in result.stdout
