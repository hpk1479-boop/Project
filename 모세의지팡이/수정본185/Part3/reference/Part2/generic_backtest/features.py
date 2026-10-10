"""Optional source providers only; no automatic feature construction."""
from .canonical import plain, identity
from .contracts import GenericError
import struct


class OptionalFeatureRegistry:
    def __init__(self, requirements, owner, *, replay_cache=None):
        self.specs = requirements.features
        self.owner = owner
        self.replay_cache = replay_cache
        self.providers = {}
        names = [s['name'] for s in self.specs]
        if len(set(names)) != len(names): raise GenericError('E_PLUGIN_SCHEMA', 'duplicate feature name')
        # bind_bundle already constructs all four families. Equal requests
        # within this private registry must not build/advance that bundle four
        # times. Include the complete input map, not just the requested family.
        self._group_keys = {s['name']:(s['timeframe'],identity(s.get('params') or {}))
                            for s in self.specs if s['kind']=='MOSES_PERCENTILE'}
        self._groups = {}
        self._hma_cache = {}
        self._latest_groups = {}
        self._latest_values = {}
        from pit.features.percentile.digest_cache import MarketDigestCache
        self._market_digest_cache = MarketDigestCache()

    def advance(self, view, change, *, materialize=True):
        result = {}
        current_groups = {}  # Only this observation; never reuse a past token.
        for spec in self.specs:
            tf = spec['timeframe']
            if spec['kind'] == 'HMA_OPEN':
                from pit.features.hma_open import SourceHMAOpenKernel
                bars = view.bars(tf)
                n = spec['period']; window = SourceHMAOpenKernel.warmup(n)
                tail = bars[-window:]
                valid = len(tail)==window and all(b.quality=='COMPLETE_PREFIX' for b in tail) and not any(b.gap_before for b in tail[1:])
                # The newest HMA depends only on this exact source window.
                # The source kernel (including order/EMPTY handling) is unchanged.
                values = tuple(b.open if b.quality=='COMPLETE_PREFIX' else None for b in tail)
                input_key = (n, valid, tuple(None if v is None else struct.pack('<d', v) for v in values))
                tape = self.replay_cache.stream(('HMA_OPEN',tf,n,spec['name'])) if self.replay_cache else None
                expected = (view.token.source_ordinal, view.token.now_ns)
                stored = tape.read(expected) if tape else None
                if tape and tape.cache.hit:
                    result[spec['name']] = stored
                    continue
                cached = self._hma_cache.get(spec['name'])
                if cached is None or cached[0] != input_key:
                    output = SourceHMAOpenKernel.calculate(values, n)
                    cached = (input_key, output[-1] if valid and output else None)
                    self._hma_cache[spec['name']] = cached
                result[spec['name']] = {'value':cached[1],
                    'status':'SOURCE_DEFINED' if valid else 'UNAVAILABLE', 'source_hash':SourceHMAOpenKernel.source_hash}
                if tape:tape.append(expected,result[spec['name']])
            else:
                from pit.features.percentile.provider import PercentileFeatureProvider
                key = self._group_keys[spec['name']]
                if key not in self._groups:
                    p = PercentileFeatureProvider(materialization="DEMAND")
                    handles = p.bind_bundle(self.owner,view.symbol,tf,params=spec.get('params'),
                        seed='SOURCE_DEFINED_ONLY',calc_policy='PIT_TICK')
                    p.session(self.owner)._digest_cache = self._market_digest_cache
                    if self.replay_cache:
                        from .fast.kernel_tape import ExportKernelTape
                        session=p.session(self.owner)
                        for family,handle in handles.items():
                            tape=self.replay_cache.stream(('PERCENTILE',tf,key[1],family))
                            if tape:session._kernels[handle]=ExportKernelTape(session._kernels[handle],tape)
                    self._groups[key] = p,handles
                self.providers[spec['name']] = self._groups[key]
                p,handles = self.providers[spec['name']]
                if key not in current_groups:
                    current_groups[key] = p.on_market(self.owner,view,change,raw_input=None,captured_prestate=None)
                current = current_groups[key]
                if not materialize:continue
                try: result[spec['name']] = plain(current.read(handles[spec['family']]))
                except ValueError as exc:
                    if getattr(exc,'code',None)!='E_FEATURE_NOT_AVAILABLE': raise
                    result[spec['name']] = {'value':None,'status':'UNAVAILABLE'}
        self._latest_groups=current_groups
        self._latest_values=result
        return result

    def snapshot(self):
        """Immutable invocation views; no extra native OnCalculate call."""
        return dict(self._latest_groups),dict(self._latest_values)

    def export_snapshot(self,snapshot,token):
        from dataclasses import replace
        from pit.features.percentile.projection import RowProjector
        groups,values=snapshot
        result={}
        for spec in self.specs:
            if spec['kind']=='HMA_OPEN':
                result[spec['name']]=values[spec['name']]
                continue
            current=groups[self._group_keys[spec['name']]]
            p,handles=self.providers[spec['name']]
            rows=current._rows(handles[spec['family']])
            if not len(rows):
                result[spec['name']]={'value':None,'status':'UNAVAILABLE'}
                continue
            # Keep the true native calc event/sequence and frozen values. Only
            # the consumer knowledge token changes; do not invoke the kernel
            # or observed-boundary tracker again when the producer is unchanged.
            frame=replace(rows.frame,token=token)
            result[spec['name']]=plain(RowProjector.project_row(frame,len(frame.bars)-1))
        return result
