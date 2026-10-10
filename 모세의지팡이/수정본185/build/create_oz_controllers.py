from pathlib import Path
import ast,re
R=Path(__file__).resolve().parents[1];P=R/'Part1/program/oz_engine'
s=(R.parent/'수정본19/Part1/program/monitor_OZ.py').read_text('utf-8-sig');lines=s.splitlines(True);tree=ast.parse(s)
pieces=[]
for name in ('ExternalLiquidityController','OZWatchController'):
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==name)
    source=''.join(lines[cls.lineno-1:cls.end_lineno]);ls=source.splitlines(True);parsed=ast.parse(source).body[0]
    if name=='ExternalLiquidityController':
        replacements={'__init__':'''    def __init__(self,clock,*,sweep_registry,initial=None):
        self.clock=clock;self._lock=threading.RLock();self._event_registry=sweep_registry
        self._specs={};self._states={};self._invalidated={};self.revision=0;self._fingerprint=None
        self._state_path='oz_external_liquidity_state.json';self._initial=initial
''',
        '_event_epoch':'''    @staticmethod
    def _event_epoch(value):
        try:
            if isinstance(value,(int,float)) and not isinstance(value,bool):return float(value)
            return epoch(value)
        except (TypeError,ValueError,OverflowError):return None
''',
        '_current_sweep_registry':'''    def _current_sweep_registry(self,force=False):return dict(self._event_registry)
''',
        '_row_epochs':'''    @staticmethod
    def _row_epochs(view):return view.time
'''}
    else:
        replacements={'__init__':'''    def __init__(self,telegram,external,clock,*,initial=None):
        self.telegram=telegram;self.external=external;self.clock=clock;self._lock=threading.RLock()
        self._watches={};self._revision={_profile_key(*p):0 for p in PROFILE_KEYS}
        self._state_path='oz_manual_watch_state.json';self._initial=initial;self.dirty=False
'''}
    for n in reversed(parsed.body):
        if isinstance(n,ast.FunctionDef) and n.name in replacements:
            start=min([n.lineno]+[d.lineno for d in n.decorator_list])
            ls[start-1:n.end_lineno]=[replacements[n.name]]
    source=''.join(ls).replace('pd.DataFrame','OZMarketView')
    source=source.replace('        if not domain_memory.exists(self._state_path):','        if self._initial is None:')
    source=source.replace('raw = read_json(self._state_path)','raw = self._initial')
    source=source.replace('time.time_ns()','self.clock.identity_ns()').replace('time.time()','self.clock.seconds')
    source=re.sub(r'(\w+)\.iloc\[(-?\d+)\]',r'\1.row(\2)',source)
    if name=='ExternalLiquidityController':
        source=source.replace('    def _save_locked(self) -> None:','    def export_payload(self):')
        source=source.replace('        _atomic_write_json(self._state_path, payload)','        return payload')
        source=source.replace('                epochs = self._row_epochs(df)\n','')
        start=source.index('                        diff = (epochs - float(st.event_time)).abs()')
        end=source.index('                        atr = row.get(EXTERNAL_ATR_COLUMN)',start)
        source=source[:start]+'''                        idx = df.nearest_index(float(st.event_time),tolerance=.5)
                        if idx is None:
                            continue
                        row = df.row(idx)
'''+source[end:]
        source=source.replace('''                        mask = epochs >= float(st.event_time or 0.0)
                        segment = df.loc[mask]
                        if segment.empty or st.max_distance is None or st.level_price is None:''','''                        if st.max_distance is None or st.level_price is None:''')
        source=source.replace('''                            lows = pd.to_numeric(segment.get("low"), errors="coerce")
                            breached = (not lows.dropna().empty) and float(lows.min()) < level - float(st.max_distance)''','''                            low = df.extreme_since(float(st.event_time or 0.0),'low')
                            breached = low is not None and low < level - float(st.max_distance)''')
        source=source.replace('''                            highs = pd.to_numeric(segment.get("high"), errors="coerce")
                            breached = (not highs.dropna().empty) and float(highs.max()) > level + float(st.max_distance)''','''                            high = df.extreme_since(float(st.event_time or 0.0),'high')
                            breached = high is not None and high > level + float(st.max_distance)''')
        source+='''
    def watch_signature(self,wid):
        spec=self._specs.get(wid)
        states=tuple(sorted((key,tuple(vars(value).items())) for key,value in self._states.items() if value.watch_id==wid))
        return (None if spec is None else tuple(vars(spec).items()),states,self._invalidated.get(wid))

    def _save_locked(self):
        # Dirty bookkeeping uses scalar tuples, not JSON/profile serialization.
        signature=(tuple(sorted((key,tuple(vars(value).items())) for key,value in self._specs.items())),
                   tuple(sorted((key,tuple(vars(value).items())) for key,value in self._states.items())),tuple(sorted(self._invalidated.items())))
        if signature!=self._fingerprint:self.revision+=1;self._fingerprint=signature
'''
    else:
        source=source.replace('    def _save_state_locked(self) -> None:','    def export_payload(self):')
        source=source.replace('''        try:
            _atomic_write_json(self._state_path, payload)
        except Exception:
            logging.exception("[OZ Watch] 상태 저장 실패 | %s", self._state_path)''','''        return payload''')
        source+='''
    def _save_state_locked(self):self.dirty=True
'''
    assert not any(x in source for x in ('pd.','self._state_path.stat','time.time','_atomic_write_json','read_json(','domain_memory.')),(name,'unconverted boundary')
    pieces.append(source)
head='''"""Persistent Watch/external-liquidity objects; unchanged judgment/try_fire.
State encoding occurs on explicit host/engine save. Input is an OZMarketView.
"""
from __future__ import annotations
import logging,threading
from typing import Optional,Iterable
from durable_protocol import identity
import oz_profiles
from .common import *
from .common import _profile_key,_resolve_oz_modes,_oz_profile_label
from .market import OZMarketView

'''
(P/'controllers.py').write_text(head+'\n\n'.join(pieces)+'\n',encoding='utf-8')
print('Persistent controllers generated')
