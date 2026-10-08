"""Exact source expression and retained-window tests; no numeric tolerance."""
from pathlib import Path
import sys, math, random
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pit.features.percentile.incremental_bands import RollingNativeWindow,IncrementalBandEngine
from pit.features.percentile.bands import Grid100BandEngine,PriceExactPercentile,OscillatorExactPercentile
from pit.features.percentile.contracts import NativeCell,CalcEvent,EMPTY_VALUE
from pit.features.percentile.profiles import freeze_source_profile
from pit.features.percentile.kernels.price import PriceSourceKernel
from pit.features.percentile.kernels.rsi import RsiSourceKernel
from pit.features.percentile.kernels.stochastic import StoSourceKernel
from pit.features.percentile.kernels.disparity import DiSourceKernel
KERNELS={'PRICE':PriceSourceKernel,'RSI':RsiSourceKernel,'STO':StoSourceKernel,'DI':DiSourceKernel}


def native(x,i=0):
    return NativeCell.from_float(x,('SOURCE_LITERAL','MARKET_INPUT','SOURCE_WRITTEN')[i%3],('diagnostic',) if i%13==0 else (),str(i))

@pytest.mark.parametrize('price',[True,False])
@pytest.mark.parametrize('case',['normal','constant','duplicates','nan','unknown','zero','infinity','empty'])
def test_order_statistic_exact_values(price,case):
    rng=random.Random(77)
    w=RollingNativeWindow(20)
    values=[]
    oracle=PriceExactPercentile if price else OscillatorExactPercentile
    for i in range(350):
        x=rng.uniform(-100,100)
        if case=='constant':x=9.
        elif case=='duplicates':x=float(rng.randrange(5))
        elif case=='nan' and i%19==0:x=float('nan')
        elif case=='zero':x=-0. if i%2 else 0.
        elif case=='infinity' and i%19==0:x=float('inf') if i%2 else float('-inf')
        elif case=='empty' and i%19==0:x=EMPTY_VALUE
        value=NativeCell.unknown('X') if case=='unknown' and i%19==0 else native(x,i)
        values.append(value);ids=tuple(str(j) for j in range(len(values)))
        w.bind(ids,'APPEND',False)
        source=lambda shift:values[-1-shift]
        w.move(len(values)-1,source)
        window=tuple(reversed(values[-20:]))
        for percent in (0.,10.,33.3,50.,90.,100.):
            assert w.percentile(percent,price=price)==oracle.calculate(window,percent),(case,i,percent)
    assert w.stats['full_window_builds']==1
    assert w.stats['full_sorts']==0

@pytest.mark.parametrize('case',['normal','constant','duplicates','nan','unknown','zero','infinity','empty'])
def test_grid_call_local_extrema_and_exact_boundaries(case):
    rng=random.Random(13);values=[]
    kernel=SimpleNamespace(_band_window=None,profile=SimpleNamespace(params={'InpUseSmooth':False}))
    for i in range(120):
        x=rng.uniform(-100,100)
        if case=='constant':x=42.
        if case=='duplicates':x=float(i%4)
        if case=='nan' and i%17==0:x=float('nan')
        if case=='zero':x=-0. if i%2 else 0.
        if case=='infinity' and i%17==0:x=float('inf')
        if case=='empty' and i%17==0:x=EMPTY_VALUE
        value=NativeCell.unknown('missing') if case=='unknown' and i%17==0 else native(x,i)
        values.append(value)
        bars=tuple({'bar_id':str(j),'state':'COMPLETED'} for j in range(len(values)))
        kernel.bars=SimpleNamespace(values=bars)
        kernel.state=SimpleNamespace(bar_ids=tuple(str(j) for j in range(len(values))),layout_kind='APPEND',writes=[])
        source=lambda shift:values[-1-shift]
        opt=IncrementalBandEngine(kernel,source,20,10.,price=True)
        ref=Grid100BandEngine(20,10.)
        limit=min(2,len(values)-1)
        for shift in range(limit,-1,-1):
            n=min(20,len(values)-shift);window=tuple(source(shift+j) for j in range(n))
            outgoing=source(shift+n) if shift+n<len(values) else native(0.)
            expected=ref.row(window,first=shift==limit,boundary=shift+n>=len(values),outgoing=outgoing)
            actual=opt.row(shift,first=shift==limit,boundary=shift+n>=len(values),outgoing=outgoing)
            assert actual==expected,(case,i,shift,actual,expected)
            assert (opt.minimum,opt.maximum)==(ref.minimum,ref.maximum)


def bars_for(n,case='normal'):
    result=[]
    for i in range(n):
        x=100+math.sin(i/7)*3+i*.001
        if case=='constant':x=100.
        if case=='duplicates':x=100.+float(i%4)
        if case=='nan' and i in (35,76):x=float('nan')
        result.append(dict(bar_id=str(i),open=x,high=x+1,low=x-1,close=x,quality='COMPLETE_PREFIX',state='COMPLETED'))
    if result:result[-1]['state']='FORMING'
    return result


def assert_kernel_exact(a,b):
    assert a.returned==b.returned
    assert a.metadata==b.metadata
    assert a.writes==b.writes
    assert a.buffers.keys()==b.buffers.keys()
    for name in a.buffers:
        assert a.buffers[name]==b.buffers[name],name

@pytest.mark.parametrize('family',KERNELS)
@pytest.mark.parametrize('precision',[False,True])
@pytest.mark.parametrize('seeded',[False,True])
def test_kernel_previews_and_source_write_order(family,precision,seeded):
    option='InpUseHighPrecision' if family=='PRICE' else 'UseHighPrecision'
    profile=freeze_source_profile(family,{option:precision},seed_policy='NATIVE_STATE_CONDITIONED' if seeded else 'SOURCE_DEFINED_ONLY')
    a=KERNELS[family](profile);b=KERNELS[family](profile);a.incremental_percentiles=False
    bars=bars_for(64);previous=0
    for i in range(80):
        if i and i%4==0:
            bars[-1]=dict(bars[-1],state='COMPLETED')
            j=len(bars);x=100+math.sin(j/7)*3+j*.001
            bars.append(dict(bar_id=str(j),open=x,high=x+1,low=x-1,close=x,quality='COMPLETE_PREFIX',state='FORMING'))
        elif i:
            old=bars[-1];x=old['open']+math.sin(i)*.5
            bars[-1]=dict(old,close=x,high=max(old['high'],x),low=min(old['low'],x))
        event=CalcEvent(str(i),len(bars),previous)
        capture={name:[NativeCell.from_float(0.)]*len(bars) for name in a.state.buffers} if seeded and not i else None
        ra=a.invoke(bars,event,captured_prestate=capture);rb=b.invoke(bars,event,captured_prestate=capture)
        assert_kernel_exact(ra,rb);previous=ra.returned
        if b._band_window:
            assert len(b._band_window.order)<=20
            assert not b._band_window.order or b._band_window.order[-1]!=bars[-1]['bar_id']
    assert b._band_window.stats['full_window_builds']==1
    # Rebuilding an index after a checkpoint must not change native state.
    restored=KERNELS[family](profile);restored.restore(b.snapshot())
    event=CalcEvent('after_restore',len(bars),previous)
    assert_kernel_exact(b.invoke(bars,event),restored.invoke(bars,event))

@pytest.mark.parametrize('family',KERNELS)
def test_warmup_native_availability_unchanged(family):
    a=KERNELS[family]();b=KERNELS[family]();a.incremental_percentiles=False
    previous=0
    for n in range(1,81):
        event=CalcEvent(str(n),n,previous)
        ra=a.invoke(bars_for(n),event);rb=b.invoke(bars_for(n),event)
        assert_kernel_exact(ra,rb);previous=ra.returned

@pytest.mark.parametrize('family',KERNELS)
@pytest.mark.parametrize('precision',[False,True])
def test_completed_state_reindex_and_recapture(family,precision):
    option='InpUseHighPrecision' if family=='PRICE' else 'UseHighPrecision'
    p=freeze_source_profile(family,{option:precision},seed_policy='NATIVE_STATE_CONDITIONED')
    a=KERNELS[family](p);b=KERNELS[family](p);a.incremental_percentiles=False
    bars=bars_for(70);previous=0
    for i in range(25):
        if i==8: # checkpoint identity reindex / truncated full recalculation
            bars=bars[5:];previous=0
        elif i:
            bars[-1]=dict(bars[-1],state='COMPLETED')
            j=100+i;x=101+math.sin(j)
            bars.append(dict(bar_id=str(j),open=x,high=x+1,low=x-1,close=x,quality='COMPLETE_PREFIX',state='COMPLETED'))
        # Includes completed base-TF rows and capture after a populated index.
        if i in (0,13):
            capture={n:[NativeCell.from_float(.25)]*len(bars) for n in a.state.buffers}
        else:capture=None
        event=CalcEvent(str(i),len(bars),previous)
        ra=a.invoke(bars,event,captured_prestate=capture)
        rb=b.invoke(bars,event,captured_prestate=capture)
        assert_kernel_exact(ra,rb);previous=ra.returned
        if bars[-1]['state']=='COMPLETED':assert b._band_window.preview is None
