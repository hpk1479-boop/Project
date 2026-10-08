"""Synthetic, point-in-time IPC fixtures. Not genuine raw or native READY bands."""
from dataclasses import replace
import math
from generic_backtest.contracts import TIMEFRAMES
from generic_backtest.canonical import plain
from generic_backtest.encoding_cache import BarPlainCache
from pit.models import BarState, AsOfToken

class Contexts:
    def __init__(self, count=120, rows=682, tfs=('1m','3m','6m'), *, features=True):
        self.count=count;self.rows=rows;self.tfs=tfs;self.features=features
        self.cache=BarPlainCache();self.previous={};self.start=1736726400*10**9
    def __iter__(self):
        from generic_backtest.watch.runtime import percentile_feature_name
        for i in range(self.count):
            now=self.start+i*15*10**9
            token=AsOfToken('IPC_SYNTHETIC',0,now,i,f'prefix-{i}','TEST_GRID','SYNTHETIC','test-view')
            history={}
            for tf in self.tfs:
                step=TIMEFRAMES[tf]*10**9;bucket=now//step;rows=[]
                old=self.previous.get(tf,{})
                for j in range(bucket-self.rows+1,bucket+1):
                    start=j*step;key=str(j);closed=j<bucket
                    b=old.get(key)
                    if b is None or not closed or b.state!='COMPLETED':
                        value=100+math.sin(j*.17)*2
                        b=BarState(f'{tf}:{j}','TEST',tf,start,start+step,value,value+1.,value-1.,
                            value+math.sin((i if not closed else j)*.3)*.7,20 if closed else 1+i%4,
                            0,i if not closed else max(0,i-1),f'bar-prefix-{j}',0 if closed else i,
                            'COMPLETED' if closed else 'FORMING')
                    rows.append(b)
                self.previous[tf]={str(b.open_ns//step):b for b in rows}
                history[tf]=self.cache.rows(rows)
            feature={}
            if self.features:
                for tf in self.tfs:
                    for family in ('PRICE','RSI','STO','DI'):
                        feature[percentile_feature_name(tf,family)]={
                            'family':family,'symbol':'TEST','timeframe':tf,'bar_time':history[tf][-1]['open_ns'],
                            'source_ordinal':i,'asof_token':plain(token),'seed_policy':'SOURCE_DEFINED_ONLY',
                            'parity_status':'UNDEFINED_SOURCE_STATE','strategy_state':{'staff_columns':{}},
                            'fields':{name:{'value':None,'validity':'UNAVAILABLE','unavailable_reason':'UNDEFINED_SOURCE_STATE'}
                                for name in ('strategy_value','lower','upper','basis','regime_upper','regime_lower')}}
            yield {'token':plain(token),'symbol':'TEST','quote':{'bid':100+math.sin(i*.1),'ask':100.1+math.sin(i*.1)},
                'history':history,'features':feature,'phase':'MEASURE','ready':True}
