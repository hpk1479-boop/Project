"""Single-thread deterministic domain loop; no output transport or persistence."""
from collections import deque
from copy import deepcopy
from threading import get_ident
from .model import Input, Kind, Signal, TimerRequest, signal_id, freeze
from .metrics import Metrics, measured_time
from .facts import EventFacts, validate_reference
from .board import Board
from .scheduler import Scheduler


class EventEngine:
    def __init__(self, ingress, strategies=(), processors=(), *, queue_warning=10000,
                 delay_warning_ms=1000, logger=None, retain_events=False, collect_timings=False, fact_parameters=None):
        self.ingress = ingress
        ingress.bind(self)
        self.strategies = tuple(strategies); self.processors = tuple(processors)
        names = [c.name for c in self.strategies + self.processors]
        if len(set(names)) != len(names):
            raise ValueError('consumer names must be unique')
        for consumer in self.strategies + self.processors:
            for name in consumer.subscriptions().facts:
                validate_reference(name, consumer.__class__.__module__)
        self.facts = EventFacts(fact_parameters); self.board = Board(self.facts); self.scheduler = Scheduler()
        self.strategy_state = {c.name: {} for c in self.strategies}
        self.processor_state = {c.name: {} for c in self.processors}
        self.failures = {c.name: 0 for c in self.strategies + self.processors}
        self.disabled = set(); self.status = 'HEALTHY'
        self.degraded = set()
        self.metrics = Metrics(); self.events = []; self.signals = []; self.error_log = []
        self._retain_events = retain_events; self._collect_timings = collect_timings
        self.queue_warning = queue_warning; self.delay_warning_ns = delay_warning_ms * 1000000
        self._logger = logger
        self._thread = None; self._running = False; self._internal = deque(); self._pressure = False
        self._configured = False
        self.signal_sink = None
        self.diagnostics = None
        self.retain_signals = True

    def _emit(self, strategy, event, output):
        if isinstance(output, TimerRequest):
            if output.symbol == event.payload.get('symbol') and output.due_time < event.source_time:
                raise ValueError('timer deadline precedes requesting event time')
            self.scheduler.request(strategy, output)
        elif isinstance(output, Signal):
            strategy = output.strategy or strategy
            self._internal.append(Input('strategy:' + strategy, None, event.source_time, Kind.SIGNAL,
                {'strategy': strategy, 'symbol': output.symbol, 'condition_key': output.condition_key,
                 'signal_id': signal_id(strategy, output.symbol, event.source_time, output.condition_key),
                 'content': output.content}, measured_time()))
        else:
            raise TypeError('emit accepts Signal or TimerRequest only')

    def _error(self, consumer, event, exc):
        self.failures[consumer.name] = self.failures.get(consumer.name, 0) + 1
        if self.diagnostics is not None:
            self.diagnostics.failure(consumer.name, event, exc)
        details = {'strategy': consumer.name, 'symbol': event.payload.get('symbol'),
                   'failed_engine_seq': event.engine_seq, 'source_time': event.source_time,
                   'error': type(exc).__name__ + ': ' + str(exc), 'consecutive': self.failures[consumer.name]}
        self.error_log.append(freeze(details))
        if self._logger is not None:
            self._logger(freeze(details))  # Host log sink only; no domain file access.
        if event.kind != Kind.STRATEGY_ERROR:
            self._internal.append(Input('engine', None, event.source_time, Kind.STRATEGY_ERROR, details, measured_time()))
        if self.failures[consumer.name] == 5:
            # Shared Composer still owns multiple SPECIALs in E2. The user
            # explicitly requires fail-open scheduling for this one boundary:
            # skip the failed event, alert once per consecutive error streak,
            # and continue. Individual consumers retain automatic suspension.
            if consumer in self.strategies and getattr(consumer,'stop_after_errors',True):
                self.disabled.add(consumer.name)
            self.degraded.add(consumer.name); self.status = 'DEGRADED'
            self._emit('engine', event, Signal(event.payload.get('symbol', ''), 'DEGRADED:' + consumer.name,
                {'strategy': consumer.name, 'status': 'DEGRADED', 'message': '이 전략의 조건은 현재 보장할 수 없음'}))

    @staticmethod
    def _accepts(consumer,event):
        if not consumer.subscriptions().accepts(event):return False
        select=getattr(consumer,'accepts_event',None)
        return True if select is None else select(event)

    def _process(self, item):
        self.ingress.number(self, item)
        event = self.ingress.take_ready(self)
        if self._retain_events:
            self.events.append(event)
        self.metrics.received(event)
        if self.diagnostics is not None and event.kind == Kind.MARKET_BUNDLE:
            self.diagnostics.touch("ENGINE")
        if event.kind == Kind.CONFIG:
            if self._configured or self.board._feeds:
                raise ValueError('CONFIG is startup-only')
            self._configured = True
        if event.kind == Kind.MARKET_BUNDLE:
            self.board.commit(event)
        elif event.kind == Kind.FEED_HEALTH:
            self.board.set_health(event)
        if event.kind == Kind.SIGNAL:
            if self.retain_signals:self.signals.append(event)
            if self.diagnostics is not None and event.payload.get('content',{}).get('type')=='NOTIFICATION':
                self.diagnostics.log(event.payload.get('strategy','ENGINE'),20,
                    '알림 생성 · %s · %s',event.payload.get('symbol',''),event.payload.get('signal_id',''))
            if self.signal_sink is not None:self.signal_sink(event)
        if event.kind == Kind.COMMAND and event.payload.get('command') == 'ENABLE_STRATEGY':
            name = event.payload['strategy']
            if name not in self.failures:
                raise ValueError('unknown strategy')
            self.disabled.discard(name); self.failures[name] = 0
            self.degraded.discard(name)
            self.status = 'DEGRADED' if self.degraded else 'HEALTHY'
        # Commit has already invalidated the pure Fact DAG. Resolve only the
        # facts actually read by an accepted consumer, inside its error boundary.
        # Declaring access is not a request to compute every TF on every SIGNAL.
        for processor in self.processors:
            try:
                if not self._accepts(processor,event):continue
                view = self.board.view(self.processor_state, processor,event.source_time)
                if self.diagnostics is not None:self.diagnostics.begin(processor.name)
                processor.on_event(event, view, self.processor_state[processor.name])
                self.board.processor_changed(processor.name)
            except Exception as exc:
                self._error(processor,event,exc)
            else:
                self.failures[processor.name]=0
                if self.diagnostics is not None:self.diagnostics.success(processor.name,event,self.processor_state[processor.name])
        for strategy in self.strategies:
            if strategy.name in self.disabled:continue
            try:
                accepted=self._accepts(strategy,event)
            except Exception as exc:
                self._error(strategy,event,exc);continue
            if not accepted:continue
            if event.kind == Kind.TIMER and event.payload['strategy'] != strategy.name:
                continue
            try:
                view = self.board.view(self.processor_state, strategy,event.source_time)
                if self.diagnostics is not None:self.diagnostics.begin(strategy.name)
                strategy.on_event(event, view, self.strategy_state[strategy.name],
                                  lambda output, s=strategy: self._emit(s.name, event, output))
            except Exception as exc:
                self._error(strategy, event, exc)
            else:
                self.failures[strategy.name] = 0
                if self.diagnostics is not None:self.diagnostics.success(strategy.name,event,self.strategy_state[strategy.name])

    def _dispatch(self, item):
        self._process(item)
        while self._internal:
            self._process(self._internal.popleft())

    def _timers(self, rows, item):
        for due, order, strategy, request in rows:
            self._dispatch(Input('scheduler', order, due, Kind.TIMER,
                {'symbol': request.symbol, 'strategy': strategy, 'key': request.key,
                 'request_order': order, 'payload': request.payload}, item.engine_time))

    def run(self):
        thread = get_ident()
        if self._running or self._thread not in (None, thread):
            raise RuntimeError('one non-reentrant engine processing thread required')
        self._thread = thread; self._running = True
        try:
            while True:
                self.metrics.queued(len(self.ingress))
                item = self.ingress.take_input(self)
                if item is None:
                    break
                delay = max(0, measured_time() - item.engine_time)
                pressure = self.metrics.queue_length > self.queue_warning or delay > self.delay_warning_ns
                if pressure and not self._pressure:
                    if self.diagnostics is not None:self.diagnostics.log("ENGINE",30,"엔진 처리 지연 · queue=%s delay_ms=%.1f",self.metrics.queue_length,delay/1e6)
                    self._dispatch(Input('engine', None, item.source_time, Kind.FEED_HEALTH,
                        {'warning': 'ENGINE_BACKLOG', 'queue_length': self.metrics.queue_length,
                         'delay_ns': delay}, item.engine_time))
                self._pressure = pressure
                if self.diagnostics is not None:self.diagnostics.backlog=pressure
                started = measured_time()
                if item.kind == Kind.MARKET_BUNDLE:
                    symbol = item.payload['symbol']; timestamp = item.source_time
                    advances = self.scheduler.advances(symbol, timestamp)
                    if advances:
                        self._timers(self.scheduler.due(symbol, timestamp, equal=False), item)
                    self._dispatch(item)
                    self.scheduler.commit(symbol, timestamp)
                    if advances:
                        self._timers(self.scheduler.due(symbol, timestamp, equal=True), item)
                    elapsed = measured_time() - started
                    self.metrics.bundle_count += 1; self.metrics.bundle_total_ns += elapsed
                    if self._collect_timings:
                        self.metrics.bundle_ns.append(elapsed)
                else:
                    self._dispatch(item)
        finally:
            self._running = False

    def checkpoint(self):
        if self._running or self._internal:
            raise RuntimeError('checkpoint requires an event boundary')
        return {'version': 1, 'seq': self.ingress.checkpoint(self), 'board': self.board.checkpoint(),
                'scheduler': self.scheduler.checkpoint(), 'strategies': deepcopy(self.strategy_state),
                'processors': deepcopy(self.processor_state), 'failures': dict(self.failures),
                'disabled': tuple(sorted(self.disabled)), 'degraded':tuple(sorted(self.degraded)),
                'status': self.status, 'configured': self._configured}

    def restore(self, checkpoint):
        if self._running or checkpoint['version'] != 1:
            raise ValueError('invalid checkpoint')
        if set(checkpoint['strategies']) != set(self.strategy_state) or set(checkpoint['processors']) != set(self.processor_state):
            raise ValueError('checkpoint consumer identity mismatch')
        self.ingress.restore(self, checkpoint['seq']); self.board.restore(checkpoint['board'])
        self.scheduler.restore(checkpoint['scheduler'])
        self.strategy_state = deepcopy(checkpoint['strategies']); self.processor_state = deepcopy(checkpoint['processors'])
        self.failures = dict(checkpoint['failures']); self.disabled = set(checkpoint['disabled'])
        self.status = checkpoint['status']; self._configured = checkpoint['configured']
        self.degraded = set(checkpoint.get('degraded',checkpoint['disabled']))
