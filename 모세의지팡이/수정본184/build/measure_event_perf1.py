"""External measurement only; revision16/17 source trees are read-only."""
import sys,os,json,time,hashlib,logging,types,socket,importlib.util,cProfile,pstats,functools,inspect
from pathlib import Path
from collections import defaultdict
BASE=Path(r'C:\Users\hpk14\Desktop\개피곤');ROOT=BASE/'수정본18';TMP=ROOT/'검증결과/event_perf1/performance';TMP.mkdir(parents=True,exist_ok=True)
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
sys.path[:0]=[str(ROOT/'Part2'),str(ROOT/'Part1/program')]
def deny(*a,**k):raise AssertionError('measurement forbids network')
socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.socket.sendto=deny
from part1_host import runtime,engine as capture_engine
from staff_golden import scenario
import numpy as np
import ctypes
def system_cpu():
    idle=ctypes.c_ulonglong();kernel=ctypes.c_ulonglong();user=ctypes.c_ulonglong()
    if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle),ctypes.byref(kernel),ctypes.byref(user)):raise OSError('GetSystemTimes')
    return idle.value,kernel.value+user.value
def idle_sample():
    a=system_cpu();time.sleep(1);b=system_cpu();return 100*(1-(b[0]-a[0])/max(1,b[1]-a[1]))

def save(path,obj):path.write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
def hashes():
    result={}
    for rev in ('수정본17','수정본18'):
        for p in (BASE/rev).rglob('*'):
            if not p.is_file() or '__pycache__' in p.parts:continue
            rel=p.relative_to(BASE/rev)
            if (rel.parts[0] in ('Part1','tests','build') and p.suffix.lower() in ('.py','.mq5','.mqh','.bat','.ps1')) or p.name in ('config.txt','special_settings.json','command_aliases.json'):
                result[rev+'/'+rel.as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
    return result

class Categories:
    def __init__(self):self.active=False;self.label='other';self.last=0;self.times=defaultdict(int);self.calls=defaultdict(int);self.roots=[]
    def charge(self):
        now=time.perf_counter_ns()
        if self.active:self.times[self.label]+=now-self.last
        self.last=now
    def begin(self):self.label='other';self.last=time.perf_counter_ns();self.active=True
    def end(self):self.charge();self.active=False
    def wrap(self,fn,label,identity):
        self.roots.append((label,identity))
        @functools.wraps(fn)
        def measured(*args,**kwargs):
            if not self.active:return fn(*args,**kwargs)
            self.charge();old=self.label;self.label=label;self.calls[label]+=1
            try:return fn(*args,**kwargs)
            finally:self.charge();self.label=old
        return measured

def categories_install(cat):
    import pandas as pd
    from pandas.core.generic import NDFrame
    pd.DataFrame.__init__=cat.wrap(pd.DataFrame.__init__,'dataframe','pandas.DataFrame.__init__')
    if hasattr(pd.DataFrame,'_constructor_from_mgr'):
        pd.DataFrame._constructor_from_mgr=cat.wrap(pd.DataFrame._constructor_from_mgr,'dataframe','DataFrame._constructor_from_mgr')
    original=NDFrame.copy
    def copy(obj,*a,**kw):
        if isinstance(obj,pd.DataFrame):return wrapped(obj,*a,**kw)
        return original(obj,*a,**kw)
    wrapped=cat.wrap(original,'dataframe','NDFrame.copy(DataFrame only)');NDFrame.copy=copy
    decisions={'run_once','run_watch_once','run_spec_once','run_query','evaluate_once','evaluate','process','process_watch_result',
        'maintenance_tick','_handle_event','_evaluate_symbol_locked','_evaluate_spec_locked','poll','handle_oz_event'}
    replacements={}
    modules=[]
    for module in list(sys.modules.values()):
        file=getattr(module,'__file__',None)
        if not file or str(ROOT/'Part1/program') not in str(file):continue
        if module in modules:continue
        modules.append(module)
        stem=Path(file).stem
        for name,fn in list(vars(module).items()):
            if inspect.isfunction(fn) and fn.__module__==module.__name__:
                label=None
                if stem in ('indicator_facts','watch_ma_features') or (stem=='staff_compat' and name=='apply_requested_features') or (stem=='monitor_OZ' and name.startswith('add_')) or (stem=='strategy_FVG' and name=='add_fvg_features'):label='indicators'
                if label:replacements[fn]=cat.wrap(fn,label,stem+'.'+name)
            if inspect.isclass(fn) and fn.__module__==module.__name__:
                for key,value in list(vars(fn).items()):
                    if not inspect.isfunction(value):continue
                    label='indicators' if stem=='indicator_facts' and key in ('get','frame') else ('decisions' if key in decisions and stem in ('monitor_OZ','strategy_FVG','strategy_SWEEP','strategy_INDICATOR','manager_KIM','watch_orchestrator','SPECIAL4','SPECIAL5') else None)
                    if label:setattr(fn,key,cat.wrap(value,label,stem+'.'+fn.__name__+'.'+key))
    # Preserve direct-import aliases while observing the original functions.
    for module in modules:
        for name,value in list(vars(module).items()):
            if inspect.isfunction(value) and value in replacements:setattr(module,name,replacements[value])

def main(mode,case):
    assert mode in ('timed','profile') and case=='XAU', 'PERF1 scope: XAU only, no polling rerun'
    suffix='final_mt5_sigma3_v2' if case=='XAU' else 'btc_weekend_previous_sigma3_v2'
    folder=ROOT/'검증결과/staff_s7'/suffix;meta=json.loads((folder/'result.json').read_text('utf-8'))
    symbol=meta['symbol'];start=meta['start_s'];end=meta['end_s'];path=folder/Path(meta['retained_export']).name
    before=hashes();idle=[idle_sample() for _ in range(5)]
    if max(idle)>15:raise RuntimeError('System is busy; wait before the one measurement: '+str(idle))
    result={'mode':mode,'case':case,'symbol':symbol,'start':start,'end':end,'market_seconds':end-start,
        'idle_cpu_percent':idle,'python':sys.version,'logical_cpus':os.cpu_count(),
        'capture':str(path),'source_manifest_sha256':hashlib.sha256(json.dumps(before,sort_keys=True).encode()).hexdigest(),
        'pandas':__import__('pandas').__version__,'numpy':np.__version__}
    watches=[(chat,text if symbol=='XAUUSD+' else text.replace('골드',symbol)) for chat,text in scenario.WATCHES]
    logging.disable(logging.CRITICAL)
    if mode=='poll':
        rt=runtime.Part1Runtime(symbols=[symbol],start_epoch=start-1,part1_root=BASE/'수정본16/Part1',
            specials=[f'SPECIAL{i}' for i in range(1,8)],trigger_overrides={'SPECIAL7':'무지성 올존'},
            config_overrides={'STAFF_ALLOWED_SYMBOLS':symbol,'TARGET_SYMBOLS':symbol},work_dir=TMP,log_level=logging.CRITICAL)
        try:
            for chat,text in watches:rt.manager.handle_command(text,chat)
            rt.start_services();feed=capture_engine.SecondFeed(path,symbol)
            cpu=time.process_time();wall=time.perf_counter()
            for second in range(start,end):
                rt.clock.set(second);feed.advance_to(second)
                for tf,raw in feed.wires():rt.publish(raw)
                rt.run_until(second+.999)
                if (second-start+1)%60==0:print(mode,case,second-start+1,flush=True)
            result.update(cpu_seconds=time.process_time()-cpu,wall_seconds=time.perf_counter()-wall,notifications=len(rt.http.deliveries),service_periods=runtime.SERVICE_PERIODS)
        finally:rt.close()
    else:
        requests=types.ModuleType('requests');requests.Session=deny;requests.get=deny;requests.post=deny;sys.modules['requests']=requests
        os.environ.update(OZ_SPECIAL_SELECTION_ACTIVE='1',OZ_ENABLED_SPECIALS=','.join(f'SPECIAL{i}' for i in range(1,8)),OZ_SPECIAL_TRIGGERS=json.dumps({'SPECIAL7':'무지성 올존'},ensure_ascii=False))
        from event_application import create_event_engine
        from event_engine import Kind
        from event_engine.capture_io import capture_bundles
        from event_engine.staff_adapter import StaffIngressAdapter
        config=runtime.backtest_config(ROOT/'Part1',{'STAFF_ALLOWED_SYMBOLS':symbol,'TARGET_SYMBOLS':symbol})
        e=create_event_engine(config,symbols=(symbol,),collect_timings=True)
        spec=importlib.util.spec_from_file_location('perf_staff',ROOT/'Part1/program/THE STAFF OF MOSES.py');staff=importlib.util.module_from_spec(spec);spec.loader.exec_module(staff)
        clock=[start];cache=staff.StaffPipeCache('',health_session='E2',monotonic=lambda:clock[0],gap_journal=TMP/(mode+case+'_gaps.jsonl'))
        adapter=StaffIngressAdapter(cache,e.ingress)
        for chat,text in watches:e.ingress.post(Kind.COMMAND,source='commands',source_seq=None,source_time=(start-1)*1000,payload={'symbol':symbol,'chat_id':chat,'text':text})
        e.run();component=defaultdict(lambda:{'wall_ns':[],'cpu_ns':[],'kinds':defaultdict(int)})
        cat=Categories()
        if mode=='timed':
            categories_install(cat)
            for owner in e.processors+e.strategies:
                fn=owner.on_event;name=owner.name
                def make(original,key):
                    def measured(event,*args,**kwargs):
                        w=time.perf_counter_ns();c=time.process_time_ns()
                        try:return original(event,*args,**kwargs)
                        finally:
                            component[key]['cpu_ns'].append(time.process_time_ns()-c);component[key]['wall_ns'].append(time.perf_counter_ns()-w);component[key]['kinds'][str(event.kind)]+=1
                    return measured
                owner.on_event=make(fn,name)
        prof=cProfile.Profile() if mode=='profile' else None
        # File decode/STAFF publication cost is outside engine latency but inside
        # end-to-end CPU accounting, identically described for polling.
        cpu=time.process_time();wall=time.perf_counter()
        for index,(observed,raw) in enumerate(capture_bundles(path)):
            if observed>=end*1000:break
            clock[0]=observed/1000;adapter.publish(raw,source_time=observed)
            if prof:prof.enable()
            if mode=='timed':cat.begin()
            e.run()
            if mode=='timed':cat.end()
            if prof:prof.disable()
            if e.error_log:raise AssertionError(str(e.error_log))
            if index%60==59:print(mode,case,index+1,flush=True)
        result.update(cpu_seconds=time.process_time()-cpu,wall_seconds=time.perf_counter()-wall,bundles=e.metrics.bundle_count,
            notifications=sum(s.payload['content'].get('type')=='NOTIFICATION' for s in e.signals),errors=len(e.error_log))
        durations=np.asarray(e.metrics.bundle_ns)/1e6
        result['bundle_ms']={'mean':float(durations.mean()),'p99':float(np.percentile(durations,99)),'sum':float(durations.sum())}
        result['components']={k:{'calls':len(v['wall_ns']),'wall_seconds':sum(v['wall_ns'])/1e9,'cpu_seconds':sum(v['cpu_ns'])/1e9,'mean_ms':float(np.mean(v['wall_ns'])/1e6),'p99_ms':float(np.percentile(v['wall_ns'],99)/1e6),'kinds':dict(v['kinds'])} for k,v in component.items()}
        result['categories']={'exclusive_wall_ns':dict(cat.times),'calls':dict(cat.calls),'roots':cat.roots}
        if prof:
            prof.dump_stats(str(TMP/(mode+case+'.prof')));stats=pstats.Stats(prof)
            result['profile_total_self_seconds']=stats.total_tt
            result['profile_top30']=[{'file':f,'line':line,'function':name,'primitive_calls':v[0],'calls':v[1],'self_seconds':v[2],'cumulative_seconds':v[3]}
                for (f,line,name),v in sorted(stats.stats.items(),key=lambda kv:kv[1][3],reverse=True)[:30]]
    result['source_hashes_unchanged']=before==hashes()
    assert result['source_hashes_unchanged']
    save(TMP/(mode+case+'.json'),result);print('DONE',mode,case,result['cpu_seconds'],flush=True)
if __name__=='__main__':main(*sys.argv[1:])
