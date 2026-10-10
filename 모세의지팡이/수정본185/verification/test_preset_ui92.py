"""Preset loading uses the existing full intent editor and never executes a draft."""
from pathlib import Path
import shutil
import subprocess

import pytest

from test_ai_reset72 import SCRIPT as RESET_SCRIPT

ROOT = Path(__file__).resolve().parents[1]


SCRIPT =RESET_SCRIPT.split('(async()=>{')[0] + r'''
Element.prototype.showModal=function(){this.open=true;};
Element.prototype.close=function(){this.open=false;};
Element.prototype.append=function(...items){this.children.push(...items);};
Element.prototype.replaceChildren=function(...items){this.children=items;};
context.document.createElement=()=>{const item=new Element();item.dataset={};return item;};
// The list groups names by source (스페셜 전략 / 새로운 전략): list > details > names > button.
const descendants=item=>(item.children||[]).flatMap(child=>[child,...descendants(child)]);
const listButtons=()=>descendants(get('ai-preset-list')).filter(item=>item.dataset?.presetId);
context.document.querySelectorAll=()=>listButtons();
context.CustomEvent=class{constructor(type,init){this.type=type;this.detail=init.detail;}};
context.window.dispatchEvent=event=>windowEvents[event.type]?.(event);
Object.defineProperty(Element.prototype,'textContent',{
 get(){return this._text||'';},set(value){this._text=value;this.children=[];},configurable:true
});
let draft, changed, activeEditor, editFailure=false, releaseEdit, loadedEditors=[], generated=0;
const clone=value=>JSON.parse(JSON.stringify(value));
const source={supported:true,interpretation:{symbols:['XAUUSD+'],direction:'LONG',order:'SEQUENTIAL',
 steps:[{kind:'MA_CROSS',tf:'1m',fast_ma:'SMA50',slow_ma:'SMA200',cross:'GOLDEN'},
 {kind:'FVG_NEW',tf:'5m',capture_as:'created-fvg',within_sec:1800}],
 final:{trigger:'OZ',tf:'1m',mode:'BREAKER',scope_ref:'created-fvg'},branches:[]}};
const items=[{id:'registry-alpha',name:'기존 전략 A',example:'15분 상승추세에 1분 올존 전략'},
 {id:'new-entry-without-number',name:'새 등록 전략 <A>',example:'1분 MA 교차 후 5분 FVG가 생성되면 같은 FVG의 1분 브레이커 올존'}];
let sequencePolls=0;
if(process.argv[2]==='jobs')context.setTimeout=(callback,delay)=>{
 if(delay===1500){sequencePolls++;return 0;}return setTimeout(callback,delay);
};
context.window.part3IntentEditor={create:({response,onChange})=>{
 loadedEditors.push(clone(response.result));
 draft={strategy:clone(response.result),operation:'STRATEGY',plan:null};changed=onChange;
 activeEditor={element:new Element(),getDraft:()=>clone(draft),setErrors(){},setDisabled(value){activeEditor.disabled=value;},setVirtualDraftFrames(){}};
 return activeEditor;
}};
context.window.part3GenerateAIRecipe=async()=>{generated++;return {filename:'Test_SPECIAL092.py'};};
context.fetch=async(path,request={})=>{
 const data=request.body?JSON.parse(request.body):null;calls.push({path,data});let value={ok:true};
 if(path==='/api/ai/settings')value={settings:{provider:'ollama',model:'sample'}};
 if(path==='/api/ai/chat')value=response;
 if(path==='/api/ai/editor')value={schema:{type:'object'}};
 if(path==='/api/ai/presets'){
  if(process.argv[2]==='list-failure')return {ok:false,status:503,json:async()=>({error:'목록 연결 실패'})};
  value={items:process.argv[2]==='empty'?[]:items,revision:'initial'};
 }
 if(path==='/api/ai/edit'){
  if(editFailure)return {ok:false,status:503,json:async()=>({error:'표 연결 실패'})};
  if(process.argv[2]==='dirty-race')await new Promise(r=>releaseEdit=r);
  const valid=data.strategy.interpretation.steps[0].period!==0;
  value={ok:valid,kind:'STRATEGY',revision:'edited',can_apply:valid,errors:valid?[]:[{path:['strategy','interpretation','steps',0,'period'],message:'기간 오류'}]};
 }
 if(path==='/api/ai/preset/load'){
  if(process.argv[2]==='load-failure')return {ok:false,status:400,json:async()=>({error:'원본 Recipe에 없는 종목'})};
  if(process.argv[2]==='stale')value={ok:false,errors:[{message:'최신 표를 다시 불러오세요.'}]};
  else value={ok:true,kind:'STRATEGY',revision:'loaded',can_apply:true,result:clone(source),
   editor_strategy:clone(source),editor_contract:{schema:{type:'object'}},example:items[1].example,preset:items[1]};
 }
 if(path==='/api/ai/apply')value={kind:'STRATEGY',recipe:{marker:'edited-preset'}};
 return {ok:true,status:200,json:async()=>value};
};
(async()=>{
 domEvents.DOMContentLoaded();await new Promise(r=>setImmediate(r));
 response={kind:'STRATEGY',revision:'initial',can_apply:true,result:{supported:true,interpretation:{steps:[{period:14}]}}};
 if(process.argv[2]==='jobs')response={kind:'BACKTEST',revision:'initial',can_confirm:false,
  result:{sequence_id:'running-sequence',phase:'run',jobs:[],message:'진행 중인 작업'}};
 get('ai-text').value='기존 해석';await get('ai-send').events.click();
 get('ai-research-chat').events.click();
 const beforeChats=calls.filter(x=>x.path==='/api/ai/chat').length;
 const priorEditor=activeEditor, priorCount=loadedEditors.length, priorMessages=get('ai-messages').children.slice();
 get('ai-text').value='작성 중인 기존 문장';
 await get('ai-preset-open').events.click();
 assert.equal(get('ai-preset-dialog').open,true);
 assert.equal(get('ai-text').value,'작성 중인 기존 문장');
 assert.equal(get('ai-messages').children.length,priorMessages.length);
 if(['empty','list-failure'].includes(process.argv[2])){
  assert.equal(get('ai-preset-load').disabled,true);
  assert.equal(loadedEditors.length,priorCount);
  assert.equal(listButtons().length,0);
  assert.equal(get('ai-preset-detail').hidden,true);
  assert.equal(calls.some(x=>x.path==='/api/ai/preset/load'),false);
  get('ai-preset-cancel').events.click();assert.equal(get('ai-preset-dialog').open,false);
 }else{
  assert.deepEqual(listButtons().map(x=>x.dataset.presetId),items.map(x=>x.id));
  assert.deepEqual(listButtons().map(x=>x.textContent),items.map(x=>x.name));
  assert.equal(get('ai-preset-detail').hidden,true);
  listButtons()[1].events.click();
  assert.equal(get('ai-preset-detail').hidden,false);
  assert.equal(get('ai-preset-example').textContent,items[1].example);
  if(process.argv[2]==='cancel'){
   get('ai-preset-cancel').events.click();assert.equal(get('ai-preset-dialog').open,false);
   assert.equal(loadedEditors.length,priorCount);assert.equal(activeEditor,priorEditor);
   assert.equal(calls.some(x=>x.path==='/api/ai/preset/load'),false);
  }else{
   if(['dirty','invalid','dirty-race','network'].includes(process.argv[2])){
    draft.strategy.interpretation.steps[0].period=process.argv[2]==='invalid'?0:25;changed();
    editFailure=process.argv[2]==='network';
   }
   const loading=get('ai-preset-load').events.click();
   if(process.argv[2]==='dirty-race'){
    await new Promise(r=>setImmediate(r));assert(releaseEdit);
    assert.equal(calls.some(x=>x.path==='/api/ai/preset/load'),false);
    get('ai-preset-dialog').events.cancel({preventDefault(){}});assert.equal(get('ai-preset-dialog').open,true);
    releaseEdit();
   }
   await loading;
   if(['load-failure','stale','network'].includes(process.argv[2])){
    assert.equal(get('ai-preset-dialog').open,true);assert.equal(get('ai-text').value,'작성 중인 기존 문장');
    assert.equal(activeEditor,priorEditor);assert.equal(loadedEditors.length,priorCount);
    assert.equal(get('ai-messages').children.slice(0,priorMessages.length).every((x,i)=>x===priorMessages[i]),true);
    assert(get('ai-preset-message').textContent.length);
    if(process.argv[2]==='network')assert.equal(calls.some(x=>x.path==='/api/ai/preset/load'),false);
   }else{
    assert.equal(get('ai-preset-dialog').open,false);
    assert.equal(get('ai-text').value,items[1].example);
    assert.equal(get('ai-messages').children[process.argv[2]==='jobs'?1:0].textContent,items[1].example);
    if(process.argv[2]==='jobs'){
     assert.equal(get('ai-messages').children[0],priorMessages[1]);
     assert.equal(sequencePolls,1,'existing job monitor keeps its poll and card');
    }
    assert.equal(get('ai-research-strategy')['aria-pressed'],'true');
    assert.deepEqual(loadedEditors.at(-1),source);
    assert.equal(calls.filter(x=>x.path==='/api/ai/editor').length,process.argv[2]==='jobs'?0:1,'load response contains the contract; no second contract request');
    const loadCall=calls.find(x=>x.path==='/api/ai/preset/load');
    assert.equal(loadCall.data.preset_id,items[1].id);
    assert.equal(loadCall.data.revision,['dirty','invalid','dirty-race'].includes(process.argv[2])?'edited':'initial');
    assert.equal(generated,0);assert.equal(calls.some(x=>x.path==='/api/ai/apply'),false);
    assert.equal(calls.filter(x=>x.path==='/api/ai/chat').length,beforeChats);
    if(process.argv[2]==='confirm'){
     draft.strategy.interpretation.steps[0].period=40;changed();
     await get('ai-confirm').events.click();assert.equal(generated,1);
     assert.equal(calls.filter(x=>x.path==='/api/ai/apply').at(-1).data.revision,'edited');
    }else if(process.argv[2]==='followup'){
     get('ai-text').value=items[1].example+' FVG 시간봉만 15분으로 바꿔줘';
     response={kind:'CHAT',message_ko:'현재 초안 수정'};await get('ai-send').events.click();
     const chat=calls.filter(x=>x.path==='/api/ai/chat').at(-1);
     assert.equal(chat.data.mode,'strategy');assert(chat.data.message.endsWith('15분으로 바꿔줘'));
    }
   }
  }
 }
 assert.equal(get('ai-send').disabled,false);assert.equal(get('ai-preset-open').disabled,false);
 assert.equal(calls.some(x=>x.path.includes('sequence/stop')||x.path.includes('ai/reset')),false);
 console.log('preset loading event flow PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''


@pytest.mark.parametrize('scenario', [
    'success', 'cancel', 'empty', 'list-failure', 'load-failure', 'stale',
    'dirty', 'invalid', 'dirty-race', 'network', 'confirm', 'followup', 'jobs',
])
def test_registry_preset_load_keeps_original_and_requires_confirmation(tmp_path, scenario):
    node = shutil.which('node')
    assert node, 'Node.js is required by current web verification'
    result = subprocess.run([node, '-e', SCRIPT, str(ROOT / 'Part3/web'), scenario], cwd=tmp_path,
                            capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
