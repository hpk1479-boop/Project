"""Per-family exact invocation ledger; finite diagnostic seeds are explicit."""
import argparse,json,math,sys,time,hashlib
from pathlib import Path
from dataclasses import asdict
p=argparse.ArgumentParser();p.add_argument('--family',required=True);p.add_argument('--count',type=int,default=1200);p.add_argument('--block-size',type=int,default=240);p.add_argument('--out',required=True);args=p.parse_args()
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root));sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_percentile import KERNELS,bars_for
from pit.features.percentile.contracts import CalcEvent,NativeCell
from pit.features.percentile.profiles import freeze_source_profile
family=args.family;report={'family':family,'comparison':'NativeCell bits, origin, taint, last_write_event; all buffers/writes/metadata; no tolerance',
    'real_raw':False,'native_terminal_oracle':False,'cases':[]}
for precision in (False,True):
 for case in ('normal','constant','nan','warmup','sliding','duplicates','preview'):
    seeded=case!='warmup'
    opt='InpUseHighPrecision' if family=='PRICE' else 'UseHighPrecision'
    profile=freeze_source_profile(family,{opt:precision},seed_policy='NATIVE_STATE_CONDITIONED' if seeded else 'SOURCE_DEFINED_ONLY')
    a=KERNELS[family](profile);b=KERNELS[family](profile);a.incremental_percentiles=False
    bars=bars_for(64 if seeded else 1,case=case);previous=0;bad=0;first=[];ar=br=0.;cells=0;finite=undefined=0
    cold_a=cold_b=0.;index_stats=[]
    def check(label,x,y,obs):
        global bad
        if x!=y:
            bad+=1
            if len(first)<10:first.append({'observation':obs,'component':label,'expected':repr(x)[:500],'actual':repr(y)[:500]})
    for i in range(args.count):
        local=i%args.block_size
        if not local and i:
            if b._band_window:index_stats.append(dict(b._band_window.stats))
            a=KERNELS[family](profile);b=KERNELS[family](profile);a.incremental_percentiles=False
            bars=bars_for(64 if seeded else 1,case=case);previous=0
        append=local and (case in ('warmup','sliding') or i%4==0)
        if append:
            bars[-1]=dict(bars[-1],state='COMPLETED')
            j=len(bars);x=100+math.sin(j/7)*3+j*.001
            if case=='constant':x=100.
            if case=='duplicates':x=100.+float(j%4)
            if case=='nan' and j%59==0:x=float('nan')
            bars.append(dict(bar_id=str(j),open=x,high=x+1,low=x-1,close=x,quality='COMPLETE_PREFIX',state='FORMING'))
        elif local:
            old=bars[-1]
            x=old['open'] if case=='constant' else old['open']+math.sin(i)*.5
            if case=='duplicates':x=100.+float(i%4)
            if case=='nan' and i%59==0:x=float('nan')
            bars[-1]=dict(old,close=x,high=max(old['high'],x),low=min(old['low'],x))
        event=CalcEvent(str(i),len(bars),previous)
        capture={name:[NativeCell.from_float(0.)]*len(bars) for name in a.state.buffers} if seeded and not local else None
        t=time.perf_counter();ra=a.invoke(bars,event,captured_prestate=capture);delta_a=time.perf_counter()-t;ar+=delta_a
        t=time.perf_counter();rb=b.invoke(bars,event,captured_prestate=capture);delta_b=time.perf_counter()-t;br+=delta_b
        if not local:cold_a+=delta_a;cold_b+=delta_b
        check('returned',ra.returned,rb.returned,i);check('metadata',ra.metadata,rb.metadata,i);check('writes',ra.writes,rb.writes,i)
        for name in ra.buffers:
            aa=ra.buffers[name];bb=rb.buffers[name];cells+=len(aa)
            if aa!=bb:
                for k,(x,y) in enumerate(zip(aa,bb)):
                    check(f'{name}[{k}]',x,y,i)
                check(f'{name}.length',len(aa),len(bb),i)
        for name in ('raw','smooth','lower','upper'):
            c=ra.cell(name)
            if c.available and math.isfinite(c.value):finite+=1
            elif not c.available:undefined+=1
        previous=ra.returned
    row={'case':case,'precision':'exact' if precision else 'grid100','seed_policy':profile.seed_policy,'observations':args.count,
         'independent_sequence_block_size':args.block_size,'block_count':math.ceil(args.count/args.block_size),
         'cold_initialization_reference_seconds':cold_a,'cold_initialization_incremental_seconds':cold_b,
         'warm_updates_reference_seconds':ar-cold_a,'warm_updates_incremental_seconds':br-cold_b,
         'disk_cache':'DISABLED','compared_buffer_cells':cells,'current_finite_samples':finite,'current_undefined_samples':undefined,
         'mismatch_count':bad,'first_mismatches':first,'reference_compute_seconds':ar,'incremental_compute_seconds':br,
         'speedup':ar/br,'incremental_stats_by_block':index_stats+([dict(b._band_window.stats)] if b._band_window else [])}
    report['cases'].append(row);print(json.dumps(row),flush=True)
    Path(args.out).write_text(json.dumps(report,indent=2))
report['observations']=sum(r['observations'] for r in report['cases']);report['mismatch_count']=sum(r['mismatch_count'] for r in report['cases'])
report['status']='PASS' if not report['mismatch_count'] else 'FAIL'
Path(args.out).write_text(json.dumps(report,indent=2))
