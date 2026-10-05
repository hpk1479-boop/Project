import argparse,json
from pathlib import Path
from .settings import ROOT,settings,scenario,warehouse_path,relative_path

def main():
    p=argparse.ArgumentParser(description='선택 전략 백테스트: 없는 데이터 자동 구축 (UTC, 종료일 미포함)')
    p.add_argument('action',choices=('plan','run','missing','compare','strategies'))
    for key in ('scenario','settings','symbol','start','end','profile','left','right','output','warehouse','strategies','approved-token'):p.add_argument('--'+key)
    p.add_argument('--mode',choices=('BAR','TIMER','EVENT','TICK'));p.add_argument('--cores',type=int)
    p.add_argument('--overlap',type=int);p.add_argument('--sequential',action='store_true')
    p.add_argument('--work-size',choices=('MONTH','FORTNIGHT'))
    p.add_argument('--capture-start',choices=('keyframe','beginning'))
    p.add_argument('--oz-evaluation',choices=('selected','all'))
    p.add_argument('--yes',action='store_true',help='표시된 데이터 구축 계획 승인')
    p.add_argument('--rebuild',action='store_true',help='기간 전체 재녹화; 검증 성공 후 교체')
    a=p.parse_args();cfg=settings(a.settings)
    if a.warehouse:cfg['warehouse']=str(Path(a.warehouse).expanduser().resolve())
    s=scenario(a.scenario,defaults={k:cfg[k] for k in ('cores','work_size','capture_start','oz_evaluation','overlap_trading_days')},
               symbol=a.symbol,start=a.start,end=a.end,mode=a.mode,cores=a.cores,strategies=a.strategies,
               work_size=a.work_size,capture_start=a.capture_start,oz_evaluation=a.oz_evaluation,
               overlap_trading_days=a.overlap)
    def emit(kind,data):print(json.dumps({'event':kind,**data},ensure_ascii=False),flush=True)
    try:
        if a.action in ('plan','missing'):
            from .workflow import proposal
            data=proposal(s,cfg['warehouse'],rebuild=a.rebuild)
        elif a.action=='run':
            from .workflow import execute
            profile=json.loads(Path(a.profile).read_text('utf-8-sig')) if a.profile else None
            data=execute(s,cfg['warehouse'],yes=a.yes,approved_token=a.approved_token,rebuild=a.rebuild,
                         profile=profile,cores=a.cores,sequential=a.sequential,emit=emit)
        elif a.action=='strategies':
            from .runner import runtime_config
            config=runtime_config(s)
            from event_selection import available
            data={'strategies':available(config)}
        else:
            from .warehouse import Warehouse
            if not a.left or not a.right:raise ValueError('--left / --right 실행 ID 필요')
            output=warehouse_path(cfg['warehouse'],a.output or 'comparison.csv');output.parent.mkdir(parents=True,exist_ok=True)
            w=Warehouse(cfg['warehouse'],results=True)
            try:count=w.compare(a.left,a.right,output)
            finally:w.close()
            data={'differences':count,'path':relative_path(cfg['warehouse'],output)}
    except Exception as exc:
        from .build_plan import ConfirmationRequired
        from .history_check import HistoryMissing
        if isinstance(exc,ConfirmationRequired):emit('CONFIRMATION_REQUIRED',exc.plan);return 2
        if isinstance(exc,HistoryMissing):emit('HISTORY_MISSING',{'gaps':exc.gaps,'backtest_started':False});return 3
        raise
    if a.output and a.action!='compare':
        output=warehouse_path(ROOT,a.output);output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    emit('COMPLETE',{'result':data});return 0

if __name__=='__main__':raise SystemExit(main())
