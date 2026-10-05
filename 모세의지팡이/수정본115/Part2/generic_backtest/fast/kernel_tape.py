"""Cache the exact two-row numerical export needed by GenericRunCoordinator.

The original kernels execute on cold runs. Hot runs replace only their invoke
results; session validation, invocation sequence, re-entry trackers and row
projection still execute with the *current* owner, token and event IDs. Full
provider windows/checkpoints are deliberately not offered by this adapter.
"""
from ..canonical import plain
from ..contracts import GenericError
from pit.features.percentile.contracts import NativeCell,KernelResult


class ExportKernelTape:
    def __init__(self, kernel, stream):
        self.kernel=kernel;self.stream=stream;self.events=[];self.event_numbers={}
        self.state=kernel.state
        self.input_stream=None
        if getattr(stream.cache,'capture_inputs',False) and not stream.cache.hit:
            self.input_stream=stream.cache.stream(('PERCENTILE_INPUT',*stream.name[1:]))
            from calculations.bar_observations import BarObservationEncoder
            self.bar_encoder=BarObservationEncoder()

    def invoke(self,bars,event,*,captured_prestate=None,raw_input=None):
        if captured_prestate is not None or raw_input is not None:
            raise GenericError('E_CALC_CACHE_CORRUPT','only source-defined PIT exports are cacheable')
        number=len(self.events);self.events.append(event.calc_event_id)
        self.event_numbers[event.calc_event_id]=number
        expected=(event.R,event.P,event.source_ordinal,event.point)
        stored=self.stream.read(expected)
        if self.stream.cache.hit:
            def decode(cell):
                bits,origin,taint,ref=cell
                return NativeCell(bits,origin,tuple(taint),self.events[ref] if isinstance(ref,int) else ref)
            return KernelResult(event,stored['returned'],
                {k:tuple(decode(x) for x in row) for k,row in stored['buffers'].items()},(),stored['metadata'])
        if self.input_stream is not None:
            from calculations.state_codec import encode
            try:
                self.input_stream.append(expected,{'parameters':dict(self.kernel.profile.params),
                                                  'bar_delta':self.bar_encoder.capture(bars),'event':encode(event)})
            except (OSError,TypeError,ValueError,OverflowError) as exc:
                from data_warehouse.replay import ReplayMismatch
                raise ReplayMismatch('PERCENTILE_REQUEST_CAPTURE_FAILED: '+str(exc)) from exc
        result=self.kernel.invoke(bars,event,captured_prestate=None,raw_input=None)
        def encode(cell):
            ref=cell.last_write_event
            if ref is not None and ref not in self.event_numbers:
                raise GenericError('E_CALC_CACHE_CORRUPT','unbound cell write event')
            return [cell.bits,cell.origin,list(cell.taint),self.event_numbers.get(ref)]
        stored={'returned':result.returned,'metadata':plain(dict(result.metadata)),
            'buffers':{k:[encode(cell) for cell in row[:2]] for k,row in result.buffers.items()}}
        self.stream.append(expected,stored)
        return result
