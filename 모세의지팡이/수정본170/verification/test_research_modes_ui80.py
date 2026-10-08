"""Exercise actual mode buttons, chat rendering and confirmation events."""
from pathlib import Path
import shutil
import subprocess

import pytest

from test_ai_reset72 import SCRIPT as RESET_SCRIPT

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = RESET_SCRIPT.split('(async()=>{')[0] + r'''
Element.prototype.remove=function(){for(const el of elements.values())el.children=el.children.filter(x=>x!==this);};
Object.defineProperty(Element.prototype,'textContent',{get(){return this._text||'';},set(value){this._text=value;if(this.children)this.children=[];}});
let lateResolve;
const nativeFetch=context.fetch;
context.fetch=async(path,request={})=>{
 if(path==='/api/ai/chat'&&JSON.parse(request.body).message==='응답 오류'){
  calls.push({path,data:JSON.parse(request.body)});
  return {ok:false,status:400,json:async()=>({error:'챗 응답 실패',pending_preserved:true})};
 }
 if(path==='/api/ai/chat'&&process.argv[2]==='busy'){
  calls.push({path,data:JSON.parse(request.body)});
  return new Promise(resolve=>lateResolve=()=>resolve({ok:true,status:200,json:async()=>response}));
 }
 return nativeFetch(path,request);
};
(async()=>{
 domEvents.DOMContentLoaded();await new Promise(r=>setImmediate(r));
 response={kind:'STRATEGY',revision:'first',result:{supported:true},can_apply:true};
 get('ai-text').value='15분 상승추세와 3분 하단 원비';
 const sending=get('ai-send').events.click();
 if(process.argv[2]==='busy'){
  assert(get('ai-research-chat').disabled);await get('ai-research-chat').events.click();
  assert(!calls.some(x=>x.data?.mode==='chat'));
  lateResolve();await sending;assert.equal(get('ai-research-chat').disabled,false);
 }else{
  await sending;assert(!get('ai-pending').flags.has('hidden'));
  const before=invalidated,count=get('ai-messages').children.length;
  await get('ai-research-chat').events.click();
  assert.equal(get('ai-research-chat')['aria-pressed'],'true');assert.equal(get('ai-research-strategy')['aria-pressed'],'false');
  assert.equal(get('ai-messages').children.length,count);assert.equal(invalidated,before);
  assert(get('ai-text').placeholder.includes('대화')||get('ai-text').placeholder.includes('이야기'));
  response={kind:'CHAT',message_ko:'<img onerror=alert(1)> 조건 차이를 설명합니다.',pending_preserved:true,can_confirm:false};
  if(process.argv[2]==='error'){
   get('ai-text').value='응답 오류';await get('ai-send').events.click();
   assert(!get('ai-pending').flags.has('hidden'));assert.equal(invalidated,before);
   assert(get('ai-messages').children.some(x=>x.textContent==='챗 응답 실패'));
  }
  get('ai-text').value='시간봉을 바꾸면 뭐가 달라?';await get('ai-send').events.click();
  assert(!get('ai-pending').flags.has('hidden'));assert.equal(invalidated,before);
  assert(get('ai-messages').children.some(x=>x.textContent===response.message_ko));
  assert.equal(calls.filter(x=>x.path==='/api/ai/apply').length,0);
  if(['preserve','error'].includes(process.argv[2])){
   await get('ai-research-strategy').events.click();assert(!get('ai-pending').flags.has('hidden'));
   await get('ai-confirm').events.click();
   assert.equal(calls.filter(x=>x.path==='/api/ai/apply').at(-1).data.revision,'first');
  }else{
   await get('ai-reset').events.click();assert.equal(get('ai-messages').textContent,'');
   assert.equal(get('ai-research-chat')['aria-pressed'],'true');assert(get('ai-pending').flags.has('hidden'));
   response={kind:'STRATEGY',revision:'new',result:{supported:true},can_apply:true};
   get('ai-text').value='신규 전략으로 만들어';await get('ai-send').events.click();
   assert.equal(calls.filter(x=>x.path==='/api/ai/chat').at(-1).data.mode,'chat');
   assert.equal(calls.filter(x=>x.path==='/api/ai/apply').length,0);
   await get('ai-confirm').events.click();
   assert.equal(calls.filter(x=>x.path==='/api/ai/apply').at(-1).data.revision,'new');
  }
 }
 assert.equal(get('ai-send').disabled,false);
 assert(calls.filter(x=>x.path==='/api/ai/chat').every(x=>['strategy','chat'].includes(x.data.mode)&&x.data.research));
 console.log('research modes UI PASS');
})().catch(e=>{console.error(e);process.exitCode=1;});
'''


@pytest.mark.parametrize('scenario', ['preserve', 'reset', 'busy', 'error'])
def test_mode_switch_preserves_conversation_and_requires_confirmation(tmp_path, scenario):
    result = subprocess.run([shutil.which('node'), '-e', SCRIPT, str(ROOT / 'Part3/web'), scenario],
                            cwd=tmp_path, capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
