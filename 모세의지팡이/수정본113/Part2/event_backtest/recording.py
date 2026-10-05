"""Reuse native_mt5's compiler/config/terminal lifecycle, retain only MSP3 input."""
from pathlib import Path
import datetime as dt
import gzip
import json
import os
import re
import shutil
import time
import uuid
from contextlib import contextmanager
from generic_backtest import native_mt5 as native
from .settings import ROOT,PROGRAM,digest,file_hash,milliseconds,fragments,overlap_start,relative_path,warehouse_path
from .warehouse import Warehouse
from .capture_layout import capture_parent,check_destination

def source_hash():
    return digest({p.name:file_hash(p) for p in sorted((PROGRAM/'MT5').iterdir()) if p.suffix in ('.mq5','.mqh')})

def selected_profile(profile):
    if profile is not None:return profile
    from generic_backtest.history.selection import discover_terminals
    profiles=discover_terminals()['terminals']
    if not profiles:raise ValueError('MT5를 실행해 주세요.')
    if len(profiles)!=1:raise ValueError('MT5가 여럿이면 --profile로 하나를 지정하세요.')
    return profiles[0]

def recover_without_recording(profile,warehouse,*,emit=lambda *a:None,cancel=lambda:None):
    """A completed capture may be reused even though its terminal restore failed."""
    from . import deployment_recovery
    from .terminal_lifecycle import wait_closed
    directory=Path(warehouse)/'build_runs'
    if not any(directory.glob('*/'+deployment_recovery.JOURNAL)):return
    profile=selected_profile(profile)
    if not deployment_recovery.pending_for(profile,directory):return
    native._stop_selected_terminal(profile,emit=emit,cancel=cancel)
    wait_closed(profile,emit=emit,cancel=lambda:None)
    deployment_recovery.recover_pending(profile,directory,deployment_targets(profile),emit=emit)
    refreshed=native._restart_selected_terminal(profile,emit=emit,cancel=lambda:None)
    profile.update(refreshed)

def deployment_targets(profile):
    root=Path(profile['data_root'])/'MQL5'
    result=[]
    for name in native.NATIVE_MQL_SOURCES:
        p=root/('Experts' if name==native.NATIVE_MQL_SOURCES[-1] else 'Indicators')/name
        result.extend([p,p.with_suffix('.ex5')])
    result.extend(root/'Experts'/name for name in ('STAFF_Identity_Status.mqh','STAFF_Wire_Schema.mqh','STAFF_Wire_V2.mqh','STAFF_Symbol_Map.mqh'))
    return result

@contextmanager
def installed_build(profile,root,emit,cancel,*,build_root=None):
    """Restore the user's installed EA/indicators before reopening their terminal."""
    from . import deployment_recovery
    from .terminal_lifecycle import wait_closed
    root=Path(root)
    recovery_root=Path(build_root).parent if build_root is not None else root.parent
    warehouse_path(recovery_root,root.relative_to(recovery_root).as_posix())
    root.mkdir(parents=True,exist_ok=True)
    targets=deployment_targets(profile);journal=None;safe_to_restart=False
    # Stop and verify closure before reading originals or applying pending restore.
    native._stop_selected_terminal(profile,emit=emit,cancel=cancel)
    try:
        wait_closed(profile,emit=emit,cancel=lambda:None)
        deployment_recovery.recover_pending(profile,root.parent,targets,emit=emit)
        safe_to_restart=True
        journal=deployment_recovery.backup(profile,root,targets)
        safe_to_restart=False
        build=Path(build_root or root.parent/'builds')/source_hash();build.mkdir(parents=True,exist_ok=True)
        ready=build/'ready.json'
        if ready.exists():
            metadata=json.loads(ready.read_text('utf-8'))
            if any(file_hash(build/name)!=sha for name,sha in metadata['files'].items()):raise ValueError('compiled cache hash mismatch')
            for p in targets:
                source=build/p.name
                if source.exists():p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,p)
        else:
            native.ensure_native_mql_current(profile,root/'deploy',emit=emit,cancel=cancel)
            for p in targets:
                if p.exists():shutil.copy2(p,build/p.name)
            metadata={'files':{p.name:file_hash(p) for p in build.iterdir() if p.is_file()}}
            ready.write_text(json.dumps(metadata,indent=2),encoding='utf-8')
        ea_hash=file_hash(build/'THE_STAFF_OF_MOSES.ex5')
        yield ea_hash
    finally:
        if journal is not None:
            # A timeout leaves the mapping and originals available for next entry.
            wait_closed(profile,emit=emit,cancel=lambda:None)
            deployment_recovery.restore(profile,journal,targets)
            safe_to_restart=True
        if safe_to_restart:
            # Cancellation must not prevent restoration.
            refreshed=native._restart_selected_terminal(profile,emit=emit,cancel=lambda:None)
            profile.update(refreshed)

def journal_positions(profile):
    roots=[Path(profile['data_root'])/'Tester',Path(os.environ.get('APPDATA',''))/'MetaQuotes/Tester']
    return {str(p):p.stat().st_size for r in roots if r.exists() for p in r.rglob('*.log')}

def tick_evidence(profile,before,out):
    lines=[]
    for path,size in journal_positions(profile).items():
        offset=before.get(path,0)
        if size<=offset:continue
        with open(path,'rb') as f:f.seek(offset);raw=f.read()
        text=raw.decode('utf-16-le',errors='replace') if b'\x00' in raw[:200] else raw.decode('utf-8',errors='replace')
        lines.extend(line for line in text.splitlines() if re.search(r'tick|틱|model|history|no data|not enough|이력|minute bars|no bars|\bM1\b|1분봉',line,re.I))
    (out/'tester_tick_evidence.log').write_text('\n'.join(lines),encoding='utf-8')
    mixed=any(re.search(r'(real ticks.*(absent|discard|unavailable|not available)|generat(ed|ing) ticks)',s,re.I) for s in lines)
    real=any(re.search(r'(based on real ticks|real ticks.*(used|begin|synchron|download))',s,re.I) for s in lines)
    actual='MIXED_OR_GENERATED' if mixed else 'REAL_TICKS' if real else 'UNCONFIRMED'
    return {'journal_lines':lines,'requested_model':4,'requested_label':'실제 틱 기반 모든 틱','actual':actual,
            'warning':None if actual=='REAL_TICKS' else '실제 틱 사용을 완전히 확인하지 못했거나 생성 틱이 포함되었습니다. 저널을 확인하세요.'}

def retain_capture(source,destination,*,progress=lambda *a:None):
    """Lossless gzip container; decompressed records remain original MSP3 bytes."""
    source=Path(source).resolve();final=destination
    destination=final.with_name(final.name+'.partial_'+uuid.uuid4().hex)
    destination.mkdir(parents=True,exist_ok=False)
    lines=[];raw_bytes=0;done=0
    manifest=(source/'manifest.tsv').read_text('ascii').splitlines()
    total=sum(line.startswith('pipe_feed\t') for line in manifest)
    for line in manifest:
        row=line.split('\t')
        if row[0]=='pipe_feed':
            p=(source/row[3]).resolve()
            if p.parent!=source:raise ValueError('capture path escape')
            raw_bytes+=p.stat().st_size
            target=destination/(p.name+'.gz')
            with p.open('rb') as src,gzip.open(target,'wb',compresslevel=1) as dst:shutil.copyfileobj(src,dst,4*1024*1024)
            row[3]=target.name
            done+=1;progress(done,total)
        lines.append('\t'.join(row))
    (destination/'manifest.tsv').write_text('\n'.join(lines)+'\n',encoding='ascii')
    shutil.copy2(source/'complete.txt',destination/'complete.txt')
    if final.exists():
        # Preserve a corrupt/unfinished earlier attempt for diagnosis.
        final.rename(final.with_name(final.name+'.quarantine_'+uuid.uuid4().hex))
    destination.rename(final)
    return raw_bytes

def prepare(scenario,warehouse,profile=None,*,emit=lambda *a:None,cancel=lambda:None,
            plan=None,approved_token=None,rebuild=False):
    from .build_plan import make_plan,require_approval,current_build,ConfirmationRequired
    from .history_check import omissions
    from .storage import convert,files
    from .calendar import capture_calendar
    root=Path(warehouse);catalog=Warehouse(root)
    proposed=make_plan(scenario,catalog,rebuild=rebuild)
    emit('BUILD_PLAN',proposed)
    try:require_approval(proposed,approved_token)
    except BaseException:catalog.close();raise
    if plan is not None and proposed['approval_token']!=plan['approval_token']:
        catalog.close();raise ValueError('녹화 계획이 바뀌었습니다. 변경된 계획을 다시 확인하세요.')
    result=list(proposed['reuse'])
    try:
        cancel()
        if scenario.get('available_only'):return result
        if not proposed['record']:recover_without_recording(profile,root,emit=emit,cancel=cancel)
        destinations=set()
        for piece in [*proposed['record'],*proposed.get('convert',())]:
            symbol=piece.get('symbol',scenario['symbol']);mode=piece.get('mode',proposed['mode'])
            parent=capture_parent(root,symbol,mode,piece['start'])
            if parent not in destinations:
                check_destination(root,symbol,mode,piece['start']);destinations.add(parent)
        for previous in proposed.get('convert',()):
            from .settings import warehouse_path
            source=warehouse_path(root,previous['path']);emit('CONVERSION_START',{'start':previous['start'],'end':previous['end']})
            dest,stored=convert(source,capture_parent(root,previous['symbol'],previous['mode'],previous['start']),previous['capture_id'],cancel=cancel,emit=emit,
                start=previous['start'],end=previous['end'])
            journal=source/'tester_tick_evidence.log'
            lines=journal.read_text('utf-8').splitlines() if journal.is_file() else []
            gaps=omissions(lines,previous['start'],previous['end'],previous.get('tester_symbol',previous['symbol']))
            if journal.is_file():shutil.copy2(journal,dest/journal.name)
            data={**previous,**stored,'path':relative_path(root,dest),'history_missing':gaps,
                'files':files(dest),'stored_bytes':sum(p.stat().st_size for p in dest.iterdir() if p.is_file())}
            if not gaps:catalog.register(data)
            result.append(data);emit('CAPTURE_CONVERTED',data)
            # Existing MSP3/gzip belongs to the user and is always retained.
        if not proposed['record']:return result
        profile=selected_profile(profile)
        work=root/'build_runs'/uuid.uuid4().hex
        # Deploy the exact already-tested package if this machine lacks its cache.
        # No source or schema modification/recompilation is needed for revision24.
        cached=root/'builds'/source_hash();ready=cached/'ready.json'
        if not ready.exists():
            cached.mkdir(parents=True,exist_ok=True)
            for target in deployment_targets(profile):
                src=PROGRAM/'MT5'/target.name
                if not src.is_file():raise ValueError('compiled package incomplete: '+target.name)
                shutil.copy2(src,cached/target.name)
            ready.write_text(json.dumps({'files':files(cached)},indent=2),encoding='utf-8')
        with installed_build(profile,work,emit,cancel,build_root=root/'builds') as ea_hash:
            if ea_hash!=proposed['ea_build_hash']:raise ValueError('EA build changed after confirmation')
            from .terminal_lifecycle import launch_once_retry
            from .settings import tester_symbol
            tester=tester_symbol(scenario['symbol'])
            for piece_index,piece in enumerate(proposed['record'],1):
                cancel();start,end=piece['start'],piece['end']
                identity={**piece,'symbol':scenario['symbol'],'mode':proposed['mode'],
                    'ea_build_hash':ea_hash,'schema_id':proposed['schema_id'],
                    'timer_ms':scenario['timer_ms'],'tester_model':4}
                key=digest(identity);before=journal_positions(profile)
                emit('CAPTURE_START',{**piece,'index':piece_index,'total':len(proposed['record'])+len(proposed.get('convert',()))})
                try:
                    launch=launch_once_retry(lambda:native.run_native_tester(profile,tester,milliseconds(start)*10**6,milliseconds(end)*10**6,
                        work/key,emit=emit,cancel=cancel,shutdown_terminal=True,start_timeout=180,capture_only=True,logical_symbol=scenario['symbol'],
                        tester_inputs={'InpMode':1,'InpWireVersion':2,'InpRecordingMode':1 if proposed['mode']=='BAR' else 0,
                            'InpNativeExport':'false','InpPipeRecording':'true','STAFF_TIMER_MS':scenario['timer_ms'],
                            'InpTesterLogicalSymbol':scenario['symbol']}),profile,emit=emit,cancel=cancel)
                except native.NativeHistoryUnavailable as exc:
                    from .periods import adjustment
                    limit=exc.available_end
                    # Only a proven unavailable tail can shorten the request.
                    # An unknown range or an internal history gap remains an error.
                    if (not limit or start<limit or limit<=scenario['start']
                        or any(day>=limit for c in result for day in c.get('observed_days',()))):raise
                    change=adjustment(scenario,scenario['start'],limit,'BROKER_HISTORY_LIMIT')
                    change['message']+=f' MT5가 제공하는 마지막 이력까지 처리했습니다.'
                    excluded={'start':limit,'end':scenario['end'],'reason':'BROKER_HISTORY_LIMIT',
                              'message':'MT5가 보고한 마지막 이력 이후: 구축하지 않습니다.'}
                    proposed.update(period_adjustment=change,excluded_periods=[excluded])
                    if plan is not None:plan.update(period_adjustment=change,excluded_periods=[excluded])
                    emit('PERIOD_ADJUSTED',{'period_adjustment':change,'excluded_periods':[excluded],'message':change['message']})
                    break
                source=Path(launch['export']);calendar=capture_calendar(source)
                emit('CAPTURE_RECORDED',{**piece,'elapsed_seconds':launch['elapsed_seconds'],'raw_bytes':sum(p.stat().st_size for p in source.glob('pipe_*.bin'))})
                emit('CONVERSION_START',piece)
                dest,stored=convert(source,capture_parent(root,scenario['symbol'],proposed['mode'],start),key,cancel=cancel,emit=emit,start=start,end=end)
                ticks=tick_evidence(profile,before,dest)
                journal=ticks.pop('journal_lines')
                gaps=omissions(journal,start,end,tester)
                journal_warning=None if any(tester in line for line in journal) else '선택 종목의 테스터 저널 확인 자료 부족'
                data={**identity,**{k:v for k,v in launch.items() if k!='export'},**stored,**calendar,'tester_symbol':tester,
                    'capture_id':key,'path':relative_path(root,dest),'tick_evidence':ticks,'history_missing':gaps,
                    'history_check':'M1_ONLY','history_evidence_warning':journal_warning,
                    'files':files(dest),'stored_bytes':sum(p.stat().st_size for p in dest.iterdir() if p.is_file()),
                    'recorded_at':dt.datetime.now(dt.timezone.utc).isoformat()}
                if gaps:
                    # Preserve the candidate and its diagnosis but never supersede
                    # an earlier valid catalog generation with incomplete history.
                    (dest/'history_missing.json').write_text(json.dumps(gaps,ensure_ascii=False,indent=2),encoding='utf-8')
                    data['files']=files(dest)
                    data['stored_bytes']=sum(p.stat().st_size for p in dest.iterdir() if p.is_file())
                    (work/'history_missing.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
                else:catalog.register(data)
                result.append(data);emit('CAPTURE_COMPLETE',data)
                # Delete only this execution's UUID-scoped temporary MSP3 after
                # exact original/restored bundle hashes have passed.
                src=source.resolve();expected=(native.common_files_root()/'MosesDataBuild'/launch['session']).resolve()
                if src!=expected or not data['reconstruction_verified']:raise ValueError('retention verification failed')
                shutil.rmtree(src)
        # The observed calendar may reveal holidays only after recording finishes.
        # Present the newly expanded plan, with a new approval token, before any
        # additional MT5 work. Completed captures remain registered and reusable.
        if int(scenario['overlap_trading_days']):
            from .calendar import warm_start
            try:warm_start(scenario['start'],int(scenario['overlap_trading_days']),result)
            except ValueError:
                extended=make_plan(scenario,catalog,rebuild=rebuild)
                emit('BUILD_PLAN',extended)
                if extended['record']:raise ConfirmationRequired(extended)
                raise
    finally:catalog.close()
    return sorted(result,key=lambda c:c['start'])
