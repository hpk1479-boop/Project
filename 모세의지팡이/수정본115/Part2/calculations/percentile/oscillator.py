from .contracts import NativeCell,EMPTY_VALUE
from .numeric import cell,add,sub,mul,controlled,taints
from .bands import Grid100BandEngine,OscillatorExactPercentile
from .regime import OscillatorRegimeStage

def _arrow(state,i,next_i,family,side,pad=None):
    direction='lower' if side=='lower' else 'upper';out=state.read(direction,next_i);old=state.read('smooth',next_i)
    # Original && short-circuits the current comparison on a false previous test.
    if not old.available or not out.available:return NativeCell(None,'SOURCE_WRITTEN',taints(old,out)),None
    condition=old.value<out.value if side=='lower' else old.value>out.value
    if not condition:return controlled(cell(EMPTY_VALUE),old,out),False
    now=state.read('smooth',i);bound=state.read(direction,i)
    if not now.available or not bound.available:return NativeCell(None,'SOURCE_WRITTEN',taints(old,out,now,bound)),None
    current=now.value>=bound.value if side=='lower' else now.value<=bound.value
    if not current:return controlled(cell(EMPTY_VALUE),old,out,now,bound),False
    if pad is None:pad=cell(5.0)
    value=sub(bound,pad) if side=='lower' else add(bound,pad)
    return controlled(value,old,out,now,bound),True

class OscillatorPipeline:
    @staticmethod
    def execute(kernel,plan):
        state=kernel.state;p=kernel.profile.params;R=kernel.event.R;limit=plan.limit
        period=p['BandPeriod'];lag=int((p['Vibration']-1)/2);torque=2.0/(p['Vibration']+1.0);one_minus=1.0-torque
        grid=Grid100BandEngine(period,p['Leveling'])
        incremental=None
        if kernel.incremental_percentiles:
            from .incremental_bands import IncrementalBandEngine
            incremental=IncrementalBandEngine(kernel,lambda j:state.read('smooth',j),period,p['Leveling'],
                precision=p['UseHighPrecision'])
            grid=incremental
        for i in range(limit,-1,-1):
            nxt=i+1 if i+1<R else i
            value=add(mul(torque,sub(mul(2.0,state.read('raw',i)),state.read('raw',i+lag))),mul(one_minus,state.read('smooth',nxt)))
            state.write('smooth',i,value,'smooth_band')
            count=min(period,R-i)
            if count<=0:continue
            if incremental is not None:
                first=i==limit;boundary=i+count>=R
                outgoing=None if first or boundary else state.read('smooth',i+count)
                lower,upper=incremental.row(i,first=first,boundary=boundary,outgoing=outgoing)
            elif p['UseHighPrecision']:
                window=tuple(state.read('smooth',i+m) for m in range(count))
                lower=OscillatorExactPercentile.calculate(window,p['Leveling']);upper=OscillatorExactPercentile.calculate(window,100.0-p['Leveling'])
            else:
                window=tuple(state.read('smooth',i+m) for m in range(count))
                first=i==limit;boundary=i+count>=R
                outgoing=None if first or boundary else state.read('smooth',i+count)
                lower,upper=grid.row(window,first=first,boundary=boundary,outgoing=outgoing)
            flat=(not p['UseHighPrecision'] and grid.minimum.available and grid.maximum.available and grid.minimum.value==grid.maximum.value)
            # Original flat oscillator branch is low = high = lmin (right to left).
            if flat:
                if upper is not None:state.write('upper',i,upper,'smooth_band')
                if lower is not None:state.write('lower',i,lower,'smooth_band')
            else:
                if lower is not None:state.write('lower',i,lower,'smooth_band')
                if upper is not None:state.write('upper',i,upper,'smooth_band')
        alpha=2.0/(period+1.0)
        for i in range(limit,-1,-1):
            state.write('arrow_lower',i,EMPTY_VALUE,'arrow');state.write('arrow_upper',i,EMPTY_VALUE,'arrow')
            nxt=i+1 if i+1<R else i
            if i!=nxt:
                pad=cell(5.0)
                if kernel.family=='DI':
                    width=sub(state.read('upper',nxt),state.read('lower',nxt))
                    if width.available and width.value==0:width=controlled(cell(0.1),width)
                    pad=mul(width,0.15)
                for side in ('lower','upper'):
                    value,assigned=_arrow(state,i,nxt,kernel.family,side,pad)
                    if assigned:state.write('arrow_'+side,i,value,'arrow_signal')
                    else:state.control('arrow_'+side,i,value)
            OscillatorRegimeStage.row(state,i,R,period,alpha)
