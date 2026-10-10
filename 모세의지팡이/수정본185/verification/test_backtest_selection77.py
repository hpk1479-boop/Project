"""Execute the shipped list controls and deletion recovery with isolated APIs."""
from pathlib import Path
import shutil
import subprocess

import pytest
from test_symbol_input76 import fixture

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = r'''
const a='a'.repeat(32),b='b'.repeat(32),c='c'.repeat(32),d='d'.repeat(32);
const clone=x=>JSON.parse(JSON.stringify(x));
let items=[{job_id:a,phase:'complete',active:false,symbol:'GOLDm'},
 {job_id:b,phase:'error',active:false,symbol:'USTEC'},
 {job_id:c,phase:'run',active:true},{job_id:d,phase:'complete',active:true}];
if(scenario==='empty')items=[];
const calls=[],forgot=[],messages=[];let consent=true,fail=false,partial=false,deferredRecent=null;
const context=vm.createContext({console,document,Option,sessionStorage:{getItem:()=>null,setItem(){}},
 window:{addEventListener(){},dispatchEvent(){},confirm:text=>{messages.push(text);return consent;}},
 setInterval:()=>1,setTimeout:()=>1,clearTimeout(){},
 moState:{job:null,view:'live',navigationRevision:0,jobSelectionRevision:0},
 moElement:(tag,text,cls)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;},
 moAdoptBacktestJob:async()=>{},moForgetBacktestJob:ids=>forgot.push(clone(ids)),
 api:async(name,body)=>{
  calls.push({name,body:body&&clone(body)});
  if(name==='mo/backtest/recent'){if(deferredRecent)return await deferredRecent;return {items:clone(items)};}
  if(name==='mo/backtest/delete'){
   if(fail)throw Error('isolated failure');
   const deleted=partial?[a]:body.job_ids;
   items=items.filter(item=>!deleted.includes(item.job_id));
   return {ok:!partial,deleted,errors:partial?[{job_id:b,message:'blocked'}]:[]};
  }
  throw Error('unexpected '+name);
 }});
const run=x=>vm.runInContext(x,context),get=id=>document.getElementById(id);
const checks=()=>get('bt-jobs-list').querySelectorAll('input');
const choose=(index,on=true)=>{checks()[index].checked=on;checks()[index].onchange();};
const deletedCalls=()=>calls.filter(x=>x.name==='mo/backtest/delete');
(async()=>{
 run(fs.readFileSync(root+'/backtest_jobs.js','utf8'));
 await run('window.MosesBacktestJobs.refresh()');
 assert.equal(get('bt-jobs-select-all').textContent,'전체선택');
 assert.equal(get('bt-jobs-delete').textContent,'선택삭제');
 assert.equal(get('bt-jobs-delete').disabled,true);
 assert.equal(document.querySelector('.moses-bt-mode-field .moses-bt-symbol-arrow').textContent,'▼');
 if(scenario==='empty'){
  assert.equal(get('bt-jobs-select-all').disabled,true);assert.equal(checks().length,0);
  await get('bt-jobs-delete').onclick();assert.equal(deletedCalls().length,0);
 }else{
  assert.deepEqual(checks().map(x=>x.disabled),[false,false,true,true]);
  assert.equal(get('bt-jobs-list').children[0].children[0].type,'checkbox');
  choose(0);assert.equal(get('bt-jobs-delete').disabled,false);
  if(scenario==='cancel'){
   consent=false;await get('bt-jobs-delete').onclick();assert.equal(deletedCalls().length,0);assert(checks()[0].checked);
  }else if(scenario==='uncheck'){
   choose(0,false);assert(get('bt-jobs-delete').disabled);await get('bt-jobs-delete').onclick();assert.equal(deletedCalls().length,0);
  }else if(scenario==='toggle'){
   get('bt-jobs-select-all').onclick();assert.equal(get('bt-jobs-select-all').textContent,'전체해제');
   assert.deepEqual(checks().map(x=>x.checked),[true,true,false,false]);
   get('bt-jobs-select-all').onclick();assert.equal(get('bt-jobs-select-all').textContent,'전체선택');
   assert.deepEqual(checks().map(x=>x.checked),[false,false,false,false]);assert(get('bt-jobs-delete').disabled);
   choose(1);get('bt-jobs-select-all').onclick();assert.equal(get('bt-jobs-select-all').textContent,'전체해제');
   choose(0,false);assert.equal(get('bt-jobs-select-all').textContent,'전체선택');
   assert.equal(deletedCalls().length,0);
  }else if(scenario==='refresh'){
   items=items.filter(x=>x.job_id!==a);await run('window.MosesBacktestJobs.refresh()');assert(get('bt-jobs-delete').disabled);
  }else{
   if(scenario==='all'||scenario==='partial')get('bt-jobs-select-all').onclick();
   if(scenario==='error')fail=true;if(scenario==='partial')partial=true;
   let release,pending;
   if(scenario==='inflight'){
    deferredRecent=new Promise(resolve=>release=resolve);pending=run('window.MosesBacktestJobs.refresh()');
   }
   const removal=get('bt-jobs-delete').onclick();
   assert(get('bt-jobs-delete').disabled);assert(get('bt-jobs-select-all').disabled);
   await get('bt-jobs-delete').onclick();
   if(scenario==='inflight'){
    assert.equal(deletedCalls().length,0);deferredRecent=null;release({items:clone(items)});await pending;
   }
   await removal;
   assert.equal(deletedCalls().length,1);
   assert.deepEqual(deletedCalls()[0].body.job_ids,scenario==='all'||scenario==='partial'?[a,b]:[a]);
   assert.equal(get('bt-jobs-refresh').disabled,false);assert.equal(get('bt-jobs-select-all').disabled,scenario==='all');
   if(scenario==='error'){
    assert.match(get('bt-jobs-message').textContent,/isolated failure/);assert(checks()[0].checked);assert.equal(forgot.length,0);
   }else{
    assert.equal(checks().length,scenario==='all'?2:3);
    assert.deepEqual(forgot,[scenario==='all'?[a,b]:[a]]);
    assert(!get('bt-jobs-list').textContent.includes(a));
    assert.equal(get('bt-jobs-delete').disabled,scenario!=='partial');
    if(scenario==='partial')assert.match(get('bt-jobs-message').textContent,/blocked/);
   }
  }
  if(messages.length)assert.match(messages[0],/원본 captures 데이터는 삭제하지 않습니다/);
 }
 console.log('PASS '+scenario);
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
'''

FORGET = r'''
const a='a'.repeat(32),b='b'.repeat(32);let resolveStatus;
const context=vm.createContext({console,document,Option,
 window:{addEventListener(){},dispatchEvent(){}},setInterval:()=>1,setTimeout:()=>1,clearTimeout(){},
 api:async name=>{if(name.startsWith('mo/backtest/status'))return await new Promise(resolve=>resolveStatus=resolve);
  throw Error('unexpected '+name);}});
const run=x=>vm.runInContext(x,context),get=id=>document.getElementById(id);
(async()=>{
 run(fs.readFileSync(root+'/unified.js','utf8'));
 run(`moState.job='${a}';moState.view='backtest';moBacktestPage('progress');moBacktestPoll.done=false;`);
 get('mo-bt-result').textContent='old result';
 run(`moForgetBacktestJob(['${b}'])`);assert.equal(run('moState.job'),a);assert.equal(get('mo-bt-result').textContent,'old result');
 const response=run('moBacktestStatus()');
 run(`moForgetBacktestJob(['${a}'])`);
 assert.equal(run('moState.job'),null);assert.equal(run('moBacktestPoll.done'),true);
 assert.equal(get('mo-bt-result').textContent,'');assert(!get('mo-bt-setup-page').classList.contains('hidden'));
 for(const id of ['mo-bt-open-progress','mo-bt-open-result','mo-bt-progress-result','mo-bt-stop-button'])assert(get(id).disabled);
 resolveStatus({job_id:a,phase:'complete',active:false,result_ready:true,message:'late deleted result'});await response;
 assert.equal(run('moState.job'),null);assert.equal(get('mo-bt-result').textContent,'');
 assert.equal(run('moBacktestPoll.status'),null);assert.equal(run('moBacktestPoll.timer'),null);
 console.log('PASS forgotten');
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
'''


@pytest.mark.parametrize('scenario', ['empty', 'one', 'all', 'cancel', 'uncheck', 'refresh', 'error', 'partial', 'inflight', 'toggle'])
def test_selected_result_controls(scenario):
    result = subprocess.run([shutil.which('node'), '-e', fixture() + SCRIPT, str(ROOT / 'Part3/web'), scenario],
                            capture_output=True, text=True, encoding='utf-8', timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr


def test_deleted_open_result_and_pending_response_are_forgotten():
    result = subprocess.run([shutil.which('node'), '-e', fixture() + FORGET, str(ROOT / 'Part3/web'), 'forgotten'],
                            capture_output=True, text=True, encoding='utf-8', timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr
