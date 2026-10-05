"""Execute the shipped UI and index DOM against fail-closed synthetic APIs.

These tests do not start a server, MT5, an engine, or a real backend. The DOM
model is shared with the existing strategy-dialog verification; timers remain
under test control so deferred responses can be delivered in an exact order.
"""
from __future__ import annotations

import ast
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _existing_dom_fixture():
    tree = ast.parse((ROOT / "verification/test_strategy_popup69.py").read_text("utf-8"))
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "SCRIPT" for target in statement.targets
        ):
            script = ast.literal_eval(statement.value)
            return script.split("const choices=", 1)[0]
    raise AssertionError("Existing index DOM fixture is unavailable")


SCRIPT = _existing_dom_fixture() + r'''
const clone = value => JSON.parse(JSON.stringify(value));
const job1 = 'a'.repeat(32), job2 = 'b'.repeat(32);
const options = {symbol:'XAUUSD+', symbols:['XAUUSD+','EURUSD'], start:'2026-09-01', end:'2026-10-01',
    mode:'BAR', specials:[], special_settings:{}, trigger_choices:[], watch_text:'original watch',
    watch_chat_id:'BACKTEST', spread_points:{'XAUUSD+':12}, warehouse_set:true};
const live = {modules:{STAFF:{name:'STAFF',state:'연결 대기'},ENGINE:{name:'ENGINE',state:'연결 대기'}},
    lines:[], pipe_connected:false};
const request = {symbol:'XAUUSD+',start:'2026-09-01',end:'2026-10-01',mode:'BAR',target_mode:'SPECIAL',
    specials:['SPECIAL2'],special_settings:{SPECIAL2:{enabled:true,trigger:'무지성 올존',
        time_filters:{MAIN_ASIA:{enabled:true,start:'0900',end:'1530'}}}},
    watch_text:'original watch',watch_chat_id:'BACKTEST',result_mode:'VIRTUAL_ENTRY',
    spread_points:12,build_only:false,rebuild:false};
const handlers = {}, calls = [], timers = new Map();let timerId=0;
let status = {job_id:job1,phase:'run',active:true,result_ready:false,message:'진행 중'};
let recent = [];
const context = vm.createContext({console,document,Option,Event:class {constructor(type){this.type=type;}},
    window:{dispatchEvent(){},addEventListener(){},confirm:()=>true},
    sessionStorage:{getItem:()=>null,setItem(){}},
    setInterval:()=>1, setTimeout:(callback,delay)=>{const id=++timerId;timers.set(id,{callback,delay});return id;},
    clearTimeout:id=>timers.delete(id),
    api:async(route,body)=>{
        calls.push({route,body:body===undefined?undefined:clone(body)});
        const name=route.split('?')[0];
        if(handlers[name])return await handlers[name](route,body);
        if(name==='mo/live/status')return clone(live);
        if(name==='mo/backtest/options')return clone(options);
        if(name==='mo/backtest/status')return clone(status);
        if(name==='mo/backtest/recent')return {items:clone(recent)};
        if(name==='mo/backtest/start')return {job_id:job2,phase:'planning'};
        if(name==='mo/backtest/result')return {run_id:'synthetic',status:'ERROR',alerts_preview:[],warnings:[]};
        throw Error('Unmocked backend call: '+route);
    }});
const run = code => vm.runInContext(code,context);
const get = id => document.querySelector('#'+id);
const value = code => clone(run(code));
const tick = () => new Promise(resolve=>setImmediate(resolve));
function deferred() {let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
function clickElement(element) {
    assert(element,'Control must exist in the shipped index.html');
    if(element.disabled)return Promise.resolve();
    const event={target:element,preventDefault(){}};
    const callbacks=[element.onclick,...(element.listeners.click||[])].filter(Boolean);
    assert(callbacks.length,'Shipped source must bind the control');
    return Promise.all(callbacks.map(callback=>callback.call(element,event)));
}
const click = id => clickElement(get(id));
const nav = name => clickElement(document.querySelector('.moses-nav button[data-moses-view="'+name+'"]'));
const shown = id => !get(id).classList.contains('hidden');
const starts = () => calls.filter(call=>call.route==='mo/backtest/start');
function assertPage(view,page) {
    assert.equal(run('moState.view'),view);
    assert(shown('view-'+view));
    for(const other of ['live','backtest','strategy','settings'])if(other!==view)assert(!shown('view-'+other));
    if(view==='backtest'){
        assert.equal(run('moState.backtestPage'),page);
        for(const name of ['setup','specials','progress','result'])assert.equal(shown('mo-bt-'+name+'-page'),name===page);
    }
}
async function prepareJob(phase='error',{hasRequest=true,result=false,active=false}={}) {
    await nav('backtest');
    status={job_id:job1,phase,active,result_ready:result,message:'모의 '+phase};
    context.fixtureRequest=hasRequest?clone(request):null;
    await run('moAdoptBacktestJob({job_id:"'+job1+'",phase:"run"},{request:fixtureRequest})');
    await tick();
}
function assertRecovery({retry=true,result=false}={}) {
    assert.equal(get('mo-bt-stop-button').disabled,true);
    assert.equal(get('mo-bt-start-button').disabled,false);
    assert(shown('mo-bt-recovery-card'));
    assert.equal(get('mo-bt-retry').disabled,!retry);
    assert.equal(get('mo-bt-recovery-setup').disabled,false);
    assert.equal(get('mo-bt-open-result').disabled,!result);
    assert.equal(get('mo-bt-progress-result').disabled,!result);
}

(async()=>{
    run(fs.readFileSync(root+'/unified.js','utf8'));
    run(fs.readFileSync(root+'/backtest_jobs.js','utf8'));
    await tick();
    get('mo-bt-target').value='SPECIAL';get('mo-bt-result-mode').value='ALERT_ONLY';
    get('mo-bt-detail').checked=false;
    if(['terminal_error','terminal_cancelled','terminal_interrupted'].includes(scenario)) {
        await prepareJob(scenario.slice('terminal_'.length));
        assertPage('backtest','progress');assertRecovery();assert.equal(run('moBacktestPoll.done'),true);
        const before=starts().length;
        await click('mo-bt-recovery-setup');assertPage('backtest','setup');assert.equal(starts().length,before);
    } else if(scenario==='error_result_available') {
        await prepareJob('error',{result:true});assertPage('backtest','progress');assertRecovery({result:true});
        assert.equal(run('moState.resultShown'),true);assert(get('mo-bt-result').textContent.includes('synthetic'));
        await click('mo-bt-progress-result');assertPage('backtest','result');
    } else if(scenario==='result_fetch_failure') {
        handlers['mo/backtest/result']=async()=>{throw Error('모의 결과 읽기 실패');};
        await prepareJob('error',{result:true});assertPage('backtest','progress');assertRecovery();
        assert.equal(run('moBacktestPoll.done'),true);assert.equal(run('moState.resultShown'),false);
        assert(get('mo-bt-warnings').textContent.includes('모의 결과 읽기 실패'));
    } else if(scenario==='failed_without_snapshot') {
        await prepareJob('error',{hasRequest:false});assertRecovery({retry:false});
        assert(get('mo-bt-recovery-help').textContent.includes('설정 화면'));
        await run('moBacktestRetry()');assert.equal(starts().length,0);
        await click('mo-bt-recovery-setup');assertPage('backtest','setup');
    } else if(scenario==='active_interruption') {
        await prepareJob('interrupted',{active:true});assertPage('backtest','progress');
        assert.equal(run('moBacktestPoll.done'),false);assert.equal(get('mo-bt-stop-button').disabled,false);
        assert.equal(get('mo-bt-start-button').disabled,true);assert.equal(get('mo-bt-retry').disabled,true);
        assert(!shown('mo-bt-recovery-card'));await run('moBacktestRetry()');assert.equal(starts().length,0);
    } else if(['backtest_options_delayed','backtest_options_failed'].includes(scenario)) {
        const gate=deferred();handlers['mo/backtest/options']=()=>gate.promise;
        const pending=nav('backtest');assertPage('backtest','setup');
        assert(calls.some(call=>call.route==='mo/backtest/recent'),'Recent jobs must not wait for options');
        if(scenario.endsWith('failed'))gate.reject(Error('모의 설정 로드 실패'));else gate.resolve(clone(options));
        await pending;assertPage('backtest','setup');
        if(scenario.endsWith('failed'))assert(shown('mo-bt-setup-error'));
    } else if(scenario==='live_immediate') {
        await nav('backtest');const gate=deferred();handlers['mo/live/status']=()=>gate.promise;
        const pending=nav('live');assertPage('live');assert(shown('live-main-page'));
        gate.resolve(clone(live));await pending;assertPage('live');
    } else if(['latest_options_success','latest_options_failure'].includes(scenario)) {
        const older=deferred(),newer=deferred();let count=0;
        handlers['mo/backtest/options']=()=>++count===1?older.promise:newer.promise;
        const oldNavigation=nav('backtest');await nav('live');const latest=nav('backtest');
        newer.resolve({...clone(options),symbol:'EURUSD',start:'2026-08-01'});await latest;
        assertPage('backtest','setup');assert.equal(get('mo-bt-symbol').value,'EURUSD');
        if(scenario.endsWith('failure'))older.reject(Error('obsolete navigation failed'));
        else older.resolve({...clone(options),symbol:'OBSOLETE',warehouse_set:false});
        await oldNavigation;assertPage('backtest','setup');
        assert.equal(get('mo-bt-symbol').value,'EURUSD');assert(get('mo-bt-data-state').textContent.includes('연결됨'));
        assert(!get('mo-bt-setup-error').textContent.includes('obsolete'));
    } else if(['late_start_live','late_start_setup'].includes(scenario)) {
        await nav('backtest');const gate=deferred();handlers['mo/backtest/start']=()=>gate.promise;
        const pending=click('mo-bt-start-button');assert.equal(starts().length,1);
        const sent=clone(starts()[0].body);
        if(scenario.endsWith('live'))await nav('live');
        else {run('moBacktestPage("specials")');await nav('backtest');assertPage('backtest','setup');}
        gate.resolve({job_id:job2,phase:'run'});await pending;await tick();
        if(scenario.endsWith('live'))assertPage('live');else assertPage('backtest','setup');
        assert.equal(run('moState.job'),job2);assert.deepEqual(value('moBacktestPoll.request'),sent);
        await nav('backtest');assertPage('backtest','setup');await click('mo-bt-open-progress');assertPage('backtest','progress');
    } else if(['retry_error','retry_cancelled','retry_interrupted'].includes(scenario)) {
        const phase=scenario.slice('retry_'.length);await prepareJob(phase);assertRecovery();
        get('mo-bt-symbol').value='CHANGED';get('mo-bt-start').value='1999-01-01';
        const gate=deferred();handlers['mo/backtest/start']=()=>gate.promise;
        const first=click('mo-bt-retry'),second=click('mo-bt-retry');
        assert.equal(starts().length,1);assert.deepEqual(starts()[0].body,request);
        assert.equal(get('mo-bt-retry').disabled,true);
        assert.deepEqual(value('moBacktestPoll.request'),request);
        status={job_id:job2,phase:'run',active:true,result_ready:false};
        gate.resolve({job_id:job2,phase:'run'});await Promise.all([first,second]);await tick();
        assert.equal(run('moState.job'),job2);assertPage('backtest','progress');assert.equal(starts().length,1);
    } else if(scenario==='late_retry_setup') {
        await prepareJob();const gate=deferred();handlers['mo/backtest/start']=()=>gate.promise;
        const pending=click('mo-bt-retry');await click('mo-bt-recovery-setup');assertPage('backtest','setup');
        gate.resolve({job_id:job2,phase:'run'});await pending;await tick();
        assertPage('backtest','setup');assert.equal(run('moState.job'),job2);
        assert.deepEqual(value('moBacktestPoll.request'),request);await click('mo-bt-open-progress');assertPage('backtest','progress');
    } else if(scenario==='snapshot_is_deep_clone') {
        await nav('backtest');const original=clone(request),gate=deferred();context.fixtureRequest=original;
        handlers['mo/backtest/start']=()=>gate.promise;
        const pending=run('moSubmitBacktest(fixtureRequest)');
        original.symbol='MUTATED';original.special_settings.SPECIAL2.time_filters.MAIN_ASIA.start='0000';
        assert.deepEqual(starts()[0].body,request);
        gate.resolve({job_id:job2,phase:'run'});await pending;await tick();assert.deepEqual(value('moBacktestPoll.request'),request);
        starts()[0].body.special_settings.SPECIAL2.enabled=false;
        assert.deepEqual(value('moBacktestPoll.request'),request);
    } else if(scenario==='recent_reconnect_keeps_setup') {
        recent=[{job_id:job1,phase:'run',active:true}];await nav('backtest');await tick();
        assertPage('backtest','setup');assert.equal(run('moState.job'),job1);
        await click('mo-bt-open-progress');assertPage('backtest','progress');
    } else if(['latest_reconnect_selection','late_start_after_reconnect'].includes(scenario)) {
        await nav('backtest');const older=deferred(),newer=deferred();
        handlers['mo/backtest/reconnect']=(_route,body)=>body.job_id===job1?older.promise:newer.promise;
        handlers['mo/backtest/status']=async route=>{
            assert(route.endsWith(job2),'An obsolete selection must not replace the polled job');
            return {job_id:job2,phase:'error',active:false,result_ready:true,message:'현재 선택한 B 작업'};
        };
        handlers['mo/backtest/result']=async route=>{
            assert(route.endsWith(job2));
            return {run_id:'selected-B-result',status:'ERROR',alerts_preview:[],warnings:[]};
        };
        let pendingOlder;
        if(scenario==='latest_reconnect_selection')
            pendingOlder=run('window.MosesBacktestJobs.reconnect("'+job1+'")');
        else {
            handlers['mo/backtest/start']=()=>older.promise;
            pendingOlder=click('mo-bt-start-button');assert.equal(starts().length,1);
        }
        const pendingNewer=run('window.MosesBacktestJobs.reconnect("'+job2+'")');
        newer.resolve({job_id:job2,phase:'run'});await pendingNewer;await tick();
        assert.equal(run('moState.job'),job2);assertPage('backtest','progress');
        assert.equal(get('mo-bt-progress-summary').textContent,'현재 선택한 B 작업');
        assert(get('mo-bt-result').textContent.includes('selected-B-result'));
        const displayedResult=get('mo-bt-result').textContent;
        older.resolve({job_id:job1,phase:'planning'});await pendingOlder;await tick();
        assert.equal(run('moState.job'),job2);assertPage('backtest','progress');
        assert.equal(get('mo-bt-progress-summary').textContent,'현재 선택한 B 작업');
        assert.equal(get('mo-bt-result').textContent,displayedResult);
        assert.equal(run('moState.resultShown'),true);assert.equal(run('moBacktestPoll.request'),null);
        assertRecovery({retry:false,result:true});
    } else throw Error('Unknown scenario: '+scenario);
    console.log('PASS '+scenario);
})().catch(error=>{console.error(error.stack||error);process.exitCode=1;});
'''


@pytest.mark.parametrize("scenario", [
    "terminal_error", "terminal_cancelled", "terminal_interrupted", "error_result_available",
    "result_fetch_failure", "failed_without_snapshot", "active_interruption",
    "backtest_options_delayed", "backtest_options_failed", "live_immediate",
    "latest_options_success", "latest_options_failure", "late_start_live", "late_start_setup",
    "retry_error", "retry_cancelled", "retry_interrupted", "late_retry_setup",
    "snapshot_is_deep_clone", "recent_reconnect_keeps_setup",
    "latest_reconnect_selection", "late_start_after_reconnect",
])
def test_actual_backtest_recovery_and_navigation(scenario):
    node = shutil.which("node")
    assert node, "Node.js is required by current web verification"
    result = subprocess.run([node, "-e", SCRIPT, str(ROOT / "Part3/web"), scenario],
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS " + scenario in result.stdout
