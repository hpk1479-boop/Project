from pathlib import Path
import sys,struct
import numpy as np
import pandas as pd
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from calculations.allzone_support.array_frame import ArrayFrame,from_validated_frame
from calculations.allzone_support.inputs import frame_signature,exact_digest
from calculations.allzone_runtime import HistoricalOZEngine
from pit.models import AsOfToken


def frame():
    f=pd.DataFrame({'time':pd.to_datetime(np.arange(12)*60,unit='s'),
        'open':np.full(12,100.),'high':np.full(12,102.),'low':np.full(12,98.),
        'close':np.full(12,101.),'volume':np.ones(12,dtype=np.int64),
        'hma_6':np.full(12,101.),'hma_17':np.full(12,100.)})
    f['state']=pd.Series([np.nan]*11+[True],dtype=object)
    f.attrs['symbol']='TEST'
    return f


def test_bool_nan_frame_uses_readonly_array_and_preserves_scalar_types():
    f=frame();a=from_validated_frame(f)
    assert isinstance(a,ArrayFrame)
    assert a.iloc[-1].get('state') is True
    assert np.isnan(a.iloc[0].get('state'))
    assert not a.arrays['state'].flags.writeable
    assert frame_signature(f)==frame_signature(f,column_arrays=a.arrays,dtype_names=a.dtype_names)
    f['state']=pd.Series([np.nan]*11+[False],dtype=object)
    assert from_validated_frame(f).iloc[-1].get('state') is False
    assert a.iloc[-1].get('state') is True # no stale views reused on replacement
    f['other']=['text']*12
    assert from_validated_frame(f) is f


@pytest.mark.parametrize('signature',[frame_signature])
def test_signature_preserves_types_nan_bits_and_all_cells(signature,monkeypatch):
    f=frame();original=signature(f)
    monkeypatch.setattr(pd.DataFrame,'to_dict',lambda *a,**k:pytest.fail('whole-frame row materialization'))
    assert signature(f.copy())==original
    for value in (False,1.0,0.0,-0.0,np.nan):
        changed=f.copy();changed.at[11,'state']=value
        assert signature(changed)!=original
    nan_a=f.copy();nan_b=f.copy()
    nan_b.at[0,'state']=struct.unpack('>d',bytes.fromhex('7ff8000000000001'))[0]
    assert signature(nan_a)!=signature(nan_b)
    changed=f.copy();changed.at[0,'open']=99.
    assert signature(changed)!=original


def test_numpy_and_reference_engines_keep_identical_state_and_events():
    reference=HistoricalOZEngine('TEST',use_numpy=False)
    fast=HistoricalOZEngine('TEST',use_numpy=True)
    from generic_backtest.watch.engines.inputs import frame_signature as watch_signature
    for i in range(8):
        f=frame();f['time']+=pd.Timedelta(minutes=i)
        f.at[11,'hma_6']=99. if i%2 else 101.
        f.at[11,'state']=bool(i%2)
        assert frame_signature(f)==watch_signature(f)
        now=(12+i)*60*10**9
        token=AsOfToken('E',i+1,now,i+1,str(i),'V','P',str(i))
        assert exact_digest(reference.observe({'1m':f},token))==exact_digest(fast.observe({'1m':f},token))
        assert exact_digest(reference.snapshot_state())==exact_digest(fast.snapshot_state())
    assert fast.numpy_stats['array_frames']==8
    assert fast.numpy_stats['reference_fallback_frames']==0
