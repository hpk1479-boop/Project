"""Cancellation regressions migrated from server threads to the shared supervisor.
No user engine or data is used; durable requests and Popen publication are real.
"""
from __future__ import annotations

import threading
from unittest.mock import Mock
import pytest
from verification.test_backtest_jobs67 import make_job, record, Process, Projection, join
from lab import backtest_jobs as jobs, backtest_supervisor as backtest


@pytest.fixture
def job(tmp_path, monkeypatch):
    return make_job(tmp_path, monkeypatch)


def worker(owner, action):
    owner.execute_plan() if action == 'plan' else owner.execute_run('approved-token')


@pytest.mark.parametrize('phase,action', [('planning','plan'), ('starting','run')])
def test_stop_before_worker_start_prevents_any_process(job, monkeypatch, phase, action):
    owner, context = job
    owner.update(phase=phase)
    launches=[]
    monkeypatch.setattr(backtest.subprocess,'Popen',lambda *a,**k: launches.append(a))
    assert jobs.stop(owner.id,**context)['ok']
    worker(owner,action)
    assert record(owner)['phase']=='cancelled' and not launches
    assert not (owner.folder/'stop.request').exists()


@pytest.mark.parametrize('action',['plan','run'])
def test_cancelled_job_cannot_launch_through_direct_transport(job,monkeypatch,action):
    owner,context=job
    launches=[]
    monkeypatch.setattr(backtest.subprocess,'Popen',lambda *a,**k: launches.append(a))
    jobs.stop(owner.id,**context)
    assert owner.execute_action(action) is None
    assert not launches and record(owner)['phase']=='cancelled'


@pytest.mark.parametrize('action',['plan','run'])
def test_stop_during_preparation_blocks_later_popen(job,monkeypatch,action):
    owner,context=job
    owner.update(phase='planning' if action=='plan' else 'starting')
    preparing,proceed=threading.Event(),threading.Event()
    launches=[]
    def prepare(*a,**k):
        preparing.set();assert proceed.wait(5)
        return ['mock',action],{},context['project_root']
    monkeypatch.setattr(jobs,'command',prepare)
    monkeypatch.setattr(backtest.subprocess,'Popen',lambda *a,**k: launches.append(a))
    thread=threading.Thread(target=worker,args=(owner,action),daemon=True);thread.start()
    try:
        assert preparing.wait(5)
        assert jobs.stop(owner.id,**context)['ok']
    finally:
        proceed.set();join(thread)
    assert record(owner)['phase']=='cancelled' and not launches


@pytest.mark.parametrize('action',['plan','run'])
def test_stop_during_popen_sees_published_process_and_stops_it(job,monkeypatch,action):
    owner,context=job
    owner.update(phase='planning' if action=='plan' else 'starting')
    entered,publish,finish=threading.Event(),threading.Event(),threading.Event()
    attempted,returned=threading.Event(),threading.Event()
    process=Process(finish,{'status':'CANCELLED'} if action=='run' else None,code=0 if action=='run' else 1)
    responses=[];launches=[]
    def popen(*a,**k):
        launches.append(a[0]);entered.set();assert publish.wait(5);return process
    def stop():
        attempted.set();responses.append(jobs.stop(owner.id,**context));returned.set()
    monkeypatch.setattr(backtest.subprocess,'Popen',popen)
    thread=threading.Thread(target=worker,args=(owner,action),daemon=True)
    stopper=threading.Thread(target=stop,daemon=True);thread.start()
    try:
        assert entered.wait(5)
        local=jobs._LOCAL_LOCKS[str(owner.folder.resolve()).casefold()]
        acquired=local.acquire(blocking=False)
        if acquired:local.release()
        assert not acquired
        stopper.start();assert attempted.wait(5);assert not returned.is_set()
        publish.set();assert returned.wait(5)
        # 수정본111: the first stop asks for a normal stop with partial save and says so.
        assert responses==[{'ok':True,'message':'종료 중입니다. 진행 내용을 저장하고 있습니다.'}]
        if action=='run':
            assert (owner.folder/'stop.request').read_text('ascii')=='STOP\n'
            assert not process.terminated.is_set()
        else:
            assert process.terminated.wait(5)
            assert not (owner.folder/'stop.request').exists()
    finally:
        publish.set();finish.set();join(thread)
        if stopper.ident is not None:join(stopper)
    assert launches==[['mock',action]] and record(owner)['process'] is None
    assert record(owner)['phase']=='cancelled'


def test_stop_between_plan_and_run_prevents_run_launch(job,monkeypatch):
    owner,context=job
    owner.update(phase='planning')
    between,proceed=threading.Event(),threading.Event();actions=[]
    original=owner.execute_run
    def planned(action,approved=None):
        actions.append(action);owner.last_result={'record':[]};return 0
    def pending(approved):
        between.set();assert proceed.wait(5);original(approved)
    monkeypatch.setattr(owner,'execute_action',planned)
    monkeypatch.setattr(owner,'execute_run',pending)
    thread=threading.Thread(target=owner.execute_plan,daemon=True);thread.start()
    try:
        assert between.wait(5);jobs.stop(owner.id,**context)
    finally:
        proceed.set();join(thread)
    assert actions==['plan'] and record(owner)['phase']=='cancelled'


def test_confirmed_job_stopped_before_scheduled_worker_cannot_restart(job,monkeypatch):
    owner,context=job
    owner.update(phase='confirm',plan={'approval_token':'approved-token'})
    scheduled=[];launches=[]
    monkeypatch.setattr(jobs,'_launch',lambda *args: scheduled.append(args[-1]))
    monkeypatch.setattr(backtest.subprocess,'Popen',lambda *a,**k: launches.append(a))
    assert jobs.confirm(owner.id,**context)['ok'] and scheduled==['run']
    assert record(owner)['phase']=='starting'
    jobs.stop(owner.id,**context);owner.execute_run('approved-token')
    assert record(owner)['phase']=='cancelled' and not launches
    with pytest.raises(ValueError,match='확인 대기'):jobs.confirm(owner.id,**context)


def test_stop_while_waiting_for_confirmation_cannot_confirm_or_launch(job,monkeypatch):
    owner,context=job
    owner.update(phase='confirm',plan={'approval_token':'approved-token'})
    launches=[]
    monkeypatch.setattr(backtest.subprocess,'Popen',lambda *a,**k: launches.append(a))
    jobs.stop(owner.id,**context)
    with pytest.raises(ValueError,match='확인 대기'):jobs.confirm(owner.id,**context)
    owner.execute_run('approved-token')
    assert record(owner)['phase']=='cancelled' and not launches


@pytest.mark.parametrize('action',['plan','run'])
def test_cancelled_preparation_exception_does_not_replace_cancelled_phase(job,monkeypatch,action):
    owner,context=job
    entered,proceed=threading.Event(),threading.Event()
    def broken(*a,**k):
        entered.set();assert proceed.wait(5);raise RuntimeError('preparation failure')
    monkeypatch.setattr(jobs,'command',broken)
    thread=threading.Thread(target=worker,args=(owner,action),daemon=True);thread.start()
    try:
        assert entered.wait(5);jobs.stop(owner.id,**context)
    finally:
        proceed.set();join(thread)
    assert record(owner)['phase']=='cancelled' and record(owner)['message']==''


def test_completed_engine_result_is_not_falsely_marked_cancelled(job,monkeypatch):
    owner,context=job
    reading,finish=threading.Event(),threading.Event();process=Process(finish,{'status':'COMPLETE'})
    def lines():
        reading.set();yield from process.lines()
    process.stdout=lines()
    monkeypatch.setattr(backtest.subprocess,'Popen',lambda *a,**k:process)
    thread=threading.Thread(target=owner.execute_run,args=(None,),daemon=True);thread.start()
    try:
        assert reading.wait(5);jobs.stop(owner.id,**context)
        assert (owner.folder/'stop.request').is_file()
    finally:
        finish.set();join(thread)
    assert owner.last_result['status']=='COMPLETE' and record(owner)['phase']=='complete'


def test_launch_failure_keeps_error_diagnostic_and_does_not_register_process(job,monkeypatch):
    owner,_context=job
    def fail(*a,**k):raise OSError('mock launch failure')
    monkeypatch.setattr(backtest.subprocess,'Popen',fail)
    owner.execute_run(None)
    assert record(owner)['phase']=='error' and record(owner)['message']=='mock launch failure'
    assert record(owner)['process'] is None
