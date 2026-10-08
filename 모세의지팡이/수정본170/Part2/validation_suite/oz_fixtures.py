"""Deterministic point-in-time OZ component inputs, NOT market raw/native bands.

Frames deliberately supply finite percentile/regime/ATR fixtures to exercise the
state machine independently of the original SOURCE_DEFINED_ONLY seed blocker.
Only the current row changes intra-bar; no observation contains a future row.
"""
from __future__ import annotations
import math
import numpy as np
import pandas as pd
from pit.models import AsOfToken
from generic_backtest.contracts import TIMEFRAMES
from generic_backtest.watch.engines.oz import OZEnvironment

SPECS={'RSI':('RSI_val','RSI_db','RSI_ub','RSI_regime_slope','RSI_regime_zone'),
       'STO':('STO_val','STO_db','STO_ub','STO_regime_slope','STO_regime_zone'),
       'DI':('DI_val','DI_db','DI_ub','DI_regime_slope','DI_regime_zone'),
       'PRICE':('price_hma_6','price_band_lower','price_band_upper','price_regime_slope','price_regime_zone')}
PROFILES=(('NORMAL','OZ'),('BLIND','OZ'),('NORMAL','BREAKER'),('BLIND','BREAKER'),
          ('NORMAL','REGIME'),('NORMAL','BREAKER_REGIME'))

class Sequence:
    def __init__(self, *, count=1000, rows=96, tfs=('1m','3m','6m'), step_seconds=15, scenario='oscillating'):
        self.count=count;self.rows=rows;self.tfs=tfs;self.step_seconds=step_seconds;self.scenario=scenario
        self.start_ns=pd.Timestamp('2025-01-13').value
        self.frames={};self.bucket={}

    def values(self, indexes, tf):
        x=np.asarray(indexes,dtype='float64')
        phase=(TIMEFRAMES[tf]//60)*.13
        center=100.+5*np.sin(x*.47+phase)
        # Alternating runs also exercise exact equal prices and one-way rules.
        opens=center+np.cos(x*.71)*1.4
        closes=opens+np.sin(x*.8)*1.2
        v={'open':opens,'high':np.maximum(opens,closes)+1.7,'low':np.minimum(opens,closes)-1.7,
           'close':closes,'volume':(30+(np.asarray(indexes)%10)).astype('int64'),
           'hma_6':100.+3*np.sin(x*.36+phase),'hma_17':np.full(len(x),100.),
           'hma_50':100.+2*np.sin(x*.17+phase),'hma_168':np.full(len(x),100.),
           'atr_14':np.full(len(x),4.),'wonbi_lower':center-1.,'wonbi_upper':center+1.}
        for i,(val,lo,hi,slope,zone) in enumerate(SPECS.values()):
            v[val]=50.+18*np.sin(x*.69+phase+i*.32)
            v[lo]=np.full(len(x),40.);v[hi]=np.full(len(x),60.)
            v[slope]=np.sin(x*.11+phase+i*.32)
            v[zone]=np.zeros(len(x))
        return v

    def __iter__(self):
        for i in range(self.count):
            now=self.start_ns+i*self.step_seconds*1_000_000_000
            for tf in self.tfs:
                period=TIMEFRAMES[tf]*1_000_000_000
                bucket=now//period
                # Small-relative, increasing bar indices independent of ns magnitude.
                idx=int((bucket*period-self.start_ns)//period)
                if self.bucket.get(tf)!=bucket:
                    xs=np.arange(idx-self.rows+1,idx+1,dtype='int64')
                    values=self.values(xs,tf)
                    times=(bucket+np.arange(-self.rows+1,1,dtype='int64'))*period
                    values={'time':pd.to_datetime(times),**values}
                    self.frames[tf]=pd.DataFrame(values)
                    self.bucket[tf]=bucket
                df=self.frames[tf]
                progress=(now%period)/period
                live=self.values(np.array([idx]),tf)
                # Live values have no dependence on the next observation.
                live['close'][0]+=1.3*math.sin(progress*5.4)
                live['high'][0]=max(live['high'][0],live['close'][0])
                live['low'][0]=min(live['low'][0],live['close'][0])
                live['hma_6'][0]+=1.5*math.sin(progress*5)
                for j,(val,lo,hi,slope,zone) in enumerate(SPECS.values()):
                    live[val][0]=50.+18*math.sin(idx*.69+(TIMEFRAMES[tf]//60)*.13+j*.32+progress*2.5)
                    if self.scenario=='nan' and (i+j)%41==0:live[val][0]=float('nan')
                    if self.scenario=='equal' and (i+j)%11==0:live[val][0]=40. if i%2 else 60.
                for col,vals in live.items():df.iat[len(df)-1,df.columns.get_loc(col)]=vals[0]
                df.attrs.update(symbol='TEST',timeframe=tf,available_at_ns=now,source_ordinal=i,input_prefix=f'prefix-{i}')
            token=AsOfToken('oz-component',0,now,i,f'prefix-{i}','BOUNDARY_TEST','FIXTURE','view')
            env={}
            for tf in self.tfs:
                for direction in ('LONG','SHORT'):
                    active=i%173<150
                    env[(tf,direction)]=OZEnvironment(frozenset({'stable-environment'}) if active else frozenset(),
                        external_required=(i%97==0),external_qualified=False)
            yield self.frames,token,env,(i%37!=0)


def full_state(engine):
    return {'state':engine.snapshot_state(),
            'resume_outin':engine._resume_outin,'resume_cross':engine._resume_cross,
            'resume_cross_seen':engine._resume_cross_seen,
            'guard':(engine.guard.last_token,engine.guard.signature)}
