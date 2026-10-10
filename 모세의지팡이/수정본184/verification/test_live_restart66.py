"""User confirmation and lifecycle reporting use the public Part1 API only."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import io
import json
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'Part3'))
from Part1 import live_control
from lab import backtest_jobs, catalog, desktop_window, live_processes, runtime_lifecycle, server, unified_live
from lab.ai.model_runtime import RUNTIME
from common_ai import client as common_client


@pytest.fixture
def owner(monkeypatch):
    result = {'start_live': Mock(return_value=(True, '감시 엔진 준비 완료 · MT5 연결 대기')),
              'list_live_engines': Mock(return_value=[]),
              'EngineConflictError': live_control.EngineConflictError}
    monkeypatch.setattr(unified_live, 'control', lambda: result)
    monkeypatch.setattr(unified_live, '_closing', False)
    monkeypatch.setattr(live_processes, 'current_process_identity', lambda: {'pid': 123, 'created': '200'})
    return result


def engine(pid=101, created='100', copy='다른 수정본'):
    return {'pid': pid, 'created': created, 'copy_name': copy,
            'current_copy': False, 'state': 'ready', 'pipe_connected': False,
            'supports_shutdown': True}


def post(route, data):
    handler = object.__new__(server.Handler)
    handler.server = SimpleNamespace(token='test-local', server_port=8763, last_seen=None)
    handler.path = route
    payload = json.dumps(data).encode('utf-8')
    handler.headers = {'X-Lab-Token': 'test-local', 'Content-Length': str(len(payload))}
    handler.rfile = io.BytesIO(payload)
    replies = []
    handler.send = lambda status, body: replies.append((status, body))
    handler.do_POST()
    return replies[-1]


@pytest.mark.parametrize('payload', [[], {'restart': 1}, {'restart': 'true'}, {'expected_engines': []}])
def test_invalid_restart_request_never_reaches_engine_api(owner, payload):
    status, body = post('/api/mo/live/start', payload)
    assert status == 400 and body['error']
    owner['start_live'].assert_not_called()


def test_first_start_supplies_ui_owner_and_only_returns_ready_after_part1(owner):
    status, body = post('/api/mo/live/start', {})
    assert status == 200 and body['ok'] and not body['confirmation_required']
    assert '준비 완료' in body['message'] and 'MT5 연결 대기' in body['message']
    owner['start_live'].assert_called_once_with(restart=False, expected_engines=None,
                                              ui_owner={'pid': 123, 'created': '200'})


def test_other_copy_is_confirmation_response_not_success(owner):
    rows = [engine()]
    owner['start_live'].side_effect = live_control.EngineConflictError(rows)
    status, body = post('/api/mo/live/start', {})
    assert status == 200 and body['ok'] is False and body['confirmation_required'] is True
    assert body['engines'] == rows
    assert '종료하고 다시 시작' in body['message']


def test_confirmed_identifiers_reach_same_public_start_api(owner):
    identifiers = [{'pid': 101, 'created': '100'}]
    status, body = post('/api/mo/live/start', {'restart': True, 'expected_engines': identifiers})
    assert status == 200 and body['ok']
    assert owner['start_live'].call_args.kwargs['expected_engines'] == identifiers
    assert owner['start_live'].call_args.kwargs['restart'] is True


def test_startup_failure_is_http_error_not_running(owner):
    owner['start_live'].return_value = (False, 'STAFF 파이프를 만들지 못했습니다.')
    status, body = post('/api/mo/live/start', {})
    assert status == 400 and '파이프' in body['error']


def test_forced_restart_warning_is_preserved_in_api(owner):
    owner['start_live'].return_value = live_control.LiveStartResult(True, '준비 완료', warnings=['저장 미확인'])
    status, body = post('/api/mo/live/start', {})
    assert status == 200 and body['warnings'] == ['저장 미확인']


def test_engine_listing_only_passes_public_fields(owner):
    rows = [engine()]
    owner['list_live_engines'].return_value = rows
    assert unified_live.engines() == {'engines': rows}
    assert 'script' not in rows[0] and 'root' not in rows[0]


def test_global_stop_and_shutdown_share_part1_api_and_preserve_warning(monkeypatch):
    result = {'ok': True, 'message': '종료 완료', 'saved_pids': [101], 'forced_pids': [102],
              'warnings': ['PID 102 저장 미확인'], 'remaining_pids': []}
    api = SimpleNamespace(stop_all_engines=Mock(return_value=result))
    monkeypatch.setattr(live_processes, 'process_api', lambda: api)
    monkeypatch.setattr(unified_live, '_closing', False)
    assert unified_live.stop() == result
    assert unified_live.shutdown() == result
    assert unified_live._closing is True
    assert api.stop_all_engines.call_count == 2


def test_failure_result_keeps_window_open_and_allows_retry(monkeypatch):
    api = SimpleNamespace(stop_all_engines=Mock(return_value={
        'ok': False, 'message': '엔진 종료 실패 · 남은 PID: 101'}))
    monkeypatch.setattr(live_processes, 'process_api', lambda: api)
    monkeypatch.setattr(unified_live, '_closing', False)
    with pytest.raises(RuntimeError, match='남은 PID'):
        unified_live.shutdown()
    assert unified_live._closing is False


def test_close_warning_is_shown_before_window_closes(monkeypatch, tmp_path):
    order = []
    window = SimpleNamespace(destroy=lambda: order.append('closed'))
    # This warning test concerns only mocked live-engine shutdown. Never read
    # the configured warehouse or show native job/model confirmation dialogs.
    monkeypatch.setattr(catalog, 'ROOT', tmp_path / 'project' / 'Part3')
    ai_client = Mock(spec=['shutdown', 'close'])
    monkeypatch.setattr(common_client, 'Client', Mock(return_value=ai_client))
    monkeypatch.setattr(runtime_lifecycle, 'prepare_shutdown', lambda: None)
    monkeypatch.setattr(backtest_jobs, 'shutdown', lambda **_kwargs: {'ok': True, 'warnings': []})
    monkeypatch.setattr(backtest_jobs, '_closing', False)
    monkeypatch.setattr(backtest_jobs, '_SESSION_CONTEXTS', {})
    monkeypatch.setattr(backtest_jobs, '_SHUTDOWN_PENDING', {})
    monkeypatch.setattr(RUNTIME, 'shutdown', lambda **_kwargs: None)
    monkeypatch.setattr(desktop_window, '_confirm_force_close', lambda _window: pytest.fail('unexpected confirmation'))
    monkeypatch.setattr(desktop_window, '_show_close_error', lambda message: pytest.fail(message))
    monkeypatch.setattr(unified_live, 'shutdown', lambda: {'ok': True, 'warnings': ['강제 종료 · 저장 미확인']})
    monkeypatch.setattr(desktop_window, '_show_close_warning', lambda result: order.append(result['warnings']))
    closer = desktop_window._EngineCloser(window)
    closer._stop_and_close()
    assert closer.ready and order == [['강제 종료 · 저장 미확인'], 'closed']
    ai_client.shutdown.assert_called_once_with(timeout=45)
    ai_client.close.assert_called_once_with()


def test_real_web_js_requires_confirmation_rechecks_and_does_not_duplicate():
    node = shutil.which('node')
    assert node, 'Node.js is required by current web verification'
    script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
class Element {
    constructor(){this.value='';this.checked=false;this.textContent='';this.dataset={};this.disabled=false;
        this.options=[{},{}];this.classes=new Set();this.children=[];
        this.classList={toggle:(n,on)=>on?this.classes.add(n):this.classes.delete(n),
            add:n=>this.classes.add(n),remove:n=>this.classes.delete(n)};}
    append(...items){this.children.push(...items);} replaceChildren(){this.children=[];}
    setAttribute(){} addEventListener(){} querySelectorAll(){return [];}
}
const root=process.argv[1],html=fs.readFileSync(root+'/index.html','utf8');
const elements=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element()]));
const get=id=>elements.get(id), calls=[],confirms=[];
let replies=[],answers=[],pending=null;
const context=vm.createContext({console,
    document:{body:{dataset:{}},querySelector:s=>get(s.slice(1))||null,querySelectorAll:()=>[],
        createElement:()=>new Element(),createTextNode:s=>({textContent:s})},Option:Element,
    window:{dispatchEvent(){},addEventListener(){},confirm:text=>{confirms.push(text);return answers.shift();}},
    setInterval(){},setTimeout(){return 1;},clearTimeout(){},
    api:(route,body)=>{
        if(route.startsWith('mo/live/status'))return Promise.resolve({modules:{STAFF:{state:'대기'},ENGINE:{state:'대기'}},lines:[],pipe_connected:false});
        calls.push({route,body});if(pending)return pending;
        const result=replies.shift();return result instanceof Error?Promise.reject(result):Promise.resolve(result);
    }
});
const run=s=>vm.runInContext(s,context);
const old={pid:101,created:'100',copy_name:'수정본 이전',supports_shutdown:true};
const fresh={pid:102,created:'200',copy_name:'새 엔진',supports_shutdown:false};
const conflict=row=>({ok:false,confirmation_required:true,engines:[row]});
const reset=()=>{calls.length=0;confirms.length=0;answers=[];replies=[];pending=null;};
(async()=>{
    run(fs.readFileSync(root+'/unified.js','utf8'));await new Promise(r=>setImmediate(r));reset();
    replies=[conflict(old)];answers=[false];await run('moLiveAction("start")');
    assert.equal(calls.length,1);assert.equal(confirms.length,1);assert(confirms[0].includes('종료하고 다시 시작하시겠습니까'));
    assert(get('live-action-message').textContent.includes('취소'));assert(!get('live-start').disabled);
    reset();replies=[conflict(old),conflict(fresh),{ok:true,message:'준비 완료 · MT5 연결 대기',warnings:['저장 미확인']}];answers=[true,true];
    await run('moLiveAction("start")');assert.equal(calls.length,3);assert.equal(confirms.length,2);
    assert.deepEqual(JSON.parse(JSON.stringify(calls[1].body)),{restart:true,expected_engines:[{pid:101,created:'100'}]});
    assert.deepEqual(JSON.parse(JSON.stringify(calls[2].body)),{restart:true,expected_engines:[{pid:102,created:'200'}]});
    assert(confirms[1].includes('목록이 변경'));assert(confirms[1].includes('강제 종료'));
    assert(get('live-action-message').classes.has('moses-warning'));
    const message=get('live-action-message').textContent;await run('moLiveStatus()');
    assert.equal(get('live-action-message').textContent,message);assert.equal(get('live-message').textContent,'라이브 감시 연결대기');
    reset();let release;pending=new Promise(r=>release=r);
    const first=run('moLiveAction("start")');await Promise.resolve();assert(get('live-start').disabled&&get('live-stop').disabled);
    await run('moLiveAction("start")');assert.equal(calls.length,1);release({ok:true,message:'준비 완료'});await first;
    assert(!get('live-start').disabled&&!get('live-stop').disabled);
    reset();replies=[new Error('파이프 점유로 준비 실패')];await run('moLiveAction("start")');
    assert(get('live-action-message').textContent.includes('시작 실패'));assert(get('live-action-message').classes.has('moses-warning'));
    assert(run('moLiveActionRecords.some(text => text.includes("준비 실패"))'));
    reset();replies=[{ok:true,message:'상태 저장 후 종료 완료',warnings:['일부 엔진 강제 종료 · 기록 저장 미확인']}];await run('moLiveAction("stop")');
    assert.equal(calls[0].route,'mo/live/stop');assert(get('live-action-message').textContent.includes('종료 완료'));
    assert(run('moLiveActionRecords.some(text => text.includes("저장 후 종료"))'));
    assert(get('live-action-message').textContent.includes('로그를 확인'));
    assert(run('moLiveActionRecords.some(text => text.includes("기록 저장 미확인"))'));
    console.log('restart/changed identities/cancel/readiness/warnings/inflight actions PASS');
})().catch(e=>{console.error(e);process.exit(1);});
'''
    result = subprocess.run([node, '-e', script, str(ROOT / 'Part3/web')],
                            capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
