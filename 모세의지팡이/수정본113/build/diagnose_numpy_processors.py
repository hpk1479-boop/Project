"""Whole-history numerical reference diagnostics, never old-alert gates."""
import sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'Part1/program'),str(ROOT/'tests')]
import numpy as np
from test_numpy_processors import view,edit,epoch
from staff_schema import legacy_frame
from indicator_facts import standalone_frame,ema,sma,wma,hma
from indicator_facts_numpy import ArrayFactFrame
from indicator_score import SCORE_FACTS,score_from_facts
from indicator_array_score import score_from_facts as array_score
from watch_array_facts import WatchMAStore
from event_engine.sweep_levels import external_levels
from event_engine.fvg_structure import StructureStore
import strategy_SWEEP as sweep
import strategy_FVG as fvg

def main():
    rows=[];details=[]
    for seed in (7,29,81):
        rng=np.random.default_rng(seed);n=650;o=3000+np.cumsum(rng.normal(0,2,n));c=o+rng.normal(0,2,n)
        v=view(n,open=o,close=c,high=np.maximum(o,c)+.5,low=np.minimum(o,c)-.5,hma_50=o)
        df=legacy_frame('X','1m',v.snapshot)
        for tf in ('1m','15m','1h','4h'):
            old=standalone_frame(df,tf);new=ArrayFactFrame(v,tf)
            for name in dict.fromkeys((*SCORE_FACTS,'ATR14_GENERAL')):
                a=np.asarray(old[name],dtype=float);b=np.asarray(new[name],dtype=float)
                equal=np.isclose(a,b,rtol=1e-10,atol=1e-10,equal_nan=True)
                delta=float(np.nanmax(abs(a-b))) if np.isfinite(a-b).any() else 0.
                record=dict(family='INDICATOR',seed=seed,tf=tf,fact=name,rows=n,different=int((~equal).sum()),max_abs=delta)
                rows.append(record)
                if not equal.all():details.append(record|{'indices':np.flatnonzero(~equal).tolist()})
            for direction in ('LONG','SHORT'):
                rows.append(dict(family='SCORE',seed=seed,tf=tf,direction=direction,old=score_from_facts(old,direction),new=array_score(new,direction)))
        store=WatchMAStore()
        for family,fn in (('SMA',sma),('WMA',wma),('EMA',ema),('HMA',hma)):
            for period in (7,23,37,72):
                name=family+str(period);a=fn(df['close' if family=='EMA' else 'open'],period).to_numpy();b=store.get('X','1m',v,[name])[name]
                equal=np.isclose(a,b,rtol=1e-10,atol=1e-10,equal_nan=True)
                record=dict(family='WATCH_MA',seed=seed,name=name,different=int((~equal).sum()),max_abs=float(np.nanmax(abs(a-b))))
                rows.append(record)
                if not equal.all():details.append(record)
        previous=fvg.build_fvg_state('X','1m',df,cache=None);current=StructureStore().evaluate('X','1m',v)
        current['filled_zones']=list(current['filled_zones'])
        record=dict(family='FVG',seed=seed,equal=previous==current,zones=len(current['zones']))
        rows.append(record)
        if not record['equal']:details.append(record|dict(old=previous,new=current))
    for day in ('2026-09-21','2026-09-25','2026-09-28'):
        views={tf:view(650,time=np.arange(650)*step+epoch(day)-650*step,high=100+np.arange(650),low=99-np.arange(650)) for tf,step in [('1d',86400),('4h',14400),('8h',28800),('5m',300)]}
        frames={tf:legacy_frame('X',tf,v.snapshot) for tf,v in views.items()}
        for london,ny in [('', ''),('1600-2100','2200-0300')]:
            a=sweep.build_external_levels(frames['1d'],frames['5m'],frames['4h'],frames['8h'],london,ny);b=external_levels(views,london,ny)
            record=dict(family='SWEEP',day=day,sessions=[london,ny],equal=a==b,levels=len(b));rows.append(record)
            if a!=b:details.append(record|dict(old=a,new=b))
    result={'policy':'diagnostic; same complete 650-row history, float tolerance 1e-10; predicates separately tested',
            'cases':rows,'differences':details}
    out=ROOT/'검증결과/numpy_processors/calculation_diagnostics.json';out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('cases',len(rows),'differences',len(details),flush=True)
    for row in details[:15]:print({k:v for k,v in row.items() if k not in ('old','new','indices')})
if __name__=='__main__':main()
