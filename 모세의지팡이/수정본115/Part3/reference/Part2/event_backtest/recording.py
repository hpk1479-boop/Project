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
from .settings import ROOT,PROGRAM,digest,file_hash,milliseconds,fragments,overlap_start,relative_path
from .warehouse import Warehouse

def source_hash():
    return digest({p.name:file_hash(p) for p in sorted((PROGRAM/'MT5').iterdir()) if p.suffix in ('.mq5','.mqh')})

def deployment_targets(profile):
    root=Path(profile['data_root'])/'MQL5'
    result=[]
    for name in native.NATIVE_MQL_SOURCES:
        p=root/('Experts' if name==native.NATIVE_MQL_SOURCES[-1] else 'Indicators')/name
        result.extend([p,p.with_suffix('.ex5')])
    result.extend(root/'Experts'/name for name in ('STAFF_Identity_Status.mqh','STAFF_Wire_Schema.mqh','STAFF_Wire_V2.mqh'))
    return result

@contextmanager
def installed_build(profile,root,emit,cancel,*,build_root=None):
    """Restore the user's installed EA/indicators before reopening their terminal."""
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    targets=deployment_targets(profile);backups={}
    for i,p in enumerate(targets):
        b=root/('original_'+str(i));backups[p]=b if p.exists() else None
        if p.exists():shutil.copy2(p,b)
    stopped=False
    try:
        native._stop_selected_terminal(profile,emit=emit,cancel=cancel);stopped=True
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
        for p,b in backups.items():
            if b is None:p.unlink(missing_ok=True)
            else:shutil.copy2(b,p)
        if stopped:
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
    from .build_plan import make_plan,require_approval,current_build
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
        for previous in proposed.get('convert',()):
            from .settings import warehouse_path
            source=warehouse_path(root,previous['path']);emit('CONVERSION_START',{'start':previous['start'],'end':previous['end']})
            dest,stored=convert(source,root/'captures',previous['capture_id'],cancel=cancel)
            journal=source/'tester_tick_evidence.log'
            lines=journal.read_text('utf-8').splitlines() if journal.is_file() else []
            gaps=omissions(lines,previous['start'],previous['end'],previous['symbol'])
            if journal.is_file():shutil.copy2(journal,dest/journal.name)
            data={**previous,**stored,'path':relative_path(root,dest),'history_missing':gaps,
                'files':files(dest),'stored_bytes':sum(p.stat().st_size for p in dest.iterdir() if p.is_file())}
            if not gaps:catalog.register(data)
            result.append(data);emit('CAPTURE_CONVERTED',data)
            # Existing MSP3/gzip belongs to the user and is always retained.
        if not proposed['record']:return result
        if profile is None:
            from generic_backtest.history.selection import discover_terminals
            profiles=discover_terminals()['terminals']
            if len(profiles)!=1:raise ValueError('MT5가 여럿이면 --profile로 하나를 지정하세요.')
            profile=profiles[0]
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
            for piece in proposed['record']:
                cancel();start,end=piece['start'],piece['end']
                identity={**piece,'symbol':scenario['symbol'],'mode':proposed['mode'],
                    'ea_build_hash':ea_hash,'schema_id':proposed['schema_id'],
                    'timer_ms':scenario['timer_ms'],'tester_model':4}
                key=digest(identity);before=journal_positions(profile)
                launch=native.run_native_tester(profile,scenario['symbol'],milliseconds(start)*10**6,milliseconds(end)*10**6,
                    work/key,emit=emit,cancel=cancel,shutdown_terminal=True,start_timeout=180,capture_only=True,
                    tester_inputs={'InpMode':1,'InpWireVersion':2,'InpRecordingMode':1 if proposed['mode']=='BAR' else 0,
                        'InpNativeExport':'false','InpPipeRecording':'true','STAFF_TIMER_MS':scenario['timer_ms']})
                source=Path(launch['export']);calendar=capture_calendar(source)
                emit('CONVERSION_START',piece)
                dest,stored=convert(source,root/'captures',key,cancel=cancel)
                ticks=tick_evidence(profile,before,dest)
                journal=ticks.pop('journal_lines')
                gaps=omissions(journal,start,end,scenario['symbol'])
                journal_warning=None if any(scenario['symbol'] in line for line in journal) else '선택 종목의 테스터 저널 확인 자료 부족'
                data={**identity,**{k:v for k,v in launch.items() if k!='export'},**stored,**calendar,
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
    finally:catalog.close()
    return sorted(result,key=lambda c:c['start'])
