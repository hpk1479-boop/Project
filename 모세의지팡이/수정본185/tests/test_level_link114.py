"""The external-liquidity touch a strategy names is tied to its final OZ.

Shipped SPECIAL2 runs unchanged except for what the recipe declares; no case here
is chosen by strategy number.
"""
from recipe_harness114 import Harness, SYMBOL, feed
from strategy_recipe.port import _intent_id

NOW = 1790000100.


def touch(code, name, price, direction, at=1790000040., tf='5m'):
    return {'kind': 'SWEEP_TOUCH', 'strategy': 'SWEEP', 'symbol': SYMBOL, 'source_tf': tf,
            'direction': direction, 'level_code': code, 'level_name': name, 'level_id': f'{code}:x',
            'level_price': price, 'touch_time': at, 'event_time': at}


def machine_for(h, direction, tf='5m'):
    return next(m for m in h.machines if m.direction == direction and m.meaning['steps'][0]['tfs'] == [tf])


def run(h, facts, direction='LONG', tf='5m'):
    """Publish a quiet board, then deliver one SWEEP Fact snapshot for the strategy's own watch."""
    h.publish(NOW, {tf: feed(tf, int(NOW) - int(NOW) % 300)})
    machine = machine_for(h, direction, tf)
    watch = h.port._dependency_id(SYMBOL, tf, 'SWEEP', machine.meaning['steps'][0])
    h.port.handle_fact({'kind': 'FACT_SNAPSHOT', 'strategy': 'SWEEP', 'symbol': SYMBOL, 'source_tf': tf,
        'watch_id': watch, 'facts': facts, 'source_health': {'sources': {tf: 'epoch'}}})
    return machine, watch


def test_every_sweep_watch_carries_the_strategys_atr_rule():
    h = Harness.special('SPECIAL2')
    watches = h.port.desired_subscriptions()['SWEEP']
    assert len(watches) == 12 * 2
    assert all(w['external_atr_period'] == 14 and w['external_atr_mult'] == 1.5 for w in watches.values())


def test_final_oz_is_linked_to_the_touch_that_completed_the_setup():
    h = Harness.special('SPECIAL2')
    machine, watch = run(h, [touch('PDL', '전일저가(PDL)', 3842.15, 'LONG')])
    assert machine.active
    order = h.watch_commands()[-1]
    assert order['timeframes'] == ['5m'] and order['direction'] == 'LONG'
    assert order['external_watch_id'] == watch
    assert order['external_source_tf'] == '5m'
    assert order['external_liquidity_required'] is True and order['external_source_kind'] == 'SWEEP'
    assert order['external_level_id'] == 'PDL:x' and order['external_level_price'] == 3842.15
    assert machine.captures['sweep']['code'] == 'PDL'


def test_the_outermost_touch_is_the_one_linked_for_each_direction():
    h = Harness.special('SPECIAL2')
    run(h, [touch('PDL', 'a', 3850., 'LONG'), touch('PREV_4H_LOW', 'b', 3842., 'LONG'),
            touch('PWL', 'c', 3845., 'LONG')])
    assert h.watch_commands()[-1]['external_level_price'] == 3842.
    h = Harness.special('SPECIAL2')
    run(h, [touch('PDH', 'a', 3900., 'SHORT'), touch('PWH', 'b', 3910., 'SHORT')], direction='SHORT')
    assert h.watch_commands()[-1]['external_level_price'] == 3910.


def test_no_touch_means_no_final_watch():
    h = Harness.special('SPECIAL2')
    machine, _ = run(h, [touch('PDH', 'a', 3900., 'SHORT')])      # only the other direction touched
    assert not machine.active and not h.watch_commands()


def test_without_a_recorded_touch_no_watch_is_opened_and_nothing_raises(caplog):
    h = Harness.special('SPECIAL2')
    machine = machine_for(h, 'LONG')
    machine.active, machine.after_ready, machine.setup_time = True, True, 1.
    with caplog.at_level('ERROR'):
        h.port._arm_watch(machine)                       # an OZ without its gate must never alert
    assert not h.watch_commands() and 'sweep' in caplog.text
    assert not h.manager._active_children


def test_a_strategy_without_a_gate_is_untouched():
    def drop(meaning):
        meaning['final'].pop('level_gate')
    h = Harness.special('SPECIAL2', mutate=drop)
    machine, watch = run(h, [touch('PDL', 'a', 3842.15, 'LONG')])
    order = h.watch_commands()[-1]
    assert machine.active and not any(key.startswith('external_') for key in order)
    # Its watch id is exactly what it always was, so persisted watch state keeps matching.
    step = machine.meaning['steps'][0]
    assert watch == f"SPECIAL2:SWEEP:{SYMBOL}:5m:{_intent_id((step['_level_codes'], None, None))}"
    assert 'external_atr_period' not in h.port.desired_subscriptions()['SWEEP'][watch]


def test_a_different_atr_rule_is_a_different_watch():
    def widen(meaning):
        meaning['final']['level_gate'].update(atr_period=20, atr_mult=2.)
    wide, normal = Harness.special('SPECIAL2', mutate=widen), Harness.special('SPECIAL2')
    wide_watch = {w['watch_id']: w for w in wide.port.desired_subscriptions()['SWEEP'].values()}
    assert all(w['external_atr_period'] == 20 and w['external_atr_mult'] == 2. for w in wide_watch.values())
    assert not set(wide_watch) & set(normal.port.desired_subscriptions()['SWEEP'])
