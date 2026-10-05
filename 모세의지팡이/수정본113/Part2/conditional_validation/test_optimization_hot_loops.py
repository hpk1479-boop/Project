"""Raw uint64/float64 and weighted accumulation equivalence guards."""
from dataclasses import fields
import math
import random
import struct
import sys

import numpy as np
import pytest

from generic_backtest import market,runner
from pit.models import TickRecord
from pit.features.hma_open import SourceHMAOpenKernel
from conditional_validation.optimization_reference import quote_values,BaselineHMAOpenKernel
from conditional_validation.optimization_hot_loop_benchmark import extract_role_tick_copy,original_role_tick_copy


def make_tick(raw,ordinal=1):
    return TickRecord('EXACT_HOT_LOOP',ordinal,1785978000,*raw[:3],2**63-1,
                      1785978000000+ordinal%3,10,raw[3])


def float_bits(value):return struct.pack('<d',value)


@pytest.mark.parametrize('bits',[0,1,2**63,0x7ff0000000000000,0xfff0000000000000,
                                  0x7ff8000000000001,0x7ff0000000000001,0xfff800000000017a,
                                  0x000fffffffffffff,0x0010000000000000,0x7fefffffffffffff])
def test_quote_ieee_special_bits_and_dict_order(bits):
    t=make_tick((bits,)*4);a=quote_values(t);b=market.quote_values(t)
    assert tuple(a)==tuple(b)
    for key in a:
        if isinstance(a[key],float):assert float_bits(a[key])==float_bits(b[key])
        else:assert type(a[key]) is type(b[key]) and a[key]==b[key]


def test_quote_random_uint64_payloads_exact():
    rng=random.Random(7608)
    for i in range(10000):
        raw=tuple(rng.getrandbits(64) for _ in range(4));t=make_tick(raw,i+1)
        a=quote_values(t);b=market.quote_values(t)
        for key in ('bid','ask','last','volume_real'):assert float_bits(a[key])==float_bits(b[key])
        for key in ('volume','flags','time_msc','time'):assert a[key]==b[key]


def test_role_tick_copy_actual_expression_raw_bits_order_and_immutability():
    fn=extract_role_tick_copy(runner);rng=random.Random(15498)
    for i in range(10000):
        t=make_tick(tuple(rng.getrandbits(64) for _ in range(4)),i+1)
        saved=t.to_bytes();state={'ordinal':rng.choice((0,1,2,10**6,2**63-1))}
        a=original_role_tick_copy(t,state);b=fn(t,state)
        assert a==b and a.to_bytes()==b.to_bytes()==saved
        assert a.tick_id==b.tick_id
        assert b is not t and t.source_ordinal==i+1 and t.to_bytes()==saved


def test_tick_record_shape_guard_for_future_model_edits():
    assert tuple(f.name for f in fields(TickRecord))==('stream_id','source_ordinal','time_sec',
        'bid_bits','ask_bits','last_bits','volume','time_msc','flags','volume_real_bits')


def equal_series(a,b):
    assert len(a)==len(b)
    for x,y in zip(a,b):
        if x is None:assert y is None
        else:assert float_bits(x)==float_bits(y)


@pytest.mark.parametrize('period',[6,17])
@pytest.mark.parametrize('kind',['list','numpy'])
def test_hma_full_series_warmup_nan_sentinel_and_float64(period,kind):
    rng=random.Random(7820)
    sequences=[[],[0.]*30,[-0.]*30,[1.]*50,
               [1000.+math.sin(i/8.) for i in range(682)],
               [rng.uniform(-1e100,1e100) for _ in range(700)],
               [math.nextafter(1.,0.),1.,math.nextafter(1.,math.inf)]*100,
               [1e308,-1e308,1.,-1.]*40]
    for invalid in (None,math.nan,math.inf,-math.inf,sys.float_info.max):
        row=[float(i) for i in range(70)];row[21]=invalid;sequences.append(row)
    for row in sequences:
        if kind=='numpy':row=np.asarray(row,dtype='float64') if None not in row else np.asarray(row,dtype='object')
        with np.errstate(all='ignore'):
            a=BaselineHMAOpenKernel.calculate(row,period);b=SourceHMAOpenKernel.calculate(row,period)
        equal_series(a,b)
        assert BaselineHMAOpenKernel.warmup(period)==SourceHMAOpenKernel.warmup(period)


def test_wma_window_start_and_weight_order():
    rows=[[float(i) for i in range(50)],[-0.]*50,[1e-310]*50,
          [None if i%7==0 else i*.1 for i in range(50)]]
    for row in rows:
        for length in (0,1,2,3,4,6,8,17,50,51):
            for idx in range(len(row)):
                a=BaselineHMAOpenKernel.wma_at(row,idx,length)
                b=SourceHMAOpenKernel.wma_at(row,idx,length)
                equal_series((a,),(b,))
