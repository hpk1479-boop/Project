"""RSI raw source + the same oscillator/band pipeline used by STO and DI.

The registered source implementation uses the supplied MetaQuotes RSI source
recurrence. Native-terminal certification receipts remain separate diagnostics;
they are not a special availability gate for this backtest family.
"""
from .base import SourceKernel
from ..contracts import NativeCell, PercentileError
from ..numeric import cell, add, sub, mul, div, controlled
from ..oscillator import OscillatorPipeline
from ..frozen_buffers import TrackedCells

RAW_SOURCE_VERSION = 'METAQUOTES_RSI_SOURCE_V1'


class RsiSourceKernel(SourceKernel):
    family = 'RSI'

    def __init__(self, profile=None):
        super().__init__(profile)
        # Recurrence state is owned by this kernel and included in the existing
        # exact checkpoint format. A new bar shifts it with every other buffer.
        self.state.buffers['_rsi_gain'] = TrackedCells()
        self.state.buffers['_rsi_loss'] = TrackedCells()

    @staticmethod
    def _gain_loss(change):
        if not change.available:
            return change, change
        gain = controlled(change if change.value > 0.0 else cell(0.0), change)
        loss = controlled(mul(-1.0, change) if change.value < 0.0 else cell(0.0), change)
        return gain, loss

    @staticmethod
    def _rsi(gain, loss):
        if not gain.available or not loss.available:
            return controlled(cell(0.0), gain, loss)
        if loss.value != 0.0:
            return sub(100.0, div(100.0, add(1.0, div(gain, loss))))
        return controlled(cell(100.0 if gain.value != 0.0 else 50.0), gain, loss)

    def _source_raw(self):
        """Execute source order, seeded mean then Wilder recurrence, on this prefix.

        Full initialization and P=0 history resets use only existing closes.
        Subsequent calls recompute the preceding forming row plus new rows;
        neither a same-bar update nor a checkpoint restart needs future data.
        """
        state = self.state
        r, p = self.event.R, self.event.P
        length = self.profile.params['RSILength']
        state.allocate_raw(r)
        pos = p - 1
        if pos <= length:
            gain = loss = cell(0.0)
            for index in range(length + 1):
                shift = r - 1 - index
                for name in ('raw', '_rsi_gain', '_rsi_loss'):
                    state.write(name, shift, 0.0, 'rsi_source_seed')
                if index:
                    change = sub(self.price('close', shift), self.price('close', shift + 1))
                    up, down = self._gain_loss(change)
                    gain, loss = add(gain, up), add(loss, down)
            gain, loss = div(gain, length), div(loss, length)
            shift = r - 1 - length
            state.write('_rsi_gain', shift, gain, 'rsi_source_seed')
            state.write('_rsi_loss', shift, loss, 'rsi_source_seed')
            state.write('raw', shift, self._rsi(gain, loss), 'rsi_source_seed')
            pos = length + 1
        for index in range(pos, r):
            shift = r - 1 - index
            change = sub(self.price('close', shift), self.price('close', shift + 1))
            up, down = self._gain_loss(change)
            # Keep multiply, add, divide order identical to the supplied source.
            gain = div(add(mul(state.read('_rsi_gain', shift + 1), length - 1), up), length)
            loss = div(add(mul(state.read('_rsi_loss', shift + 1), length - 1), down), length)
            state.write('_rsi_gain', shift, gain, 'rsi_source_recurrence')
            state.write('_rsi_loss', shift, loss, 'rsi_source_recurrence')
            state.write('raw', shift, self._rsi(gain, loss), 'rsi_source_recurrence')

    def invoke(self, bars, event, captured_prestate=None, raw_input=None):
        plan = self._begin(bars, event)
        state = self.state
        state.capture(captured_prestate, self.profile.seed_policy)
        if captured_prestate is not None:self._band_window=None
        if not plan.ready:
            return self._result(0, plan)
        if event.R > state.allocated:
            state.allocated = event.R + 1024
            state.allocate_raw(state.allocated)
        requested = event.R if event.P == 0 else plan.limit + int((self.profile.params['Vibration'] - 1) / 2) + 2
        if raw_input is None:
            self._source_raw()
            OscillatorPipeline.execute(self, plan)
            return self._result(event.R, plan, {
                'raw_status': 'SOURCE_DEFINED', 'raw_implementation': RAW_SOURCE_VERSION,
                'registration': 'REGISTERED', 'raw_input_kind': 'PIT_CLOSE_SOURCE',
            })

        # Explicit native tapes/candidates remain diagnostics. No injected value
        # is relabeled as certified data or has an existing taint removed.
        values = tuple(raw_input.cells if hasattr(raw_input, 'cells') else raw_input)
        copied = getattr(raw_input, 'copied', min(requested, len(values)))
        if copied <= 0:
            return self._result(0, plan, {'raw_status': 'NATIVE_COPY_UNAVAILABLE',
                'copy_requested': requested, 'copy_copied': copied})
        array_size = getattr(raw_input, 'array_size', len(values))
        state.allocate_raw(array_size)
        for index, value in enumerate(values[:array_size]):
            value = cell(value)
            value = NativeCell(value.bits, value.origin,
                tuple(sorted(set(value.taint + ('NATIVE_RAW_DIAGNOSTIC',)))), value.last_write_event)
            state.write('raw', index, value, 'native_raw_copy')
        largest_raw_read = plan.limit + int((self.profile.params['Vibration'] - 1) / 2)
        if plan.limit >= 0 and largest_raw_read >= array_size:
            raise PercentileError('E_COPY_PARTIAL_UNDEFINED',
                f'source raw[{largest_raw_read}], actual ArraySize={array_size}, copied={copied}, requested={requested}')
        OscillatorPipeline.execute(self, plan)
        return self._result(event.R, plan, {'raw_status': 'NATIVE_RAW_DIAGNOSTIC',
            'copy_requested': requested, 'copy_copied': copied, 'copy_partial': copied < requested})
