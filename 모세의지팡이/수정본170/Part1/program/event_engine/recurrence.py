"""Engine-owned memoization of the canonical pure EWM step, never a new formula."""
import numpy as np
from indicator_facts_numpy import ewm_step

class EWMPrefix:
    def __init__(self):
        self.key=None;self.times=None;self.inputs=None;self.output=None;self.states=[]
        self.full_rebuilds=0;self.steps=0
    def evaluate(self,times,values,source_epoch,alpha,min_periods):
        times=np.asarray(times,dtype='<i8');values=np.asarray(values,dtype='<f8')
        n=len(values)
        if times.shape!=values.shape:raise ValueError('EWM time/value axis mismatch')
        key=(source_epoch,n,int(times[-2]) if n>1 else None,float(alpha),min_periods)
        # Compare closed inputs as uint64: NaN payloads and -0.0 corrections are
        # never mistaken for identical source data. Sliding windows reseed using
        # their own first value, exactly as a fresh whole-array calculation does.
        reusable=(self.key is not None and self.key[0]==source_epoch and self.key[3:]==key[3:]
                  and n>=len(self.times) and np.array_equal(times[:max(0,len(self.times)-1)],self.times[:-1])
                  and np.array_equal(values[:max(0,len(self.times)-1)].view('<u8'),self.inputs[:-1].view('<u8')))
        start=max(0,len(self.times)-1) if reusable else 0
        if not reusable:self.full_rebuilds+=1
        output=np.empty(n,dtype='<f8');states=[]
        if start:
            output[:start]=self.output[:start];states=self.states[:start]
        state=states[-1] if states else (float('nan'),1.,0)
        for i in range(start,n):
            state,output[i]=ewm_step(values[i],state,alpha,min_periods);states.append(state);self.steps+=1
        self.key=key;self.times=times.copy();self.inputs=values.copy();self.output=output;self.states=states
        return output
