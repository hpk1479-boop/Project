"""CLI for validated AI Recipe preview/generation and existing project tools."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import time
sys.dont_write_bytecode=True
from lab import catalog,storage,integration

def main():
    p=argparse.ArgumentParser(description='PART3 전략연구소 CLI')
    commands=p.add_subparsers(dest='command',required=True)
    gen=commands.add_parser('generate',help='새 Test_SPECIALXXX.py 생성')
    gen.add_argument('--recipe',required=True)
    preview=commands.add_parser('preview',help='실제 생성 코드 출력')
    preview.add_argument('--recipe',required=True)
    commands.add_parser('list',help='생성된 전략 목록')
    conn=commands.add_parser('connect',help='기존 프로젝트 연결')
    conn.add_argument('project_root');conn.add_argument('--python',default='');conn.add_argument('--warehouse',default='')
    bt=commands.add_parser('backtest',help='선택 Test 전략의 PART2 데이터 계획/백테스트')
    bt.add_argument('filename');bt.add_argument('--start',required=True);bt.add_argument('--end',required=True)
    bt.add_argument('--symbol',default='XAUUSD+');bt.add_argument('--warehouse',required=True)
    bt.add_argument('--mode',choices=('BAR','EVENT','TICK'),default='TICK')
    bt.add_argument('--action',choices=('plan','run'),default='plan')
    bt.add_argument('--cores',type=int,default=1)
    bt.add_argument('--yes',action='store_true',help='실제로 표시된 데이터 구축 계획 승인')
    job=commands.add_parser('job',help='공통 백테스트 실행 기록 조회·승인·정지')
    job.add_argument('action',choices=('status','confirm','stop'))
    job.add_argument('job_id')
    job.add_argument('--warehouse',default='')
    args=p.parse_args()
    if args.command in ('generate','preview'):
        path=Path(args.recipe);data=json.loads(path.read_text('utf-8-sig'));r=data.get('recipe',data)
        if args.command=='preview':print(storage.preview(r)['code'])
        else:
            result=storage.generate(r);print(result['path'])
    elif args.command=='list':print(json.dumps(storage.recent(),ensure_ascii=False,indent=2))
    elif args.command=='connect':
        storage.save_connections({'project_root':args.project_root,'python_executable':args.python,'warehouse':args.warehouse})
        print('연결 설정을 저장했습니다.')
    elif args.command=='backtest':
        from lab import backtest_jobs
        data=vars(args);result=integration.backtest(data)
        print(result['message']+' · 실행 ID '+result['job_id'],flush=True)
        context={'warehouse':Path(args.warehouse).expanduser().resolve(),
                 'project_root':Path(storage.project_path()),
                 'python_executable':storage.connections().get('python_executable') or sys.executable}
        previous=''
        while True:
            info=integration.job_status(result['job_id'],**context)
            text=info['log']
            if text.startswith(previous):fresh=text[len(previous):]
            else:
                marker=previous[-2048:]
                overlap=text.rfind(marker) if marker else -1
                fresh=text[overlap+len(marker):] if overlap>=0 else '\n'+text
            print(fresh,end='',flush=True);previous=text
            if info['phase']=='confirm':
                print(json.dumps(info['plan'],ensure_ascii=False,indent=2),flush=True)
                approved=args.yes
                if not approved and sys.stdin.isatty():
                    approved=input('표시된 데이터 구축 계획으로 실행하시겠습니까? [y/N] ').strip().lower()=='y'
                if not approved:
                    print('승인 대기 중입니다. job confirm '+result['job_id']+' 명령으로 이어서 실행할 수 있습니다.',flush=True)
                    return 0
                backtest_jobs.confirm(result['job_id'],plan_revision=info.get('plan_revision'),**context)
            elif not info['running']:
                return info['returncode'] or 0
            time.sleep(1)
    elif args.command=='job':
        from lab import backtest_jobs
        context={'warehouse':Path(args.warehouse).expanduser().resolve()} if args.warehouse else {}
        if args.action=='status':result=backtest_jobs.reconnect(args.job_id,**context)
        elif args.action=='stop':result=backtest_jobs.stop(args.job_id,**context)
        else:
            shown=backtest_jobs.status(args.job_id,**context)
            print(json.dumps(shown.get('plan'),ensure_ascii=False,indent=2),flush=True)
            result=backtest_jobs.confirm(args.job_id,plan_revision=shown.get('plan_revision'),**context)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0
if __name__=='__main__':
    try:raise SystemExit(main())
    except (ValueError,OSError,KeyError,SyntaxError) as e:print('오류: '+str(e),file=sys.stderr);raise SystemExit(1)
