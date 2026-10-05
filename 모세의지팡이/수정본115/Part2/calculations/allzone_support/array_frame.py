"""Read-only, observation-local NumPy access for validated OZ numeric frames.

The public boundary is STILL a Pandas DataFrame with the original validation and
retry signature. The source-ordered OZ rules receive scalar rows without making
new Pandas Series on every .iloc. Native numeric columns and bool/float object
state columns with naive datetime64 time take this path; other object/time-zone
frames retain the original implementation. No cross-observation mutable view is
cached, no formula/state transition/event version is changed.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

class ArrayRow:
    __slots__=('frame','position')
    def __init__(self,frame,position):self.frame=frame;self.position=position
    def get(self,name,default=None):
        a=self.frame.arrays.get(name)
        if a is None:return default
        if name=='time':return self.frame.timestamp(self.position)
        return a[self.position]

class ArrayRows:
    __slots__=('frame','cache')
    def __init__(self,frame):self.frame=frame;self.cache={}
    def __getitem__(self,index):
        if not isinstance(index,(int,np.integer)):
            raise TypeError('OZ scalar-only readonly row adapter')
        i=int(index)
        if i<0:i+=len(self.frame)
        if not 0<=i<len(self.frame):raise IndexError('row index out of bounds')
        row=self.cache.get(i)
        if row is None:
            row=ArrayRow(self.frame,i);self.cache[i]=row
        return row

class ArrayFrame:
    __slots__=('original','arrays','columns','_length','iloc','stamps','_times','_distances','dtype_names')
    def __init__(self,frame,arrays,dtype_names):
        self.original=frame;self.arrays=arrays;self.columns=tuple(arrays);self.dtype_names=dtype_names
        self._length=len(frame);self.iloc=ArrayRows(self)
        self.stamps=arrays['time'].astype('datetime64[ns]',copy=False).view('int64')
        self._times={};self._distances={}
    def __len__(self):return self._length
    @property
    def empty(self):return self._length==0
    def timestamp(self,index):
        t=self._times.get(index)
        if t is None:
            t=pd.Timestamp(self.arrays['time'][index]);self._times[index]=t
        return t
    def bars_since(self,event_time):
        if self.empty:return None
        target=pd.Timestamp(event_time)
        if pd.isna(target) or target.tzinfo is not None:return None
        if target in self._distances:return self._distances[target]
        try:ns=target.value
        except OverflowError:
            # Preserve Pandas' cross-resolution/out-of-ns-range comparison.
            from ..allzone import _OZRules
            return _OZRules._bars_since_event(self.original,event_time)
        # validate_frame already established strict chronological uniqueness.
        position=int(np.searchsorted(self.stamps,ns,side='left'))
        value=(len(self)-1-position) if position<len(self) and int(self.stamps[position])==ns else None
        self._distances[target]=value
        return value
    def one_way_reason(self,direction,hma_cross_time):
        if len(self)<4 or hma_cross_time is None:return None
        cross_time=pd.Timestamp(hma_cross_time)
        # Match original comparison and even timezone incompatibility errors.
        if any(self.timestamp(i)<=cross_time for i in range(len(self)-4,len(self)-1)):
            return None
        opens=self.arrays['open'][-4:-1];closes=self.arrays['close'][-4:-1]
        # Infinity is NOT rejected by the original pd.to_numeric branch.
        if np.isnan(opens).any() or np.isnan(closes).any():return None
        if direction=='LONG' and bool((closes>opens).all()):return 'ONE_WAY_UP'
        if direction=='SHORT' and bool((closes<opens).all()):return 'ONE_WAY_DOWN'
        return None


def from_validated_frame(frame):
    """Use only after validate_frame: unique columns, RangeIndex and ordered time."""
    if type(frame) is not pd.DataFrame:return frame
    dtypes=frame.dtypes
    time_dtype=dtypes.get('time')
    if not isinstance(time_dtype,np.dtype) or time_dtype.kind!='M':return frame
    for column,dtype in zip(frame.columns,dtypes):
        if column=='time':continue
        if not isinstance(dtype,np.dtype):return frame
        if dtype.kind in 'biuf':continue
        # Preserve bool/NaN state scalars without disabling NumPy row access.
        # Price arrays used by vector arithmetic must remain native numeric.
        if dtype.kind!='O' or column in ('open','high','low','close','volume'):return frame
        if not all(isinstance(v,(bool,np.bool_,float,np.floating)) for v in frame[column].to_numpy(copy=False)):
            return frame
    # Pandas 2.2.3 / 3.0.1 expose this read-only internal column hook.
    # It avoids constructing a Series (and copying attrs) for each column.
    # Check every exported dtype/shape; unsupported managers use the public
    # to_numpy path instead. Views are reacquired EVERY observation, so CoW
    # block replacement cannot leave a stale cross-observation column cache.
    getter=getattr(frame,'_get_column_array',None)
    arrays={}
    for i,(column,dtype) in enumerate(zip(frame.columns,dtypes)):
        a=None
        if callable(getter):
            try:
                candidate=np.asarray(getter(i))
                if candidate.ndim==1 and len(candidate)==len(frame) and candidate.dtype==dtype:
                    a=candidate
            except (AttributeError,TypeError,ValueError,NotImplementedError):
                pass
        if a is None:a=frame[column].to_numpy(copy=False)
        a=a.view();a.flags.writeable=False  # NEVER change the owner's flags/data
        arrays[column]=a
    return ArrayFrame(frame,arrays,tuple(str(x) for x in dtypes))
