"""163: the shipped list offers run folders left without a record or a result as one cleanup button."""
from pathlib import Path
import shutil
import subprocess

import pytest
from test_symbol_input76 import fixture

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = r'''
const x='1'.repeat(32),y='2'.repeat(32);
const clone=value=>JSON.parse(JSON.stringify(value));
let items=[{job_id:'a'.repeat(32),phase:'complete',active:false}],leftovers=scenario==='none'?[]:[x,y];
const calls=[],messages=[];let consent=scenario!=='cancel',fail=scenario==='error';
const context=vm.createContext({console,document,Option,sessionStorage:{getItem:()=>null,setItem(){}},
 window:{addEventListener(){},dispatchEvent(){},confirm:text=>{messages.push(text);return consent;}},
 setInterval:()=>1,setTimeout:()=>1,clearTimeout(){},
 moState:{job:null,view:'live',navigationRevision:0,jobSelectionRevision:0},
 moElement:(tag,text,cls)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;},
 moAdoptBacktestJob:async()=>{},moForgetBacktestJob:()=>{},
 api:async(name,body)=>{
  calls.push({name,body:body&&clone(body)});
  if(name==='mo/backtest/recent')return {items:clone(items),warnings:[],leftovers:clone(leftovers)};
  if(name==='mo/backtest/leftovers'){
   if(fail)throw Error('isolated failure');
   leftovers=leftovers.filter(id=>!body.job_ids.includes(id));
   return {ok:true,deleted:body.job_ids,errors:[]};
  }
  throw Error('unexpected '+name);
 }});
const run=value=>vm.runInContext(value,context),get=id=>document.getElementById(id);
const cleanups=()=>calls.filter(call=>call.name==='mo/backtest/leftovers');
(async()=>{
 const tidy=get('bt-jobs-leftovers');
 assert.equal(tidy.getAttribute('hidden'),'');
 run(fs.readFileSync(root+'/backtest_jobs.js','utf8'));
 await run('window.MosesBacktestJobs.refresh()');
 if(scenario==='none'){
  assert.equal(tidy.hidden,true);await tidy.onclick();assert.equal(cleanups().length,0);assert.equal(messages.length,0);
 }else{
  assert.equal(tidy.hidden,false);assert.equal(tidy.textContent,'결과 없는 폴더 2개 정리');
  const pending=tidy.onclick();
  if(scenario!=='cancel'){
   assert.equal(tidy.disabled,true);assert.equal(get('bt-jobs-refresh').disabled,true);
   await tidy.onclick();
  }
  await pending;
  assert.match(messages[0],/결과 없이 남은 실행 폴더 2개를 지우시겠습니까/);assert.equal(messages.length,1);
  if(scenario==='cancel'){
   assert.equal(cleanups().length,0);assert.equal(tidy.hidden,false);
  }else{
   assert.equal(cleanups().length,1);assert.deepEqual(cleanups()[0].body.job_ids,[x,y]);
   if(scenario==='error'){
    assert.match(get('bt-jobs-message').textContent,/폴더 정리: isolated failure/);assert.equal(tidy.hidden,false);
   }else{
    assert.equal(tidy.hidden,true);assert.equal(get('bt-jobs-message').textContent,'2개 폴더를 정리했습니다.');
   }
  }
  assert.equal(tidy.disabled,false);assert.equal(get('bt-jobs-refresh').disabled,false);
 }
 console.log('PASS '+scenario);
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
'''


@pytest.mark.parametrize('scenario', ['none', 'clean', 'cancel', 'error'])
def test_leftover_cleanup_button(scenario):
    result = subprocess.run([shutil.which('node'), '-e', fixture() + SCRIPT, str(ROOT / 'Part3/web'), scenario],
                            capture_output=True, text=True, encoding='utf-8', timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS ' + scenario in result.stdout
