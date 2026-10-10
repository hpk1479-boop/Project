"""Shipped unified chat events with isolated HTTP responses and job navigation."""
from pathlib import Path
import shutil
import subprocess

import pytest
from test_ai_reset72 import SCRIPT as RESET_SCRIPT

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = RESET_SCRIPT.split('(async()=>{')[0] + r'''
Element.prototype.setAttribute=function(k,v){this[k]=v;};
Element.prototype.remove=function(){for(const el of elements.values())el.children=el.children.filter(x=>x!==this);};
Object.defineProperty(Element.prototype,'textContent',{get(){return this._text||'';},set(value){this._text=value;if(this.children)this.children=[];}});
const reopened=[],applied=[],timers=[];
context.window.MosesBacktestJobs={reconnect:async id=>reopened.push(id),refresh:async()=>{}};
context.window.part3ApplyAIRecipe=async recipe=>applied.push(recipe);
context.window.confirm=()=>true;context.setTimeout=fn=>{timers.push(fn);return timers.length;};context.clearTimeout=()=>{};
let lateResolve,sequence={sequence_id:'a'.repeat(32),phase:'run',generated_filename:'Test_SPECIAL002.py',jobs:[{job_id:'b'.repeat(32),symbol:'GOLDm',phase:'confirm'}]};
context.fetch=async(path,request={})=>{
 const data=request.body?JSON.parse(request.body):null;calls.push({path,data});
 let value={ok:true};
 if(path==='/api/ai/settings')value={settings:{provider:'ollama',model:'sample'}};
 if(path==='/api/ai/chat'){
  if(process.argv[2]==='stale')return new Promise(resolve=>lateResolve=()=>resolve({ok:true,json:async()=>response}));
  value=response;
 }
 if(path==='/api/ai/apply')value={kind:'BACKTEST',result:sequence};
 if(path.startsWith('/api/ai/sequence?'))value=sequence;
 return {ok:true,status:200,json:async()=>value};
};
(async()=>{
 domEvents.DOMContentLoaded();await new Promise(r=>setImmediate(r));
 response={kind:'BACKTEST',action:'SEQUENTIAL',revision:'fresh',can_confirm:true,
  message_ko:'첫 작업 완료 후 다음 작업 실행',preview:'<img onerror=alert(1)> GOLDm 1년 → USTEC 1년',strategy:{result:{supported:true}}};
 get('ai-text').value='스페셜1 골드 1년, 끝나면 나스닥 1년';
 const pending=get('ai-send').events.click();
 if(process.argv[2]==='stale'){
  windowEvents['part3-ai-settings-changed']();lateResolve();await pending;
  assert(get('ai-pending').flags.has('hidden'));
  await get('ai-confirm').events.click();assert.equal(calls.some(x=>x.path==='/api/ai/apply'),false);
  assert(get('ai-messages').children.some(x=>x.textContent.includes('설정이 변경')));
 }else{
  await pending;
  assert.equal(calls.some(x=>x.path==='/api/ai/apply'),false);
  assert(!get('ai-pending').flags.has('hidden'));assert.equal(get('ai-confirm').textContent,'조건 확인 후 실행');
  assert(get('ai-messages').children.some(x=>x.textContent===response.preview));
  await get('ai-confirm').events.click();await get('ai-confirm').events.click();
  assert.equal(calls.filter(x=>x.path==='/api/ai/apply').length,1);assert.equal(applied.length,0);
  const card=get('ai-messages').children.at(-1);
  assert(card.children.some(x=>x.textContent.includes('데이터 구축 승인이 필요')));
  const row=card.children.find(x=>x.textContent.includes('GOLDm'));
  await row.children.find(x=>x.textContent==='진행 / 결과 보기').events.click();
  assert.deepEqual(reopened,['b'.repeat(32)]);
  sequence={...sequence,phase:'complete',jobs:[{job_id:'b'.repeat(32),symbol:'GOLDm',phase:'complete',
   result:{alert_statistics:{total:4},by_rr:{'1.0':{total_trades:4,win_rate:50,total_r:0}}}}]};
  delete sequence.generated_filename;
  await timers.shift()();assert(card.textContent.includes('완료'));
  assert(card.textContent.includes('Test_SPECIAL002.py'));
  assert(card.children.some(x=>x.textContent.includes('총 알림 4건')));
  const completed=card.children.find(x=>x.textContent.includes('GOLDm'));
  assert(completed.children.some(x=>x.textContent.includes('승률 50%')));
  assert.equal(timers.length,0);
  const before=calls.filter(x=>x.path==='/api/ai/sequence/stop').length;
  await get('ai-reset').events.click();assert.equal(calls.filter(x=>x.path==='/api/ai/sequence/stop').length,before);
  assert.equal(get('ai-messages').textContent,'');
 }
 assert(calls.filter(x=>x.path==='/api/ai/chat').every(x=>x.data.research===true));
 assert.equal(get('ai-send').disabled,false);
 console.log('unified research UI PASS');
})().catch(e=>{console.error(e);process.exitCode=1;});
'''


@pytest.mark.parametrize('scenario', ['sequence', 'stale'])
def test_combined_research_ui_confirmation_progress_results_and_stale_reply(tmp_path, scenario):
    result = subprocess.run([shutil.which('node'), '-e', SCRIPT, str(ROOT / 'Part3/web'), scenario],
                            cwd=tmp_path, capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
