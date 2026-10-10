"""Completed personal signals keep their own market context, entirely offline."""
from copy import deepcopy
from pathlib import Path
import socket
import sys
import threading
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part1/program'))
from domain_clock import event_scope
from domain_memory import memory_scope
from watch_orchestrator import ChainTriggerSpec, TimedChainSpec, WatchOrchestrator
from event_composition import NotificationPort

BASE = 1800000000.


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('Watch entry context tests cannot access the network')
    monkeypatch.setattr(socket, 'create_connection', deny)
    for name in ('connect', 'connect_ex', 'sendto'):
        monkeypatch.setattr(socket.socket, name, deny)


class Run:
    def __init__(self, tmp_path, *, memory=None, quote=None):
        self.memory = memory if memory is not None else {}
        self.clock = {}
        self.pushes = []
        self.notices = []
        self.context_reads = []
        self.deliver = True
        self.quote = quote if quote is not None else {
            'symbol': 'TEST', 'direction': 'LONG', 'signal_tf': '5m', 'source_tf': '5m',
            'current_price': 101.25, 'event_time': BASE + 2,
        }
        self.state = WatchOrchestrator(
            lock=threading.RLock(), state_path=tmp_path / 'chains.json', config={},
            push_callback=self.pushes.append, notify_callback=self.notify,
            mark_dirty_callback=lambda: None, signal_context_callback=self.context,
        )

    def notify(self, text, recipient, **kwargs):
        self.notices.append((text, recipient, deepcopy(kwargs)))
        return self.deliver

    def context(self, chain, event):
        self.context_reads.append((chain.chain_id, deepcopy(event)))
        return self.quote

    def call(self, name, *args, now=BASE + 2):
        with event_scope(int(now * 1000), 'watch113-test', self.clock), memory_scope(self.memory):
            return getattr(self.state, name)(*args)

    def hit(self, chain, index, *, now=BASE + 2, **extra):
        wid = chain.current_watch_id if chain.order_mode == 'SEQUENTIAL' else chain.current_watch_ids[str(index)]
        event = {'kind': 'GENERIC_TRIGGER', 'chain_id': chain.chain_id, 'watch_id': wid,
                 'chain_stage': index, 'direction': 'LONG', 'source_tf': chain.triggers[index].tf,
                 'event_time': now, 'event_id': f'hit:{index}:{now}', **extra}
        return self.call('handle_generic_trigger', event, now=now)


def chain(order='SEQUENTIAL', *, triggers=None, **kwargs):
    if triggers is None:
        triggers = tuple(ChainTriggerSpec('BAR_CLOSE', tf, valid_sec=60 if order == 'FILTER' else None)
                         for tf in ('1h', '5m'))
    value = TimedChainSpec('CHAIN:ENTRY113', 'USER', 'TEST', triggers,
                          final_action='NOTIFY', order_mode=order, created_at=BASE, **kwargs)
    value.validate()
    return value


@pytest.mark.parametrize('order', ('SEQUENTIAL', 'UNORDERED', 'FILTER'))
def test_only_complete_condition_set_carries_entry_context(tmp_path, order):
    run = Run(tmp_path)
    value = chain(order)
    run.call('add_chain', value)
    assert run.notices[-1][2]['signal_context'] == {}
    run.hit(value, 0, now=BASE + 1)
    assert value.chain_id in run.state.chains
    assert run.notices[-1][2]['signal_context'] == {}
    assert not run.context_reads
    run.hit(value, 1)
    assert value.chain_id not in run.state.chains
    assert len(run.context_reads) == 1
    text, recipient, kw = run.notices[-1]
    assert recipient == 'USER'
    assert kw['watch_id'] == value.chain_id
    assert kw['event_id']
    context = kw['signal_context']
    assert context['signal_source'] == 'SIGNAL'
    assert context['direction'] == 'LONG'
    assert context['signal_tf'] == '5m'
    assert context['current_price'] == 101.25
    assert context['event_time'] == BASE + 2
    assert not any(key in context for key in ('b0_price', 'neckline_price'))
    assert not run.state.pending_notifications
    if order == 'FILTER':
        assert text == '✅ 조건 유효시간 필터 성립'
    elif order == 'UNORDERED':
        assert text == '✅ 순서무관 조건 성립 · 2/2'


def oz_event(wid, now=BASE + 1):
    return {'kind': 'FINAL_ALERT', 'strategy': 'OZ', 'watch_ids': [wid],
            'symbol': 'TEST', 'direction': 'LONG', 'source_tf': '1m',
            'event_time': now, 'event_id': 'oz:original', 'message': 'original OZ alert',
            'current_price': 100.5, 'b0_price': 98., 'b0_time': BASE - 120,
            'neckline_price': 103., 'neckline_time_ms': int((BASE - 60) * 1000)}


def test_final_oz_completion_keeps_original_pattern_through_retry_and_restore(tmp_path):
    run = Run(tmp_path)
    value = chain(triggers=(ChainTriggerSpec('OZ_ALERT', '1m'),))
    run.call('add_chain', value)
    event = oz_event(value.current_watch_id)
    wanted = deepcopy(event)
    run.deliver = False
    result = run.call('handle_oz_stage_event', event, now=BASE + 1)
    assert result['handled'] and result['all_internal']
    assert value.chain_id not in run.state.chains
    assert len(run.state.pending_notifications) == 1
    assert not run.context_reads
    original = run.notices[-1]
    context = original[2]['signal_context']
    assert original[:2] == ('original OZ alert', 'USER')
    assert context['signal_source'] == 'OZ' and context['signal_tf'] == '1m'
    for key in ('current_price', 'b0_price', 'b0_time', 'neckline_price', 'neckline_time_ms'):
        assert context[key] == wanted[key]
    # Neither producer reuse nor a later market publication can alter the intent.
    event['neckline_price'] = 0.
    event['current_price'] = 999.
    run.quote['current_price'] = 888.
    resumed = Run(tmp_path, memory=run.memory, quote=run.quote)
    resumed.call('load_state', now=BASE + 90)
    resumed.call('retry_notifications', now=BASE + 90)
    assert resumed.notices[-1] == original
    assert not resumed.context_reads
    assert not resumed.state.pending_notifications


def test_generic_completion_after_oz_does_not_reuse_oz_pattern(tmp_path):
    contaminated = {'signal_source': 'OZ', 'signal_tf': '5m', 'source_tf': '5m',
                    'current_price': 105., 'direction': 'LONG', 'b0_price': 1., 'b0_time': BASE,
                    'neckline_price': 3., 'neckline_time_ms': int(BASE * 1000)}
    run = Run(tmp_path, quote=contaminated)
    value = chain(triggers=(ChainTriggerSpec('OZ_ALERT', '1m'), ChainTriggerSpec('BAR_CLOSE', '5m')))
    run.call('add_chain', value)
    run.call('handle_oz_stage_event', oz_event(value.current_watch_id), now=BASE + 1)
    assert value.stage == 1
    assert run.notices[-1][2]['signal_context'] == {}
    run.hit(value, 1)
    context = run.notices[-1][2]['signal_context']
    assert context['signal_source'] == 'SIGNAL' and context['signal_tf'] == '5m'
    assert context['current_price'] == 105.
    assert not any(key in context for key in ('b0_price', 'b0_time', 'neckline_price', 'neckline_time_ms'))
    assert contaminated['signal_source'] == 'OZ'  # No mutation of provider input.


def test_generic_retry_freezes_quote_and_guards_against_callback_mutation(tmp_path):
    run = Run(tmp_path)
    value = chain(triggers=(ChainTriggerSpec('BAR_CLOSE', '5m'),))
    run.call('add_chain', value)
    run.deliver = False
    run.hit(value, 0)
    first = run.notices[-1]
    run.quote['current_price'] = 500.
    def mutate(text, recipient, **kwargs):
        run.notices.append((text, recipient, deepcopy(kwargs)))
        kwargs['signal_context']['current_price'] = 600.
        return False
    run.state.notify_callback = mutate
    run.call('retry_notifications', now=BASE + 40)
    run.state.notify_callback = run.notify
    run.deliver = True
    run.call('retry_notifications', now=BASE + 50)
    assert run.notices[-1] == first
    assert len(run.context_reads) == 1
    assert not run.state.pending_notifications


def test_old_pending_notification_has_no_guessed_market_context(tmp_path):
    run = Run(tmp_path)
    run.state.pending_notifications['old'] = {'text': 'old completion', 'recipient': 'USER', 'chain_id': 'old-chain'}
    run.call('retry_notifications')
    assert run.notices[-1][2]['signal_context'] == {}
    assert not run.context_reads


def test_real_notification_boundary_excludes_status_from_unrelated_oz_context(tmp_path):
    run = Run(tmp_path)
    parent = oz_event('OTHER:OZ')
    parent.update(signal_source='OZ', signal_strategy='OZ')
    kernel = NS(config={'TELEGRAM_CHAT_ID': 'CHANNEL'}, messages=[], current_event=parent,
                timestamp=int((BASE + 2) * 1000), manager=NS(_delivery_context=NS(oz_event=parent)))
    port = NotificationPort(kernel)
    run.state.notify_callback = lambda text, recipient, **kwargs: port.send(text, recipient, **kwargs)
    value = chain()
    run.call('add_chain', value)
    run.hit(value, 0, now=BASE + 1)
    assert all(m['signal_source'] == 'NOTICE' and not m['direction'] and not m['source_tf']
               and 'neckline_price' not in m for m in kernel.messages)
    run.hit(value, 1)
    final = kernel.messages[-1]
    assert final['signal_source'] == 'SIGNAL'
    assert final['direction'] == 'LONG' and final['source_tf'] == '5m'
    assert final['signal_price'] == 101.25
    assert not final.get('b0_price') and 'neckline_price' not in final
    assert final['recipients'] == ['USER']


def test_oz_arm_and_candidate_cancel_are_not_entry_signals(tmp_path):
    run = Run(tmp_path)
    value = chain(final_window_sec=60, oz_tfs=('1m',), invalidation_triggers=(ChainTriggerSpec('BAR_CLOSE', '15m'),))
    value.final_action = 'OZ'
    value.validate()
    run.call('add_chain', value)
    run.hit(value, 0, now=BASE + 1)
    run.hit(value, 1)
    assert value.active_child_id
    assert all(kw['signal_context'] == {} for _, _, kw in run.notices)
    assert not run.context_reads
    run.call('handle_generic_trigger', {'kind': 'GENERIC_TRIGGER', 'chain_id': value.chain_id,
             'chain_stage': -1, 'watch_id': value.invalidation_watch_ids['0'],
             'event_time': BASE + 3, 'event_id': 'cancel:1'}, now=BASE + 3)
    assert value.stage == 0 and not value.active_child_id
    assert run.notices[-1][2]['signal_context'] == {}
    assert not run.context_reads
