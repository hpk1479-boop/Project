"""Current conversation reset stays visible and changes only strategy chat state."""
from __future__ import annotations

import importlib.util
import json
from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part3'))
from lab import server
from lab.ai import model_runtime


def test_reset_button_is_unique_above_chat_and_outside_collapsed_settings():
    class Tree(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parents = []
            self.reset = []
            self.ids = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if attrs.get('id'):
                self.ids.append(attrs['id'])
            if attrs.get('id') == 'ai-reset':
                self.reset.append((list(self.parents), attrs))
            if tag not in {'input', 'meta', 'link', 'br', 'hr', 'img'}:
                self.parents.append((tag, attrs))

        def handle_endtag(self, tag):
            for index in range(len(self.parents) - 1, -1, -1):
                if self.parents[index][0] == tag:
                    del self.parents[index:]
                    break

    parsed = Tree()
    parsed.feed((ROOT / 'Part3/web/index.html').read_text('utf-8'))
    assert len(parsed.reset) == 1
    parents, attrs = parsed.reset[0]
    assert not any(tag == 'details' or 'hidden' in data.get('class', '').split() for tag, data in parents)
    assert any('ai-heading' in data.get('class', '').split() for _, data in parents)
    assert attrs['type'] == 'button'
    assert parsed.ids.index('ai-reset') < parsed.ids.index('ai-messages')


SCRIPT = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
class Element {
 constructor(){this.value='';this.textContent='';this.children=[];this.disabled=false;this.flags=new Set(['hidden']);this.events={};this.classList={add:k=>this.flags.add(k),remove:k=>this.flags.delete(k),contains:k=>this.flags.has(k),toggle:(k,v)=>v?this.flags.add(k):this.flags.delete(k)};}
 addEventListener(n,f){this.events[n]=f;} appendChild(e){this.children.push(e);} scrollIntoView(){} focus(){this.focused=true;} setAttribute(k,v){this[k]=v;}
}
const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
const domEvents={},windowEvents={},calls=[];let invalidated=0,shouldFail=false,response={};
const context=vm.createContext({
 document:{querySelector:s=>get(s.slice(1)),createElement:()=>new Element(),addEventListener:(n,f)=>domEvents[n]=f,body:{prepend(){}}},
 window:{addEventListener:(n,f)=>windowEvents[n]=f,part3IntentDisplay:{render:()=>new Element()},part3InvalidateAIRecipe:()=>invalidated++,part3ApplyAIRecipe:async()=>{}},
 sessionStorage:{getItem:()=> 'test-token'},crypto:{randomUUID:()=> 'current-session'},Date,JSON,setInterval:()=>{},setTimeout,clearTimeout,
 fetch:async(path,request={})=>{
  const data=request.body?JSON.parse(request.body):null;calls.push({path,data});
  if(path==='/api/ai/reset'&&shouldFail)return {ok:false,status:500,json:async()=>({error:'초기화 연결 실패'})};
  const body=path==='/api/ai/settings'?{settings:{provider:'ollama',model:'sample'}}:path==='/api/ai/chat'?response:{ok:true};
  return {ok:true,status:200,json:async()=>body};
 }
});
vm.runInContext(fs.readFileSync(process.argv[1]+'/ai_chat.js','utf8'),context);
(async()=>{
 domEvents.DOMContentLoaded();await new Promise(resolve=>setImmediate(resolve));
 get('ai-text').value='최초 전략';response={revision:9,result:{supported:true},can_apply:true};await get('ai-send').events.click();
 assert.equal(get('ai-pending').flags.has('hidden'),false);
 get('ai-messages').textContent='기존 대화';get('ai-text').value='작성 중인 수정 초안';get('ai-pending-text').textContent='이전 확인 요청';
 const before=invalidated;
 if(process.argv[2]==='failure'){
  shouldFail=true;await get('ai-reset').events.click();
  assert.equal(get('ai-text').value,'작성 중인 수정 초안');assert.equal(get('ai-messages').textContent,'기존 대화');
  assert.equal(get('ai-pending').flags.has('hidden'),false);assert.equal(invalidated,before);
  assert.equal(get('ai-messages').children.at(-1).textContent,'초기화 연결 실패');
 }else{
  await get('ai-reset').events.click();
  assert.equal(get('ai-messages').textContent,'');assert.equal(get('ai-text').value,'');assert.equal(get('ai-text').focused,true);
  assert.equal(get('ai-pending-text').textContent,'');assert.equal(get('ai-pending').flags.has('hidden'),true);
  assert.equal(get('ai-mode').textContent,'새 전략 대화');assert.equal(invalidated,before+1);
  const applies=calls.filter(x=>x.path==='/api/ai/apply').length;await get('ai-confirm').events.click();assert.equal(calls.filter(x=>x.path==='/api/ai/apply').length,applies);
  get('ai-text').value='다른 신규 전략';response={revision:1,result:{supported:true},can_apply:true};await get('ai-send').events.click();await get('ai-confirm').events.click();
  assert.equal(calls.filter(x=>x.path==='/api/ai/apply').at(-1).data.revision,1);
 }
 assert.deepEqual(calls.filter(x=>x.path==='/api/ai/reset').map(x=>x.data),[{session:'current-session',research:true}]);
 assert.equal(calls.some(x=>['/api/ai/backtest/reset','/api/ai/settings','/api/generate'].includes(x.path)&&x.data!==null),false);
 assert.equal(get('ai-reset').disabled,false);assert.equal(get('ai-send').disabled,false);
 console.log('current AI reset PASS');
})().catch(e=>{console.error(e);process.exitCode=1;});
'''


@pytest.mark.parametrize('scenario', ['success', 'failure'])
def test_current_ai_reset_browser_events(tmp_path, scenario):
    node = shutil.which('node')
    assert node, 'Node.js is required by current web verification'
    result = subprocess.run([node, '-e', SCRIPT, str(ROOT / 'Part3/web'), scenario], cwd=tmp_path,
                            capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'current AI reset PASS' in result.stdout


def test_server_reset_keeps_other_sessions_settings_generated_files_and_model(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('revision_fixture72', ROOT / 'Part3/tests/test_ai_revision59.py')
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    settings = tmp_path / 'projects/ai_settings.json'
    settings.parent.mkdir()
    settings.write_text('{"provider":"ollama","model":"sample"}', encoding='utf-8')
    generated = tmp_path / 'generated/Test_SPECIAL001.py'
    generated.parent.mkdir()
    generated.write_text('# existing strategy', encoding='utf-8')
    saved = {path: path.read_bytes() for path in [settings, generated]}
    monkeypatch.setattr(server, 'AI_SETTINGS', settings)
    monkeypatch.setattr(server, 'AI_SESSIONS', {})
    backtest = object()
    monkeypatch.setattr(server, 'BACKTEST_COMMAND_SESSIONS', {'current': backtest})
    from lab.ai import shared_provider
    monkeypatch.setattr(shared_provider, 'shared_from_settings', lambda *_, **_kwargs: fixture.RecordingProvider([
        fixture.reply(fixture.original()), fixture.reply(fixture.changed())]))
    invalidate, close = Mock(), Mock()
    monkeypatch.setattr(model_runtime.RUNTIME, 'invalidate', invalidate)
    monkeypatch.setattr(model_runtime.RUNTIME, 'close', close)
    first = server.ai_post('/api/ai/chat', {'session': 'current', 'message': '최초 전략'})
    server.ai_post('/api/ai/chat', {'session': 'other', 'message': '다른 창의 전략'})
    current, other = server.AI_SESSIONS['current'], server.AI_SESSIONS['other']
    generation = model_runtime.RUNTIME.generation
    assert server.ai_post('/api/ai/reset', {'session': 'current'}) == {'ok': True}
    assert server.AI_SESSIONS == {'other': other}
    assert server.BACKTEST_COMMAND_SESSIONS == {'current': backtest}
    assert model_runtime.RUNTIME.generation == generation
    invalidate.assert_not_called()
    close.assert_not_called()
    assert all(path.read_bytes() == contents for path, contents in saved.items())
    new = server.ai_post('/api/ai/chat', {'session': 'current', 'message': '별도 신규 전략'})
    assert server.AI_SESSIONS['current'] is not current
    assert new['revision'] == first['revision'] == 1
    assert new['is_revision'] is False
    request=json.loads(server.AI_SESSIONS['current'].provider.seen[0]['content'])
    assert request['message'] == '별도 신규 전략'
    assert request['context'] == {'current_strategy':None,'question':None}
