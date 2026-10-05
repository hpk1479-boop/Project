"""Timing stays enabled; lazy storage must not change output or error handling."""
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]
from event_backtest import instrumentation as module
from event_engine.model import Signal, TimerRequest


def clock(monkeypatch, ticks):
    source = iter(ticks)
    monkeypatch.setattr(module, 'time', SimpleNamespace(perf_counter_ns=lambda: next(source)))


class Writer:
    def __init__(self):
        self.notes = []
    def note_emission(self, name, event, value):
        self.notes.append((name, event, value))


def test_lazy_entries_and_first_invocation_order(monkeypatch):
    clock(monkeypatch, [10, 25, 30, 50, 60, 90])
    rows, writer = {}, Writer()
    first = module.measure_consumer(lambda *a: 'first', 'A', writer, rows)
    second = module.measure_consumer(lambda *a: 'second', 'B', writer, rows)
    module.measure_consumer(lambda *a: None, 'never', writer, rows)
    assert rows == {}
    assert second(1, 2, 3) == 'second'
    assert first(1, 2, 3) == 'first'
    assert second(1, 2, 3) == 'second'
    assert list(rows) == ['B', 'A']
    assert rows == {'B': {'ns': 45, 'calls': 2}, 'A': {'ns': 20, 'calls': 1}}


def test_one_accumulator_lookup_for_repeated_calls(monkeypatch):
    class Counting(dict):
        lookups = 0
        def setdefault(self, *a):
            self.lookups += 1
            return super().setdefault(*a)
    clock(monkeypatch, range(200))
    rows = Counting()
    measured = module.measure_consumer(lambda *a: None, 'P', Writer(), rows)
    for _ in range(100):
        measured(None, None, None)
    assert rows.lookups == 1
    assert rows['P'] == {'calls': 100, 'ns': 100}


def test_three_argument_return_and_keyword_identity(monkeypatch):
    clock(monkeypatch, [1, 5])
    objects = [object() for _ in range(5)]
    calls, writer, rows = [], Writer(), {}
    def original(*args, **kwargs):
        calls.append((args, kwargs))
        return objects[4]
    fn = module.measure_consumer(original, 'P', writer, rows)
    assert fn(*objects[:3], extra=objects[3]) is objects[4]
    assert all(a is b for a, b in zip(calls[0][0], objects[:3]))
    assert calls[0][1]['extra'] is objects[3]
    assert writer.notes == [] and rows['P'] == {'ns': 4, 'calls': 1}


@pytest.mark.parametrize('arity', [3, 4])
def test_original_exception_is_preserved_and_counted(monkeypatch, arity):
    clock(monkeypatch, [100, 160])
    error, rows = RuntimeError('original exception'), {}
    def original(*args):
        raise error
    fn = module.measure_consumer(original, 'P', Writer(), rows)
    with pytest.raises(RuntimeError) as exc:
        fn(*([None] * arity))
    assert exc.value is error
    assert rows['P'] == {'ns': 60, 'calls': 1}


def test_context_precedes_each_emit_and_values_are_not_replaced(monkeypatch):
    clock(monkeypatch, [10, 20])
    writer, rows, order = Writer(), {}, []
    event, board, state = object(), object(), {}
    values = [Signal('X', 'one', {'type': 'NOTIFICATION'}), TimerRequest('X', 2000),
              Signal('X', 'two', {'type': 'WATCH_COMMAND'}, strategy='OTHER')]
    def original(e, b, s, emit, *, option):
        assert (e, b, s) == (event, board, state) and option == 7
        for value in values:
            emit(value)
        return 'done'
    def emit(value):
        assert writer.notes[-1] == ('P', event, value)
        assert writer.notes[-1][2] is value
        order.append(value)
    fn = module.measure_consumer(original, 'P', writer, rows)
    assert fn(event, board, state, emit, option=7) == 'done'
    assert all(a is b for a, b in zip(order, values))
    assert len(writer.notes) == 3 and rows['P']['calls'] == 1


@pytest.mark.parametrize('location', ['note', 'emit'])
def test_emission_failure_not_swallowed_or_skipped(monkeypatch, location):
    clock(monkeypatch, [10, 40])
    error, trace, rows = ValueError(location), [], {}
    def note(*args):
        trace.append('note')
        if location == 'note':
            raise error
    def emit(value):
        trace.append('emit')
        raise error
    fn = module.measure_consumer(lambda e, b, s, output: output('value'), 'P',
                                 SimpleNamespace(note_emission=note), rows)
    with pytest.raises(ValueError) as exc:
        fn(None, None, None, emit)
    assert exc.value is error
    assert trace == (['note'] if location == 'note' else ['note', 'emit'])
    assert rows['P'] == {'ns': 30, 'calls': 1}


def test_name_binding_preexisting_totals_and_independent_runs(monkeypatch):
    clock(monkeypatch, range(10, 22))
    writer, rows = Writer(), {'same': {'ns': 9, 'calls': 4}}
    first = module.measure_consumer(lambda *a: None, 'same', writer, rows)
    second = module.measure_consumer(lambda *a: None, 'same', writer, rows)
    independent = {}
    third = module.measure_consumer(lambda *a: None, 'same', writer, independent)
    for fn in (first, second, first, second, third, third):
        fn(None, None, None)
    assert rows['same'] == {'ns': 13, 'calls': 8}
    assert independent['same'] == {'ns': 2, 'calls': 2}


def test_keyword_only_invocation_keeps_original_forwarding(monkeypatch):
    clock(monkeypatch, [10, 20])
    writer, emitted, rows = Writer(), [], {}
    def original(*, event, board, state, emit):
        emit(event)
        return state
    state = {}
    fn = module.measure_consumer(original, 'P', writer, rows)
    assert fn(event=1, board=2, state=state, emit=emitted.append) is state
    assert emitted == [1] and writer.notes == []


def test_two_clock_reads_remain_enabled_for_every_call(monkeypatch):
    ticks = []
    def now():
        ticks.append(len(ticks))
        return len(ticks)
    monkeypatch.setattr(module, 'time', SimpleNamespace(perf_counter_ns=now))
    rows = {}
    fn = module.measure_consumer(lambda *a: None, 'P', Writer(), rows)
    for _ in range(10):
        fn(1, 2, 3)
    assert len(ticks) == 20 and rows['P'] == {'ns': 10, 'calls': 10}


def test_factory_loop_binds_each_consumer_and_emission_name(monkeypatch):
    clock(monkeypatch, range(8))
    writer, rows, calls = Writer(), {}, []
    functions = []
    for name in ['P1', 'P2', 'S1', 'S2']:
        def original(e, b, s, emit, name=name):
            calls.append(name)
            emit(name)
        functions.append(module.measure_consumer(original, name, writer, rows))
    for fn in functions:
        fn(None, None, None, lambda value: None)
    assert calls == ['P1', 'P2', 'S1', 'S2']
    assert [n for n, _, _ in writer.notes] == calls
    assert list(rows) == calls
