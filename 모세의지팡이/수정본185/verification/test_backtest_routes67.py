"""Authenticated common-job routes and browser command confirmation boundaries."""
from __future__ import annotations

import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part3'))
from lab import server, unified_backtest, unified_settings
from common_ai.client import Client


def handler(path, data=None, token='test-token', origin=None):
    value = object.__new__(server.Handler)
    value.server = SimpleNamespace(token='test-token', server_port=8763, last_seen=None)
    value.path = path
    value.headers = {'X-Lab-Token': token}
    if origin is not None:
        value.headers['Origin'] = origin
    payload = json.dumps(data).encode() if data is not None else b''
    value.headers['Content-Length'] = str(len(payload))
    value.rfile = io.BytesIO(payload)
    value.replies = []
    value.send = lambda code, body, *args: value.replies.append((code, body))
    return value


@pytest.mark.parametrize('token,origin', [('', None), ('wrong', None), ('test-token', 'https://example.test')])
def test_command_and_reconnect_require_ui_authorization(monkeypatch, token, origin):
    call = Mock(side_effect=AssertionError('unauthorized action'))
    monkeypatch.setattr(server, 'backtest_command_session', call)
    for route in ('/api/ai/backtest/chat', '/api/ai/backtest/confirm', '/api/mo/backtest/reconnect'):
        value = handler(route, {'session': 'one', 'job_id': 'a' * 32}, token, origin)
        value.do_POST()
        assert value.replies[-1][0] == 403
    value = handler('/api/mo/backtest/recent', token=token, origin=origin)
    value.do_GET()
    assert value.replies[-1][0] == 403
    assert call.call_count == 0


def test_common_recent_reconnect_and_plan_revision_routes(monkeypatch):
    identifier = 'a' * 32
    monkeypatch.setattr(unified_backtest, 'recent', lambda: {'items': [{'job_id': identifier}]})
    reconnect = Mock(return_value={'job_id': identifier, 'phase': 'run'})
    confirm = Mock(return_value={'ok': True})
    monkeypatch.setattr(unified_backtest, 'reconnect', reconnect)
    monkeypatch.setattr(unified_backtest, 'confirm', confirm)
    value = handler('/api/mo/backtest/recent')
    value.do_GET()
    assert value.replies[-1] == (200, {'items': [{'job_id': identifier}]})
    value = handler('/api/mo/backtest/reconnect', {'job_id': identifier})
    value.do_POST()
    assert value.replies[-1][1]['job_id'] == identifier
    reconnect.assert_called_once_with(identifier)
    value = handler('/api/mo/backtest/confirm', {'job_id': identifier, 'plan_revision': 'reviewed'})
    value.do_POST()
    assert value.replies[-1] == (200, {'ok': True})
    confirm.assert_called_once_with(identifier, 'reviewed')


def test_commands_are_independent_of_strategy_sessions(monkeypatch):
    command = SimpleNamespace(send=Mock(return_value={'can_confirm': True}), confirm=Mock(return_value={'ok': True}))
    monkeypatch.setattr(server, 'backtest_command_session', lambda _: command)
    strategy = {'last_intent': {'conditions': ['unchanged']}}
    monkeypatch.setattr(server, 'AI_SESSIONS', {'one': strategy})
    monkeypatch.setattr(server, 'BACKTEST_COMMAND_SESSIONS', {'one': command})
    assert server.ai_post('/api/ai/backtest/chat', {'session': 'one', 'message': '테스트해'})['can_confirm']
    server.ai_post('/api/ai/backtest/confirm', {'session': 'one', 'revision': 'reviewed'})
    command.confirm.assert_called_once_with('reviewed')
    server.ai_post('/api/ai/backtest/reset', {'session': 'one'})
    assert server.BACKTEST_COMMAND_SESSIONS == {}
    assert server.AI_SESSIONS['one'] is strategy


def test_model_settings_clear_both_pending_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(server, 'ROOT', tmp_path / 'Part3')
    monkeypatch.setattr(server, 'AI_SETTINGS', tmp_path / 'settings/ai_settings.json')
    monkeypatch.setattr(server, 'AI_SESSIONS', {'strategy': object()})
    monkeypatch.setattr(server, 'BACKTEST_COMMAND_SESSIONS', {'command': object()})
    monkeypatch.setattr(server, 'RESEARCH_SESSIONS', {})
    for method in ('_start', '_request'):
        monkeypatch.setattr(Client, method, Mock(side_effect=AssertionError('No common AI service in this fixture')))
    unified_settings.save_ai({'model': 'qwen3.5:4b'})
    assert server.AI_SESSIONS == {} and server.BACKTEST_COMMAND_SESSIONS == {}
    assert server.ai_settings()['model'] == 'qwen3.5:4b'
    Client._start.assert_not_called()
    Client._request.assert_not_called()


def test_command_browser_flow_and_recent_reconnection(tmp_path):
    node = shutil.which('node')
    assert node, 'Node.js is required by current verification'
    script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
class Element {
 constructor(){this.textContent='';this.value='';this.disabled=false;this.children=[];this.flags=new Set();this.classList={toggle:(k,v)=>v?this.flags.add(k):this.flags.delete(k)};}
 replaceChildren(...x){this.children=x;this.textContent='';} append(...x){this.children.push(...x);} setAttribute(){}
}
const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
const events={},calls=[],adopted=[];let result={},recent={items:[]};
const state={job:null,view:'backtest'};
const context=vm.createContext({document:{getElementById:get},window:{addEventListener:(n,f)=>events[n]=f},
 sessionStorage:{getItem:()=>null,setItem:()=>{}},Date,Math,Set,Array,
 moElement:(tag,text)=>{const e=new Element();e.textContent=text||'';e.tag=tag;return e;},moState:state,
 moAdoptBacktestJob:async x=>{adopted.push(x);state.job=x.job_id;},moScheduleBacktestStatus:()=>{},
 api:async (route,data)=>{calls.push([route,data]);if(route==='mo/backtest/recent')return recent;
 if(route==='mo/backtest/reconnect')return {job_id:data.job_id,phase:'run'};
 if(result instanceof Error)throw result;return result;}});
vm.runInContext(fs.readFileSync(process.argv[1]+'/backtest_jobs.js','utf8'),context);
(async()=>{
 await context.window.MosesBacktestJobs.refresh();assert.match(get('bt-jobs-message').textContent,/아직/);
 const id='a'.repeat(32);recent={items:[{job_id:id,phase:'run',symbol:'XAUUSD+',start:'2026-09-01',end:'2026-10-01'}]};
 await context.window.MosesBacktestJobs.refresh();assert.equal(adopted.length,1);assert.equal(adopted[0].job_id,id);
 state.job=null;recent={items:[{job_id:id,phase:'run'},{job_id:'c'.repeat(32),phase:'confirm'}]};
 const before=adopted.length;await context.window.MosesBacktestJobs.refresh();assert.equal(adopted.length,before);assert.match(get('bt-jobs-message').textContent,/여러 개/);
 await get('bt-jobs-list').children[1].children.find(child=>child.tag==='button').onclick();
 assert.equal(calls.at(-1)[0],'mo/backtest/reconnect');assert.equal(adopted.at(-1).job_id,'c'.repeat(32));
 assert.equal(calls.some(x=>x[0]==='mo/backtest/start'),false);
 console.log('common UI flow PASS');
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    result = subprocess.run([node, '-e', script, str(ROOT / 'Part3/web')], cwd=tmp_path,
                            capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'common UI flow PASS' in result.stdout
