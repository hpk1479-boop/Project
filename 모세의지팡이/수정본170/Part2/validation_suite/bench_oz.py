"""Same observation sequence, full ordered OZ state/event digest and wall-time."""
import argparse,sys,json,time,cProfile,pstats,io,collections,hashlib
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--out',required=True)
p.add_argument('--count',type=int,default=1000);p.add_argument('--rows',type=int,default=96)
p.add_argument('--reference',action='store_true');p.add_argument('--profile',action='store_true')
args=p.parse_args();sys.path.insert(0,args.root)
from oz_fixtures import Sequence,PROFILES,full_state
from generic_backtest.watch.engines.oz import HistoricalOZEngine
from generic_backtest.watch.engines.inputs import exact_digest
report={'scope':'synthetic supplied-OZ-frame state-machine replay, not raw/native pipeline','profiles':[]}
for vm,tm in PROFILES:
    engine=HistoricalOZEngine('TEST',vm,tm,max_bars=10,max_cross_bars=7,max_outin_bars=4)
    engine.use_numpy=not args.reference
    prof=cProfile.Profile() if args.profile else None
    total=0.;digests=[];counts=collections.Counter();directions=collections.Counter()
    prev_hma={};prev_candidates={};prev_true={};prev_states={}
    state_counts=collections.Counter()
    for i,(data,token,env,delivery) in enumerate(Sequence(count=args.count,rows=args.rows,scenario='nan')):
        if i and i%113==0:engine.mark_observation_gap()
        if prof:prof.enable()
        start=time.perf_counter();events=engine.observe(data,token,environments=env,delivery_succeeded=delivery)
        total+=time.perf_counter()-start
        if prof:prof.disable()
        for e in events:
            counts[e['kind']]+=1
            if e['kind']=='OZ_LOCAL_ALERT':directions[e['direction']]+=1
        state=full_state(engine);digests.append(exact_digest((events,state)))
        for key,value in engine.hma_cross_extremes.items():
            if value is not None and prev_hma.get(key)!=value:state_counts['hma_cross_updates']+=1
        for key,value in engine.candidates.items():
            if value is not None:
                state_counts['candidate_observations']+=1
                ident=(value.outin_b0_time,value.trigger_time)
                if prev_candidates.get(key)!=ident:state_counts['candidate_structure_updates']+=1
                if prev_true.get(key)!=(value.true_b0_price,value.true_b0_time):state_counts['true_b0_updates']+=1
        for tf,states in engine.prev_states.items():
            for family,current in states.items():
                if prev_states.get(tf,{}).get(family) in ('LOWER_OUT','UPPER_OUT') and current=='IN':state_counts['out_to_in']+=1
        prev_hma=dict(engine.hma_cross_extremes)
        prev_candidates={k:(v.outin_b0_time,v.trigger_time) for k,v in engine.candidates.items() if v is not None}
        prev_true={k:(v.true_b0_price,v.true_b0_time) for k,v in engine.candidates.items() if v is not None}
        prev_states={tf:dict(s) for tf,s in engine.prev_states.items()}
    row={'validation_mode':vm,'trigger_mode':tm,'observations':args.count,'rows_per_tf':args.rows,
         'observe_wall_seconds':total,'cprofile_enabled':bool(prof),'events':dict(counts),'alerts_by_direction':dict(directions),
         'state_coverage':dict(state_counts),'observation_digests':digests,
         'sequence_digest':exact_digest(digests),'numpy_stats':getattr(engine,'numpy_stats',None)}
    if prof:
        buf=io.StringIO();pstats.Stats(prof,stream=buf).sort_stats('cumtime').print_stats(50)
        path=str(Path(args.out).with_suffix(''))+'_'+vm+'_'+tm+'.profile.txt'
        Path(path).write_text(buf.getvalue())
        stats=pstats.Stats(prof).stats
        row['pandas_calls']={f'{Path(k[0]).name}:{k[1]}:{k[2]}':v[1] for k,v in stats.items()
                             if '/pandas/' in k[0] and k[2] in ('__init__','_ixs','_getitem_axis','_get_value','to_datetime','_cmp_method','copy','concat','rolling','shift')}
    report['profiles'].append(row)
    Path(args.out).write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in row.items() if k!='observation_digests'}),flush=True)
