"""One Python float operation per source operation; native equivalence needs probes."""
import math
from .contracts import NativeCell

def cell(value): return value if isinstance(value,NativeCell) else NativeCell.from_float(value,'SOURCE_LITERAL')
def taints(*values): return tuple(sorted({t for v in values for t in cell(v).taint}))
def calc(fn,*values):
    args=tuple(cell(v) for v in values)
    if any(not x.available for x in args): return NativeCell(None,'SOURCE_WRITTEN',taints(*args) or ('UNDEFINED_SOURCE_STATE',))
    try: result=fn(*(x.value for x in args))
    except OverflowError: result=float('inf')
    except (ValueError,ZeroDivisionError): result=float('nan')
    return NativeCell.from_float(result,taint=taints(*args))
def add(a,b): return calc(lambda x,y:x+y,a,b)
def sub(a,b): return calc(lambda x,y:x-y,a,b)
def mul(a,b): return calc(lambda x,y:x*y,a,b)
def div(a,b): return calc(lambda x,y:x/y,a,b)
def power(a,b): return calc(math.pow,a,b)
def sqrt(a): return calc(math.sqrt,a)
def compare(a,b,op):
    a,b=cell(a),cell(b)
    if not a.available or not b.available:return None
    return {'lt':lambda:a.value<b.value,'le':lambda:a.value<=b.value,'gt':lambda:a.value>b.value,
            'ge':lambda:a.value>=b.value,'eq':lambda:a.value==b.value,'ne':lambda:a.value!=b.value}[op]()
def controlled(value,*dependencies):
    value=cell(value)
    if any(not cell(d).available for d in dependencies):return NativeCell(None,'SOURCE_WRITTEN',taints(value,*dependencies))
    return NativeCell(value.bits,value.origin,taints(value,*dependencies))
def sequential_sum(values):
    result=cell(0.0)
    for value in values:result=add(result,value)
    return result


