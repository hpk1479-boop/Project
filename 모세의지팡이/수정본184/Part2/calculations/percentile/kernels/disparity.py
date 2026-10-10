from .base import SourceKernel
from ..contracts import NativeCell
from ..numeric import cell,sub,div,mul,sequential_sum,controlled
from ..oscillator import OscillatorPipeline

class DiSourceKernel(SourceKernel):
    family='DI'
    def invoke(self,bars,event,captured_prestate=None,raw_input=None):
        plan=self._begin(bars,event);state=self.state;p=self.profile.params
        state.capture(captured_prestate,self.profile.seed_policy)
        if captured_prestate is not None:self._band_window=None
        if not plan.ready:return self._result(0,plan)
        if event.R>state.allocated:
            state.allocated=event.R+1024;state.allocate_raw(state.allocated)
        for i in range(plan.raw_limit,-1,-1):
            sma=div(sequential_sum(self.price('close',i+j) for j in range(p['DILength'])),p['DILength'])
            if not sma.available:value=sma
            elif sma.value==0:value=controlled(cell(0.0),sma)
            else:value=mul(div(sub(self.price('close',i),sma),sma),100.0)
            state.write('raw',i,value,'raw')
        OscillatorPipeline.execute(self,plan);return self._result(event.R,plan)
