import math
import sys
from ..contracts import HMA_SOURCE_SHA,PitError

class SourceHMAOpenKernel:
    version='HMA_EA_OPEN_PIT_V1'
    source_hash=HMA_SOURCE_SHA
    @staticmethod
    def wma_at(values,idx,length):
        if idx<length-1 or length<=0:return None
        numerator=0.0;denominator=0.0;weight=1
        isfinite=math.isfinite;empty_value=sys.float_info.max
        for k in range(idx-length+1,idx+1):
            value=values[k]
            if value is None or value==empty_value or not isfinite(value):return None
            # Reuse the same exact integer-to-float result. Accumulation order,
            # float64 arithmetic, sentinel checks and window bounds are unchanged.
            float_weight=float(weight)
            numerator+=value*float_weight
            denominator+=float_weight;weight+=1
        return numerator/denominator if denominator else None
    @classmethod
    def calculate(cls,values,period):
        if period not in (6,17):raise PitError('E_FEATURE_NOT_AVAILABLE','Only source OPEN HMA6/17')
        half=max(1,period//2);root=max(1,int(math.sqrt(float(period))))
        raw=[None]*len(values);out=[None]*len(values)
        for i in range(len(values)):
            a=cls.wma_at(values,i,half);b=cls.wma_at(values,i,period)
            if a is not None and b is not None:raw[i]=2.0*a-b
        for i in range(len(values)):out[i]=cls.wma_at(raw,i,root)
        return tuple(out)
    @staticmethod
    def warmup(period):return period+int(math.sqrt(period))-1
