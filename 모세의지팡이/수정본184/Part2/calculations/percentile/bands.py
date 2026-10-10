"""The two original percentile expression trees and call-local grid state."""
import math
from .contracts import NativeCell
from .numeric import cell,add,sub,mul,div,controlled,taints

def _sorted(values):
    values=tuple(map(cell,values))
    if any(not v.available for v in values):return None,NativeCell(None,'SOURCE_WRITTEN',taints(*values))
    if any(math.isnan(v.value) for v in values):return None,NativeCell.unknown('UNVERIFIED_NATIVE_ARRAYSORT_NAN')
    zero_signs={v.bits>>63 for v in values if v.value==0.0}
    if len(zero_signs)>1:
        # Native build6182's observed equal-zero ordering differs from Python's
        # stable sort. The four-element probe does not define its general sort.
        return None,NativeCell(None,'UNVERIFIED_NATIVE_DEPENDENCY',('UNVERIFIED_NATIVE_ARRAYSORT_MIXED_SIGNED_ZERO',))
    return sorted(values,key=lambda v:v.value),None

class PriceExactPercentile:
    @staticmethod
    def calculate(values,percent):
        arr,unknown=_sorted(values)
        if unknown:return unknown
        n=len(arr)
        if not n:return cell(0.0)
        rank=(percent/100.0)*(n-1);lo=math.floor(rank);hi=math.ceil(rank)
        if lo<0:lo=0
        if hi>=n:hi=n-1
        if not(0<=lo<n and 0<=hi<n):return NativeCell.unknown('SOURCE_ARRAY_BOUNDS')
        if lo==hi:return controlled(arr[lo],*arr)
        w=rank-lo
        return controlled(add(mul(arr[lo],1.0-w),mul(arr[hi],w)),*arr)

class OscillatorExactPercentile:
    @staticmethod
    def calculate(values,percent):
        arr,unknown=_sorted(values)
        if unknown:return unknown
        n=len(arr)
        if n==0:return cell(0.0)
        if n==1:return arr[0]
        rank=(percent/100.0)*(n-1);idx=math.floor(rank);frac=rank-idx
        if idx>=n-1:return controlled(arr[-1],*arr)
        if idx<0:return controlled(arr[0],*arr)
        return controlled(add(arr[idx],mul(frac,sub(arr[idx+1],arr[idx]))),*arr)

class Grid100BandEngine:
    def __init__(self,period=20,leveling=10.0):
        self.period=period;self.leveling=leveling;self.target=math.ceil(period*leveling/100.0)
        self.minimum=cell(0.0);self.maximum=cell(0.0)
    def row(self,values,*,first=False,boundary=False,outgoing=None):
        values=tuple(map(cell,values))
        if not values:return None,None
        # Do not dereference outgoing when an earlier OR operand short-circuits.
        deps=list(values)
        rebuild=first or boundary
        if not rebuild:
            outgoing=cell(outgoing) if outgoing is not None else NativeCell.unknown('OUTGOING_UNAVAILABLE')
            deps.extend((outgoing,self.minimum,self.maximum))
            if not all(v.available for v in deps):
                unknown=NativeCell(None,'SOURCE_WRITTEN',taints(*deps));self.minimum=self.maximum=unknown;return unknown,unknown
            rebuild=outgoing.value==self.minimum.value or outgoing.value==self.maximum.value
        if any(not v.available for v in deps):
            unknown=NativeCell(None,'SOURCE_WRITTEN',taints(*deps));self.minimum=self.maximum=unknown;return unknown,unknown
        if rebuild:
            low=high=values[0].value
            for value in values[1:]:
                if value.value<low:low=value.value
                elif value.value>high:high=value.value
        else:
            low=self.minimum.value;high=self.maximum.value
            if values[0].value<low:low=values[0].value
            if values[0].value>high:high=values[0].value
        self.minimum=controlled(cell(low),*deps);self.maximum=controlled(cell(high),*deps)
        if low==high:return self.minimum,self.maximum
        step=(high-low)*0.01;lower=upper=None
        for s in range(101):
            threshold=low+s*step;count=0
            for value in values:
                if count>=self.target:break
                if value.value<threshold:count+=1
            if count>=self.target:lower=controlled(cell(threshold),*deps);break
        for s in range(101):
            threshold=high-s*step;count=0
            for value in values:
                if count>=self.target:break
                if value.value>=threshold:count+=1
            if count>=self.target:upper=controlled(cell(threshold),*deps);break
        return lower,upper
