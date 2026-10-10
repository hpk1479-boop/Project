"""PART3 → 기존 PART1/PART2 방향의 명시적 실행 연결.

기존 두 파트의 파일을 패치/덮어쓰기하지 않습니다. 원본 애플리케이션 실행은
사용자 버튼으로만 시작합니다. 생성 버튼은 데이터 서버나 백테스트를 시작하지 않습니다.
"""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import sys
from . import storage

def _python():return storage.connections().get('python_executable') or sys.executable

def _root()->Path:
    value=storage.project_path()
    if not value:raise ValueError('연결 설정에서 Part1/Part2 공통 상위 폴더를 먼저 지정하세요.')
    return Path(value)

def open_folder():
    path=str(storage.test_folder(for_write=True))
    if os.name=='nt':os.startfile(path)
    elif sys.platform=='darwin':subprocess.Popen(['open',path])
    else:subprocess.Popen(['xdg-open',path],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

def warehouse(data:dict,root=None)->Path:
    """The warehouse a generated backtest uses: the given one, else the connected one (relative to the project)."""
    value=str(data.get('warehouse') or storage.connections().get('warehouse') or '').strip()
    if not value:raise ValueError('기존 PART2 데이터 창고 경로를 입력하세요.')
    return storage.resolve_warehouse(value,root if root is not None else _root())

def backtest(data:dict)->dict:
    """Start a generated strategy through the same durable job as native runs."""
    if not isinstance(data, dict):
        raise ValueError('백테스트 입력을 확인하세요.')
    from . import backtest_adapters, backtest_jobs, unified_backtest
    available_only=unified_backtest._available_only(data)
    root=_root()
    watch_text=str(data.get('watch_text') or '').strip()
    request={**data,'symbol':data.get('symbol') or 'XAUUSD+','mode':data.get('mode') or 'TICK'}
    store=warehouse(data,root)
    action=data.get('action','plan')
    if action not in ('plan','run'):raise ValueError('plan 또는 run을 선택하세요.')
    if watch_text:
        scenario=unified_backtest._scenario({**request,'target_mode':'WATCH','watch_text':watch_text,'specials':[]})
        kind,adapter='normal',{}
    else:
        scenario,adapter=backtest_adapters.prepare_generated(request,root)
        kind='generated'
    result=backtest_jobs.start({'project_root':root,'warehouse':store,'python_executable':_python(),
        'kind':kind,'scenario':scenario,'adapter':adapter,'rebuild':bool(data.get('rebuild')),
        'plan_only':action=='plan'})
    return {**result,'message':'보유 데이터 사용 구간 확인을 시작했습니다.' if available_only else
            '데이터 구축 계획 확인을 시작했습니다.' if action=='plan' else
            '공통 백테스트를 시작했습니다. 데이터가 부족하면 구축 계획 확인 후 실행합니다.'}

def job_status(job:str, **context):
    """Legacy transport shape backed by the common recoverable job and log."""
    from . import backtest_jobs
    info=backtest_jobs.status(job,**context)
    text=backtest_jobs.log(job,**context)['text']
    running=info['phase'] in backtest_jobs.ACTIVE_PHASES and info.get('active',False)
    code=None if running else 1 if info['phase'] in ('error','interrupted') else 0
    return {**info,'running':running,'returncode':code,'log':text,
            'action':'plan' if info['phase'] in ('planning','plan','planned','confirm') else 'run'}
