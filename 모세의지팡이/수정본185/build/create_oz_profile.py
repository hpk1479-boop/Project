"""Port the existing rule bodies; replace only input/time/checkpoint plumbing."""
from pathlib import Path
import ast,re
R=Path(__file__).resolve().parents[1];P=R/'Part1/program/oz_engine'
s=(R.parent/'수정본19/Part1/program/monitor_OZ.py').read_text('utf-8-sig');lines=s.splitlines(True)
cls=next(n for n in ast.parse(s).body if isinstance(n,ast.ClassDef) and n.name=='OZMonitor')
source=''.join(lines[cls.lineno-1:cls.end_lineno])
manual={
'__init__':'''    def __init__(self,symbol,config,telegram,watch,validation_mode='NORMAL',trigger_mode='OZ',*,source_time):
        self.symbol=symbol;self.config=dict(config);self.telegram=telegram;self.watch=watch
        self.validation_mode,self.trigger_mode=_resolve_oz_modes(validation_mode,trigger_mode)
        self._source_time=source_time;self.watch_generation=-1;self._in_cycle=False
        self.max_bars=int(config.get('MAX_BARS_AFTER_B0','10'))
        self.max_bars_after_hma_cross=int(config.get('MAX_BARS_AFTER_HMA_CROSS','7'))
        self.max_bars_after_outin=int(config.get('MAX_BARS_AFTER_OUTIN','7'))
        self.min_bars=int(config.get('MIN_BARS_AFTER_B0','1'))
        self.alert_long=as_bool(config.get('ALERT_LONG'),True);self.alert_short=as_bool(config.get('ALERT_SHORT'),True)
        self.base_tfs=list(TF_MAP)
        self.episodes={(tf,d,p):IndicatorEpisode() for tf in self.base_tfs for d in ('LONG','SHORT') for p in PERCENTILES}
        self.prev_states={};self.prev_bar_time={tf:None for tf in self.base_tfs}
        self.candidates={(tf,d):None for tf in self.base_tfs for d in ('LONG','SHORT')}
        self.percentile_candidates={(tf,d,p):None for tf in self.base_tfs for d in ('LONG','SHORT') for p in PERCENTILES}
        self.alert_keys=set();self.bootstrapped=set()
        self.environment_identities={(tf,d):frozenset() for tf in self.base_tfs for d in ('LONG','SHORT')}
        self.hma_cross_extremes={(tf,d):None for tf in self.base_tfs for d in ('LONG','SHORT')}
        self._resume_outin=set();self._resume_cross=set();self._resume_cross_seen={}
        self.checkpoint_name='oz_observed_'+identity(symbol,self.validation_mode,self.trigger_mode)[:24]+'.json'
''',
'restore_observed_checkpoint':'''    def restore_observed_checkpoint(self,payload):
        if payload.get('version')!=1:raise ValueError('Unsupported OZ checkpoint')
        for name in self._checkpoint_fields:setattr(self,name,decode(payload['observed'][name]))
        self._resume_outin=set(self.prev_states);self._resume_cross=set(self.base_tfs)
''',
'export_event_state':'''    def export_event_state(self):
        fields=self._checkpoint_fields+('watch_generation','_resume_outin','_resume_cross','_resume_cross_seen')
        return {name:encode(getattr(self,name)) for name in fields}
''',
'restore_event_state':'''    def restore_event_state(self,state):
        for name,value in state.items():setattr(self,name,decode(value))
''',
'_checkpoint':'',
'_fact':'''    def _fact(self,view,key,compute):
        return view.memo(key,compute)
''',
'_super_fact':'''    def _super_fact(self,view,percentile,direction,b0_time):
        return view.memo(('super',percentile,direction,b0_time),lambda:view.super_filter(percentile,direction,b0_time))
''',
'_hma_cross_observation':'''    @staticmethod
    def _hma_cross_observation(view):return view.hma_cross()
''',
'_reconstruct_pre_cross_extreme':'''    @staticmethod
    def _reconstruct_pre_cross_extreme(view,direction):return view.pre_cross_extreme(direction)
''',
'_one_way_reason':'''    @staticmethod
    def _one_way_reason(view,direction,cross_time):return view.one_way(direction,cross_time)
''',
'_breaker_bo_break_trigger':'''    @staticmethod
    def _breaker_bo_break_trigger(view,direction,level,cross_time):return view.bo_break(direction,level,cross_time)
''',
'_bars_since_event':'''    @staticmethod
    def _bars_since_event(view,event_time):return view.bars_since(event_time)
''',
'_candle_pullback_trigger':'''    @staticmethod
    def _candle_pullback_trigger(view,cross_time,direction):return view.candle_pullback(direction,cross_time)
''',
'_hma6_turn_pullback_trigger':'''    @staticmethod
    def _hma6_turn_pullback_trigger(view,cross_time,direction):return view.hma6_turn(direction,cross_time)
''',
'run_once':'''    def run_once(self,revision,watched_tfs,data,evaluate_tfs=None):
        self._in_cycle=True
        try:return self._run_once(revision,watched_tfs,data,self.base_tfs if evaluate_tfs is None else evaluate_tfs)
        finally:self._in_cycle=False
''',
}
chunks=source.splitlines(True);sub=ast.parse(source).body[0]
for n in reversed(sub.body):
    if isinstance(n,ast.FunctionDef) and n.name in manual:
        start=min([n.lineno]+[d.lineno for d in n.decorator_list])
        chunks[start-1:n.end_lineno]=[manual[n.name]]
source=''.join(chunks).replace('class OZMonitor:','class OZProfile:').replace('OZMonitor._row_time','OZProfile._row_time')
source=source.replace('    @_checkpoint_observation\n','')
source=re.sub(r'^\s*self\._checkpoint\(\).*\n','\n',source,flags=re.M)
source=source.replace('pd.Timestamp','epoch').replace('pd.DataFrame','OZMarketView').replace('pd.Series','MarketRow')
source=re.sub(r'(\w+)\.iloc\[(-?\d+)\]',r'\1.row(\2)',source)
source=source.replace('str(decision.true_b0_time)','timestamp_text(decision.true_b0_time)')
source=source.replace("self._source_time if getattr(self, '_source_time', None) is not None else time.time()","self._source_time")
source=source.replace('def _run_once(self, revision: int, watched_tfs: Iterable[str]):','def _run_once(self, revision: int, watched_tfs: Iterable[str], data, evaluate_tfs):')
source=source.replace('        data = self.client.request(self.symbol, required_tfs)\n        if not data:\n','        if not data or any(tf not in data for tf in required_tfs):\n')
source=source.replace('        for tf in self.base_tfs:\n            df = data.get(tf)','        for tf in evaluate_tfs:\n            df = data.get(tf)')
source=source.replace('        for tf in self.base_tfs:\n            if tf not in data:','        for tf in evaluate_tfs:\n            if tf not in data:')
assert 'pd.' not in source and '.iloc' not in source and 'self.client' not in source
head='''"""Persistent OZ profile. Existing decision flow/operators; NumPy view input."""
from __future__ import annotations
import logging
from typing import Optional,Iterable
from durable_protocol import identity
from .common import *
from .common import _resolve_oz_modes
from .market import OZMarketView,MarketRow
from .checkpoint import encode,decode

'''
(P/'profile.py').write_text(head+source+'\n',encoding='utf-8')
print('Profile generated',len(source.splitlines()),'lines; decision bodies retained')
