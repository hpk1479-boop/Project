"""CLI for validated AI Recipe preview/generation and existing project tools."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import time
sys.dont_write_bytecode=True
from lab import catalog,storage,integration

def _virtual_entry(args):
    """The virtual entry policy asked for: a policy file, or the recipe's own on another base frame; None uses the recipe's."""
    if not args.virtual_entry and not args.base_frame:return None
    if args.result_mode!='VIRTUAL_ENTRY':raise ValueError('가상진입 설정은 --result-mode VIRTUAL_ENTRY에서만 씁니다.')
    if args.virtual_entry and args.base_frame:
        raise ValueError('--virtual-entry와 --base-frame은 함께 쓸 수 없습니다. 정책 파일에서는 tf가 기준 프레임입니다.')
    if args.base_frame:
        from lab import backtest_adapters
        return backtest_adapters.base_frame_policy(args.filename,args.base_frame,storage.project_path())
    policy=json.loads(Path(args.virtual_entry).read_text('utf-8-sig'))
    if not isinstance(policy,dict):raise ValueError('가상진입 정책 파일은 JSON 객체 하나여야 합니다.')
    return policy

def _follow(job_id,context,approve=False,done_text=None):
    """Print a common job's log until it ends; a recording plan waits for approval unless approve."""
    from lab import backtest_jobs
    previous=''
    while True:
        info=integration.job_status(job_id,**context)
        text=info['log']
        if text.startswith(previous):fresh=text[len(previous):]
        else:
            marker=previous[-2048:]
            overlap=text.rfind(marker) if marker else -1
            fresh=text[overlap+len(marker):] if overlap>=0 else '\n'+text
        print(fresh,end='',flush=True);previous=text
        if info['phase']=='confirm':
            print(json.dumps(info['plan'],ensure_ascii=False,indent=2),flush=True)
            approved=approve
            if not approved and sys.stdin.isatty():
                approved=input('표시된 데이터 구축 계획으로 실행하시겠습니까? [y/N] ').strip().lower()=='y'
            if not approved:
                print('승인 대기 중입니다. job confirm '+job_id+' 명령으로 이어서 실행할 수 있습니다.',flush=True)
                return 0
            backtest_jobs.confirm(job_id,plan_revision=info.get('plan_revision'),**context)
        elif not info['running']:
            if info['phase']=='planned':print(json.dumps(info.get('plan'),ensure_ascii=False,indent=2),flush=True)
            elif info['phase']=='complete':print(done_text or '결과 폴더(창고 기준): runs/'+job_id,flush=True)
            elif info.get('message'):print(info['message'],flush=True)
            return info['returncode'] or 0
        time.sleep(1)

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
    bt.add_argument('--symbol',default='XAUUSD+')
    bt.add_argument('--warehouse',default='',help='생략하면 연결 설정의 창고 (상대 경로는 프로젝트 폴더 기준)')
    bt.add_argument('--mode',choices=('BAR','EVENT','TICK'),default='TICK')
    bt.add_argument('--action',choices=('plan','run'),default='plan')
    bt.add_argument('--cores',type=int,default=None,help='생략하면 화면과 같은 작업 프로세스 수 (설정값 또는 이 PC 최적값)')
    bt.add_argument('--result-mode',choices=('ALERT_ONLY','VIRTUAL_ENTRY'),default='ALERT_ONLY')
    bt.add_argument('--virtual-entry',default='',help='가상진입 정책 JSON 파일 (공통 가상진입 계약)')
    bt.add_argument('--base-frame',default='',help='기준 프레임: 레시피를 이 시간봉으로 쓴 값으로 전략과 가상진입을 함께 옮김')
    bt.add_argument('--spread-points',type=float,default=0.0)
    bt.add_argument('--commission',type=float,default=0.0,help='수수료: 1랏 왕복 달러 (설정의 종목 1랏 크기 필요)')
    bt.add_argument('--available-only',action='store_true',help='요청 기간 안의 보유 데이터만 사용 (데이터 구축 안 함)')
    bt.add_argument('--yes',action='store_true',help='실제로 표시된 데이터 구축 계획 승인')
    lanes=commands.add_parser('lanes',help='여러 전략 × 트리거를 계획 파일 하나로 함께 백테스트 (레인)')
    lanes.add_argument('action',choices=('plan','run','status','stop','report'),
                       help='plan 미리보기(실행 안 함) / run 시작(끝난 실행은 건너뜀) / status 진행 / stop 정지 / report 결과 표')
    lanes.add_argument('plan_file',help='계획 JSON 파일 (예: AI작업\\금_스페셜1_2시간.json)')
    lanes.add_argument('--warehouse',default='',help='생략하면 연결 설정의 창고 (상대 경로는 프로젝트 폴더 기준)')
    job=commands.add_parser('job',help='공통 백테스트 실행 기록 조회·승인·정지')
    job.add_argument('action',choices=('status','confirm','stop'))
    job.add_argument('job_id')
    job.add_argument('--warehouse',default='',help='생략하면 연결 설정의 창고 (상대 경로는 프로젝트 폴더 기준)')
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
        data={key:value for key,value in vars(args).items() if key not in ('command','virtual_entry','base_frame')}
        if data['cores'] is None:del data['cores']          # as the screen: the saved setting decides
        # The run and its status read the same warehouse.
        store=integration.warehouse(data);data['warehouse']=str(store)
        policy=_virtual_entry(args)
        if policy is not None:data['virtual_entry']=policy
        result=integration.backtest(data)
        print(result['message']+' · 실행 ID '+result['job_id'],flush=True)
        context={'warehouse':store,
                 'project_root':Path(storage.project_path()),
                 'python_executable':storage.connections().get('python_executable') or sys.executable}
        return _follow(result['job_id'],context,args.yes)
    elif args.command=='lanes':
        from lab import lanes as planned
        where=args.warehouse or None
        if args.action=='plan':print(json.dumps(planned.preview(args.plan_file,warehouse=where),ensure_ascii=False,indent=2))
        elif args.action=='status':print(json.dumps(planned.status(args.plan_file,warehouse=where),ensure_ascii=False,indent=2))
        elif args.action=='stop':print(json.dumps(planned.stop(args.plan_file,warehouse=where),ensure_ascii=False,indent=2))
        elif args.action=='report':print(planned.report(args.plan_file,warehouse=where),end='')
        else:
            result=planned.start(args.plan_file,warehouse=where)
            print(result['message']+(' · 실행 ID '+result['job_id'] if result['job_id'] else ''),flush=True)
            if not result['job_id']:return 0
            context=planned._context(where)
            # A recording to build is never approved for the caller: a terminal asks its user, any other caller waits.
            return _follow(result['job_id'],context,False,
                           '끝났습니다. lanes report '+args.plan_file+' 로 결과를 보세요.')
    elif args.command=='job':
        from lab import backtest_jobs
        context={'warehouse':integration.warehouse({'warehouse':args.warehouse})} if args.warehouse else {}
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
