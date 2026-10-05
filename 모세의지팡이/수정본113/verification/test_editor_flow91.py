"""Actual chat event wiring: edits require validation and explicit confirmation."""
from pathlib import Path
import shutil
import subprocess

import pytest

from test_ai_reset72 import SCRIPT as RESET_SCRIPT

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = RESET_SCRIPT.split('(async()=>{')[0] + r'''
context.document.querySelectorAll=()=>[];
context.CustomEvent=class {constructor(type,options={}){this.type=type;this.detail=options.detail;}};
context.window.dispatchEvent=()=>true;
Element.prototype.remove=function(){for(const el of elements.values())el.children=el.children.filter(x=>x!==this);};
let active, draft, changed, errors=[], generated=0, applied=0, editNumber=0, release, editFailure=false;
context.window.part3IntentEditor={create:({response,onChange})=>{
 draft={strategy:JSON.parse(JSON.stringify(response.result)),operation:'STRATEGY',plan:null};changed=onChange;
 active={element:new Element(),getDraft:()=>JSON.parse(JSON.stringify(draft)),
  setErrors:value=>errors=value,setDisabled:value=>active.disabled=value};return active;
}};
context.window.part3GenerateAIRecipe=async recipe=>{generated++;assert.equal(recipe.marker,'latest');return {filename:'Test_SPECIAL091.py'};};
context.window.part3ApplyAIRecipe=async()=>{applied++;};
context.fetch=async(path,request={})=>{
 const data=request.body?JSON.parse(request.body):null;calls.push({path,data});let value={ok:true};
 if(path==='/api/ai/settings')value={settings:{provider:'ollama',model:'sample'}};
 if(path==='/api/ai/chat')value=response;
 if(path==='/api/ai/editor')value={schema:{type:'object'},options:{}};
 if(path==='/api/ai/edit'){
  if(editFailure)return {ok:false,status:503,json:async()=>({error:'표 저장 연결 실패'})};
  editNumber++;
  if(process.argv[2]==='race'&&editNumber===1)await new Promise(r=>release=r);
  const valid=data.strategy.interpretation.steps[0].period>0;
  value={ok:valid,kind:data.operation,operation:data.operation,revision:'edit-'+editNumber,
   can_apply:valid&&data.operation==='STRATEGY',can_confirm:valid&&data.operation==='BACKTEST',
   errors:valid?[]:[{path:['strategy','interpretation','steps',0,'period'],message:'1 이상의 숫자를 입력하세요.'}]};
 }
 if(path==='/api/ai/apply')value=process.argv[2]==='backtest'
  ? {kind:'BACKTEST',result:{jobs:[],message:'실행 시작'}} : {kind:'STRATEGY',recipe:{marker:'latest'}};
 if(path==='/api/preview')value={filename:'Test_SPECIAL091.py',code:'# compiled'};
 if(path==='/api/generate'){generated++;value={filename:'Test_SPECIAL091.py',code:'# generated'};}
 return {ok:true,status:200,json:async()=>value};
};
(async()=>{
 if(process.argv[2]==='generate'){
  Element.prototype.append=function(...items){this.children.push(...items);};
  context.location={hash:'',pathname:'/'};context.history={replaceState(){}};context.URLSearchParams=URLSearchParams;
  vm.runInContext(fs.readFileSync(process.argv[1]+'/app.js','utf8'),context);
  const invalidate=context.window.part3InvalidateAIRecipe;
  context.window.part3InvalidateAIRecipe=()=>{invalidated++;invalidate();};
 }
 domEvents.DOMContentLoaded();await new Promise(r=>setImmediate(r));
 response={kind:'STRATEGY',revision:'initial',can_apply:true,
  result:{supported:true,interpretation:{steps:[{period:14}]}}};
 get('ai-text').value='최초 해석';await get('ai-send').events.click();
 assert.equal(generated,0);assert.equal(calls.filter(x=>x.path==='/api/ai/apply').length,0);
 assert.equal(get('ai-confirm').textContent,'확인 후 전략 생성');
 draft.strategy.interpretation.steps[0].period=process.argv[2]==='invalid'?0:20;
 if(process.argv[2]==='backtest')draft.operation='BACKTEST';
 if(process.argv[2]==='network')editFailure=true;
 const before=invalidated;changed();assert.equal(get('ai-confirm').disabled,true);assert.equal(invalidated,before+1);
 if(process.argv[2]==='race'){
  await new Promise(r=>setTimeout(r,300));assert(release);
  draft.strategy.interpretation.steps[0].period=30;changed();release();
  await new Promise(r=>setTimeout(r,350));
  const edits=calls.filter(x=>x.path==='/api/ai/edit');
  assert.equal(edits.length,2);assert.equal(edits[1].data.revision,'edit-1');
  assert.equal(edits[1].data.strategy.interpretation.steps[0].period,30);
 }else await new Promise(r=>setTimeout(r,350));
 if(process.argv[2]==='network'){
  assert.equal(get('ai-confirm').disabled,true);
  const chats=calls.filter(x=>x.path==='/api/ai/chat').length;
  get('ai-text').value='시간봉만 다시 수정해';await get('ai-send').events.click();
  assert.equal(calls.filter(x=>x.path==='/api/ai/chat').length,chats);
  assert.equal(get('ai-text').value,'시간봉만 다시 수정해');assert.equal(get('ai-send').disabled,false);
  editFailure=false;response={kind:'CHAT',message_ko:'수정 설명',pending_preserved:true};
  await get('ai-send').events.click();assert.equal(calls.filter(x=>x.path==='/api/ai/chat').length,chats+1);
  assert.equal(calls.filter(x=>x.path==='/api/ai/edit').at(-1).data.strategy.interpretation.steps[0].period,20);
 }else if(process.argv[2]==='invalid'){
  assert.equal(get('ai-confirm').disabled,true);assert.equal(errors.length,1);
  await get('ai-confirm').events.click();assert.equal(generated,0);
  assert.equal(calls.filter(x=>x.path==='/api/ai/apply').length,0);
  get('ai-text').value='기간은 14로 다시 고쳐줘';response={kind:'CHAT',message_ko:'수정 설명',pending_preserved:true};
  await get('ai-send').events.click();
  assert.equal(calls.filter(x=>x.path==='/api/ai/chat').at(-1).data.message,'기간은 14로 다시 고쳐줘');
 }else if(process.argv[2]==='settings'){
  windowEvents['part3-ai-settings-changed']();
  await get('ai-confirm').events.click();assert.equal(generated,0);
  assert.equal(calls.filter(x=>x.path==='/api/ai/apply').length,0);
 }else if(process.argv[2]==='backtest'){
  assert.equal(get('ai-confirm').textContent,'확인 후 백테스트 실행');assert.equal(get('ai-confirm').disabled,false);
  await get('ai-confirm').events.click();await get('ai-confirm').events.click();
  assert.equal(generated,0);assert.equal(applied,0);assert.equal(calls.filter(x=>x.path==='/api/ai/apply').length,1);
 }else{
  assert.equal(get('ai-confirm').disabled,false);assert.equal(generated,0);
  await get('ai-confirm').events.click();await get('ai-confirm').events.click();
  assert.equal(generated,1);assert.equal(applied,0);
  const confirms=calls.filter(x=>x.path==='/api/ai/apply');assert.equal(confirms.length,1);
  assert.equal(confirms[0].data.revision,process.argv[2]==='race'?'edit-2':'edit-1');
  if(process.argv[2]==='generate'){
   const writes=calls.filter(x=>['/api/preview','/api/draft','/api/generate'].includes(x.path));
   assert.deepEqual(writes.map(x=>x.path),['/api/preview','/api/draft','/api/generate']);
   assert(writes.every(x=>x.data.recipe.marker==='latest'));
  }
 }
 console.log('edited interpretation confirmation PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''


@pytest.mark.parametrize('scenario', ['valid', 'invalid', 'race', 'settings', 'network', 'generate', 'backtest'])
def test_table_edit_is_validated_before_confirmation_and_old_revision_cannot_run(tmp_path, scenario):
    node = shutil.which('node')
    assert node, 'Node.js is required by current web verification'
    result = subprocess.run([node, '-e', SCRIPT, str(ROOT / 'Part3/web'), scenario], cwd=tmp_path,
                            capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
