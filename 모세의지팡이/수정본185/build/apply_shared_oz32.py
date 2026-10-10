from pathlib import Path
import json, hashlib, shutil
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'검증결과/shared_oz_composer_input'
OUT.mkdir(parents=True,exist_ok=True)
inventory={}
for folder in ('Part1/program','Part2/event_backtest'):
    for p in (ROOT/folder).rglob('*.py'):
        if '__pycache__' in p.parts:continue
        name=p.relative_to(ROOT).as_posix()
        if name.endswith('composer_oz_dispatch.py'):continue
        inventory[name]=hashlib.sha256(p.read_bytes()).hexdigest()
(OUT/'before_sources.json').write_text(json.dumps(inventory,indent=2),encoding='utf-8')

def edit(name,fn):
    p=ROOT/name;b=p.read_bytes();s=b.decode('utf-8');nl='\r\n' if b'\r\n' in b else '\n'
    s=s.replace('\r\n','\n');new=fn(s)
    if new==s:raise ValueError('no edit: '+name)
    p.write_bytes(new.replace('\n',nl).encode('utf-8'))

edit('Part1/program/composer_oz_dispatch.py',lambda s:s.replace('from event_protocol import identity','from durable_protocol import identity'))
def domain(s):
    a=s.index('    def _dispatch_special_oz_event(');b=s.index('    @staticmethod',a)
    s=s[:a]+'''    def _dispatch_special_oz_event(self, event: dict) -> Optional[dict]:
        from composer_oz_dispatch import dispatch
        return dispatch(self, event)

'''+s[b:]
    needle='''        cancels: list[dict] = []
        with self._manager._lock:
            for wid in ids:
'''
    repl='''        transaction = getattr(self._manager, '_oz_dispatch_transaction', None)
        deferred = set(ids).intersection(transaction['ids']) if transaction else set()
        if deferred:
            transaction['pending'].update(deferred)
            ids = tuple(wid for wid in ids if wid not in deferred)
        cancels: list[dict] = []
        with self._manager._lock:
            for wid in ids:
'''
    assert needle in s;s=s.replace(needle,repl,1).replace('        return len(cancels)\n','        return len(cancels) + len(deferred)\n',1)
    a=s.index('    def _handle_oz_event(self, event: dict)');b=s.index('    def _handle_oz_event_core',a)
    s=s[:a]+'''    def _handle_oz_event(self, event: dict) -> dict:
        special_result = self._dispatch_special_oz_event(event)
        if special_result is not None:
            return special_result
        if str(event.get('kind', '')).upper() == 'FINAL_ALERT':
            event, expired = self._filter_config_chain_deadline_event(event)
            if expired:
                return {'ok':True,'delivered':True,'filtered':True,'expired':True}
            event, expired = self.watch_orchestrator.filter_deadline_event(event)
            if expired:
                return {'ok':True,'delivered':True,'filtered':True,'expired':True}
        return self._handle_oz_event_core(event)

'''+s[b:]
    a=s.index('            with self._lock:\n                active_changed = False',s.index('    def _handle_oz_event_core'))
    b=s.index('            self.watch_orchestrator.complete_final_oz',a)
    s=s[:a]+"            self.special_api.cancel_oz_watches(watch_ids)\n"+s[b:]
    # Core rendering must not select another SPECIAL through the shared child.
    needle='''        candidates: list[str] = []

        raw_ids = event.get("source_spec_ids")'''
    repl='''        candidates: list[str] = []
        if event.get('signal_strategy') and event.get('source_spec_id'):
            key = str(event['source_spec_id'])
            return self.official_specs.get(key) or self.official_chain_specs.get(key)

        raw_ids = event.get("source_spec_ids")'''
    assert needle in s;s=s.replace(needle,repl,1)
    return s
edit('Part1/program/event_composer_domain.py',domain)
edit('Part1/program/oz_engine/controllers.py',lambda s:s.replace("                    event_time=completion_time,", "                    market_event_id=identity('OZ_MARKET', alert_identity) if alert_identity else None,\n                    watch_sources={key: w.source_spec_id for key,w in zip(recipient_keys,recipient_specs) if w is not None},\n                    event_time=completion_time,",1))
def composition(s):
    s=s.replace('        raw=self.kernel.current_event or {}',"        context=getattr(getattr(self.kernel,'manager',None),'_delivery_context',None)\n        raw=getattr(context,'oz_event',None) or self.kernel.current_event or {}")
    s=s.replace("            'event_id':event_id,'message_token':token,'reply_to_message_id':reply_to_message_id})", "            'event_id':event_id,'message_token':token,'reply_to_message_id':reply_to_message_id,\n            **({k:raw.get(k) for k in ('signal_strategy','market_event_id','source_spec_id','b0_price','b0_time','grade','validation_mode','trigger_mode','trigger_name')} if raw.get('signal_strategy') else {})})")
    return s
edit('Part1/program/event_composition.py',composition)
edit('Part1/program/event_engine/model.py',lambda s:s.replace('    condition_key: str\n    content: object','    condition_key: str\n    content: object\n    strategy: str | None = None',1))
edit('Part1/program/event_engine/engine.py',lambda s:s.replace("        elif isinstance(output, Signal):\n            self._internal", "        elif isinstance(output, Signal):\n            strategy = output.strategy or strategy\n            self._internal",1))
edit('Part1/program/event_engine/composition_consumer.py',lambda s:s.replace("emit(Signal(symbol,key,{'type':'NOTIFICATION',**plain(message)}))", "emit(Signal(symbol,key,{'type':'NOTIFICATION',**plain(message)},strategy=message.get('signal_strategy')))"))
edit('Part2/event_backtest/warehouse.py',lambda s:s.replace('signal_id(consumer,output.symbol,event.source_time,output.condition_key)','signal_id(output.strategy or consumer,output.symbol,event.source_time,output.condition_key)').replace("'strategy':raw.get('strategy',consumer)","'strategy':output.strategy or raw.get('strategy',consumer)").replace("'strategy':'SPECIAL'+special[1] if special else context.get('strategy',p['strategy']),", "'strategy':c.get('signal_strategy') or ('SPECIAL'+special[1] if special else context.get('strategy',p['strategy'])),"))
