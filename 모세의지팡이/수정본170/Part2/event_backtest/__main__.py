import argparse,json,uuid,re,traceback
from pathlib import Path
from .settings import ROOT,settings,scenario,warehouse_path,relative_path

def main():
    p=argparse.ArgumentParser(description='선택 전략 백테스트: 없는 데이터 자동 구축 (UTC, 종료일 미포함)')
    p.add_argument('action',choices=('plan','run','missing','compare','strategies','tune'))
    for key in ('scenario','settings','symbol','start','end','profile','left','right','output','warehouse','strategies','approved-token'):p.add_argument('--'+key)
    p.add_argument('--result-mode',choices=('ALERT_ONLY','VIRTUAL_ENTRY'))
    p.add_argument('--spread-points',type=float)
    p.add_argument('--build-only',action='store_true')
    p.add_argument('--session-id')
    p.add_argument('--mode',choices=('BAR','TIMER','EVENT','TICK'));p.add_argument('--cores',type=int)
    p.add_argument('--overlap',type=int);p.add_argument('--sequential',action='store_true')
    p.add_argument('--capture-start',choices=('keyframe','beginning'))
    p.add_argument('--oz-evaluation',choices=('selected','all'))
    p.add_argument('--yes',action='store_true',help='표시된 데이터 구축 계획 승인')
    p.add_argument('--rebuild',action='store_true',help='기간 전체 재녹화; 검증 성공 후 교체')
    p.add_argument('--skip-cleanup',action='store_true',help='창고 정리 생략 (Part3 작업: 작업을 만들 때 정리함)')
    a=p.parse_args();cfg=settings(a.settings)
    if a.warehouse:cfg['warehouse']=str(Path(a.warehouse).expanduser().resolve())
    s=scenario(a.scenario,defaults={k:cfg[k] for k in ('cores','capture_start','oz_evaluation','overlap_trading_days')},
               symbol=a.symbol,start=a.start,end=a.end,mode=a.mode,cores=a.cores,strategies=a.strategies,
               capture_start=a.capture_start,oz_evaluation=a.oz_evaluation,
               overlap_trading_days=a.overlap,result_mode=a.result_mode,build_only=a.build_only or None)
    if a.spread_points is not None:s['spread_points']={s['symbol']:a.spread_points}
    run_id=a.session_id or uuid.uuid4().hex
    if len(run_id)!=32 or any(c not in '0123456789abcdef' for c in run_id):raise ValueError('invalid run ID')
    s['_run_id']=run_id
    journal=None
    if a.action=='run':
        folder=warehouse_path(cfg['warehouse'],'runs/'+run_id);folder.mkdir(parents=True,exist_ok=True)
        journal=folder/'progress.jsonl'
    def clean(value):
        if isinstance(value,dict):return {k:clean(v) for k,v in value.items()}
        if isinstance(value,(list,tuple)):return [clean(v) for v in value]
        if isinstance(value,Path):value=str(value)
        if isinstance(value,str):
            value=value.replace(str(ROOT),'<project>').replace(cfg['warehouse'],'<warehouse>')
            return re.sub(r'[A-Za-z]:[\\/][^\s"\n]+',lambda m:Path(m[0]).name,value)
        return value
    def emit(kind,data):
        line=json.dumps(clean({'event':kind,**data}),ensure_ascii=False)
        if journal:
            with journal.open('a',encoding='utf-8') as log:log.write(line+'\n')
        print(line,flush=True)
    try:
        if a.action in ('plan','missing'):
            from .workflow import proposal
            # A plan reads the catalog only: the run hashes each recording once before replaying it (수정본170).
            data=proposal(s,cfg['warehouse'],rebuild=a.rebuild,verify=a.action=='missing',cleanup=not a.skip_cleanup)
        elif a.action=='run':
            from .workflow import execute
            from .cancellation import file_check
            profile=json.loads(Path(a.profile).read_text('utf-8-sig')) if a.profile else None
            data=execute(s,cfg['warehouse'],yes=a.yes,approved_token=a.approved_token,rebuild=a.rebuild,
                         profile=profile,cores=a.cores,sequential=a.sequential,emit=emit,cancel=file_check(folder/'stop.request'),
                         cleanup=not a.skip_cleanup)
        elif a.action=='tune':
            # This PC's fastest worker count, measured on the scenario's symbol and strategies (수정본167).
            from .worker_tuning import measure,save
            result=measure(cfg['warehouse'],symbol=s['symbol'],strategies=s['strategies'],triggers=s.get('triggers'),emit=emit)
            data={**result,'saved':save(result,Path(a.settings) if a.settings else None)}
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
        emit('ERROR',{'message':str(exc),'exception':traceback.format_exc(),'completed_pieces_preserved':True});return 1
    if a.output and a.action!='compare':
        output=warehouse_path(ROOT,a.output);output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    emit('COMPLETE',{'result':data});return 0

if __name__=='__main__':raise SystemExit(main())
