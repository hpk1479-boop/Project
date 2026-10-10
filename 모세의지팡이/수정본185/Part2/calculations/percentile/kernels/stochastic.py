from .base import SourceKernel
from ..contracts import NativeCell
from ..numeric import cell,sub,div,mul,controlled,taints
from ..oscillator import OscillatorPipeline

class StoSourceKernel(SourceKernel):
    family='STO'
    def invoke(self,bars,event,captured_prestate=None,raw_input=None):
        plan=self._begin(bars,event);state=self.state;p=self.profile.params
        state.capture(captured_prestate,self.profile.seed_policy)
        if captured_prestate is not None:self._band_window=None
        if not plan.ready:return self._result(0,plan)
        if event.R>state.allocated:
            state.allocated=event.R+1024;state.allocate_raw(state.allocated)
        for i in range(plan.raw_limit,-1,-1):
            highs=[self.price('high',i+j) for j in range(p['STOLength'])];lows=[self.price('low',i+j) for j in range(p['STOLength'])]
            dependencies=highs+lows
            if any(not c.available for c in dependencies):value=NativeCell(None,'SOURCE_WRITTEN',taints(*dependencies))
            else:
                high=highs[0].value;low=lows[0].value
                for j in range(1,p['STOLength']):
                    if highs[j].value>high:high=highs[j].value
                    if lows[j].value<low:low=lows[j].value
                value=cell(50.0) if high-low==0 else mul(div(sub(self.price('close',i),low),high-low),100.0)
                value=controlled(value,*dependencies)
            state.write('raw',i,value,'raw')
        OscillatorPipeline.execute(self,plan);return self._result(event.R,plan)
