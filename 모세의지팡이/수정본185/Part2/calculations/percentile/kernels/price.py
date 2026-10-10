from .base import SourceKernel
from ..contracts import NativeCell,EMPTY_VALUE
from ..numeric import cell,add,sub,mul,div,controlled,taints
from ..bands import Grid100BandEngine,PriceExactPercentile
from ..regime import PriceRegimeStage

class CloseHMA6Stage:
    @staticmethod
    def wma(source,shift,length,R):
        if shift<0 or shift+length-1>=R:return cell(EMPTY_VALUE)
        weighted=cell(0.0);denominator=0.0
        for j in range(length):
            weight=float(length-j);weighted=add(weighted,mul(source(shift+j),weight));denominator+=weight
        return div(weighted,denominator) if denominator>0.0 else cell(EMPTY_VALUE)
    @classmethod
    def raw(cls,source,shift,R):
        wma3=cls.wma(source,shift,3,R);wma6=cls.wma(source,shift,6,R)
        if wma3.value==EMPTY_VALUE or wma6.value==EMPTY_VALUE:return controlled(cell(EMPTY_VALUE),wma3,wma6)
        return sub(mul(2.0,wma3),wma6)
    @classmethod
    def calculate(cls,source,shift,R):
        raw0=cls.raw(source,shift,R);raw1=cls.raw(source,shift+1,R)
        if raw0.value==EMPTY_VALUE or raw1.value==EMPTY_VALUE:return controlled(cell(EMPTY_VALUE),raw0,raw1)
        return div(add(mul(raw0,2.0),raw1),3.0)

class PriceSourceKernel(SourceKernel):
    family='PRICE'
    def invoke(self,bars,event,captured_prestate=None,raw_input=None):
        plan=self._begin(bars,event);state=self.state;p=self.profile.params;R=event.R;limit=plan.limit
        state.capture(captured_prestate,self.profile.seed_policy)
        if captured_prestate is not None:self._band_window=None
        if not plan.ready:return self._result(0,plan)
        show_band=p['InpDisplayMode'] in (0,1);show_ma=p['InpDisplayMode'] in (0,2)
        if show_ma:
            from ..contracts import PercentileError
            raise PercentileError('E_UNSUPPORTED_INPUT_PROFILE','native iMA dependency')
        if not show_band and not show_ma:
            for i in range(limit,-1,-1):
                for name in ('upper','lower','ma1','ma2','ma3','hma','arrow_lower','arrow_upper','basis'):
                    state.write(name,i,EMPTY_VALUE,'hide_all')
                state.write('color',i,2,'hide_all')
                state.write('regime_upper',i,EMPTY_VALUE,'hide_all');state.write('regime_lower',i,EMPTY_VALUE,'hide_all')
            return self._result(R,plan)
        for i in range(limit,-1,-1):
            for name in ('ma1','ma2','ma3'):state.write(name,i,EMPTY_VALUE,'ma_hidden')
        torque=2.0/11.0;fraction=0.5
        for i in range(plan.ehlers_limit,-1,-1):
            reference=add(mul(self.price('close',i+4),1.0-fraction),mul(self.price('close',i+5),fraction))
            previous=state.read('ehlers',i+1) if i+1<R else self.price('close',i)
            if previous.available and (previous.value==0 or previous.value==EMPTY_VALUE):
                previous=controlled(self.price('close',i),previous)
            value=add(mul(torque,sub(mul(2.0,self.price('close',i)),reference)),mul(1.0-torque,previous))
            state.write('ehlers',i,value,'ehlers')
        source=lambda i:state.read('ehlers',i) if p['InpUseSmooth'] else self.price('close',i)
        grid=Grid100BandEngine(p['InpDomCycle'],p['InpLeveling'])
        incremental=None
        if self.incremental_percentiles:
            from ..incremental_bands import IncrementalBandEngine
            incremental=IncrementalBandEngine(self,source,p['InpDomCycle'],p['InpLeveling'],
                precision=p['InpUseHighPrecision'],price=True)
        for i in range(limit,-1,-1):
            count=min(p['InpDomCycle'],R-i)
            if count<=0:continue
            if incremental is not None:
                outgoing=(source(i+count) if i+count<R else cell(0.0)) if not p['InpUseHighPrecision'] else None
                lower,upper=incremental.row(i,first=i==limit,boundary=i+count>=R,outgoing=outgoing)
            elif p['InpUseHighPrecision']:
                window=tuple(source(i+j) for j in range(count))
                lower=PriceExactPercentile.calculate(window,p['InpLeveling']);upper=PriceExactPercentile.calculate(window,100.0-p['InpLeveling'])
            else:
                window=tuple(source(i+j) for j in range(count))
                # PRICE evaluates out_val before the OR condition, unlike oscillators.
                outgoing=source(i+count) if i+count<R else cell(0.0)
                lower,upper=grid.row(window,first=i==limit,boundary=i+count>=R,outgoing=outgoing)
            if lower is not None:state.write('lower',i,lower,'band')
            if upper is not None:state.write('upper',i,upper,'band')
        for i in range(limit,-1,-1):
            if p['InpShowRegimeBand']:PriceRegimeStage.row(state,source,i,R,event.P,limit)
            else:
                state.write('basis',i,EMPTY_VALUE,'regime_hidden');state.write('color',i,2,'regime_hidden')
                state.write('regime_upper',i,EMPTY_VALUE,'regime_hidden');state.write('regime_lower',i,EMPTY_VALUE,'regime_hidden')
        close=lambda i:self.price('close',i)
        for i in range(plan.hma_limit,-1,-1):state.write('hma',i,CloseHMA6Stage.calculate(close,i,R),'hma')
        for i in range(limit,-1,-1):
            state.write('arrow_lower',i,EMPTY_VALUE,'arrow');state.write('arrow_upper',i,EMPTY_VALUE,'arrow')
        signal_scans=[]
        for i in range(limit,0,-1):
            if i+1>=R:continue
            guards=tuple(state.read(name,j) for name,j in (('hma',i),('hma',i+1),('lower',i),('lower',i+1),('upper',i),('upper',i+1)))
            if any(v.available and v.value==EMPTY_VALUE for v in guards):continue
            if any(not v.available for v in guards):
                if p['InpShowSignals']:
                    unknown=NativeCell(None,'SOURCE_WRITTEN',taints(*guards))
                    state.control('arrow_lower',i,unknown);state.control('arrow_upper',i,unknown)
                continue
            now,old,lower,old_lower,upper,old_upper=guards
            conditions=(old.value<old_lower.value and now.value>=lower.value,old.value>old_upper.value and now.value<=upper.value)
            high=self.price('high',i);low=self.price('low',i);range_pad=mul(sub(high,low),0.25)
            pad=controlled(cell(max(range_pad.value,event.point*10.0)),range_pad) if range_pad.available else range_pad
            for side,condition in zip(('lower','upper'),conditions):
                if not condition:
                    if p['InpShowSignals']:state.control('arrow_'+side,i,controlled(cell(EMPTY_VALUE),*guards))
                    continue
                extreme=low if side=='lower' else high;k=i+1
                while k<R:
                    hma=state.read('hma',k);bound=state.read(side,k)
                    if not hma.available or not bound.available:
                        extreme=NativeCell(None,'SOURCE_WRITTEN',taints(extreme,hma,bound));break
                    if hma.value==EMPTY_VALUE or bound.value==EMPTY_VALUE:break
                    outside=hma.value<bound.value if side=='lower' else hma.value>bound.value
                    if not outside:break
                    value=self.price('low' if side=='lower' else 'high',k)
                    if not value.available:extreme=value;break
                    if extreme.available and ((value.value<extreme.value) if side=='lower' else (value.value>extreme.value)):extreme=value
                    k+=1
                signal_scans.append((i,side,k,extreme.bits))
                if p['InpShowSignals']:
                    value=sub(low,pad) if side=='lower' else add(high,pad)
                    state.write('arrow_'+side,i,controlled(value,*guards),'arrow_signal')
        if not p['InpShowHMA6']:
            for i in range(limit,-1,-1):state.write('hma',i,EMPTY_VALUE,'hma_hidden')
        return self._result(R,plan,{'signal_extreme_scans':tuple(signal_scans),'native_iMA_initialization':'UNVERIFIED_EXTERNAL_DEPENDENCY'})
