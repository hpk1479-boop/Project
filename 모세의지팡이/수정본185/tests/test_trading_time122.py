"""Trading time is checked only at the moment an alert goes out.

A setup prepared before trading time still alerts when its final trigger comes inside trading time;
a final trigger outside trading time is dropped, never sent later. Same rule for recipe SPECIALs,
KIM private strategies and configured chains, for OZ and for plain condition alerts.
"""
import datetime as dt
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Part1/program'))
from domain_clock import event_scope
from event_composer_domain import TimePolicy
from test_time_filter115 import CONFIG, meaning_of, rising
from recipe_harness114 import Harness

KST = dt.timezone(dt.timedelta(hours=9))     # SPECIAL7's own: 08:00-12:00, 14-19, 21-24; CONFIG MAIN_ASIA 09:00-11:00


def stamp(hour, minute=0):
    return float(int(dt.datetime(2026, 9, 21, hour, minute, tzinfo=KST).timestamp()))


def at(hour, minute=0):
    return event_scope(int(stamp(hour, minute) * 1000), 'trading-time122', {})


# --- recipe SPECIAL (the shipped SPECIAL7, its own sessions) -----------------------------------------

def special7():
    h = Harness(meaning_of(None), 'SPECIAL7', dict(CONFIG))
    h.manager._time_policy = TimePolicy(CONFIG)
    return h


def test_setup_before_trading_time_alerts_when_the_final_comes_inside_it():
    h = special7()
    with at(7, 50):
        h.publish(stamp(7, 50), {'15m': rising(stamp(7, 50))})
        machine = next(m for m in h.machines if m.direction == 'LONG')
        assert machine.active, 'the setup progresses before trading time'
    with at(8, 5):
        h.final_oz(machine, stamp(8, 5))
    assert len(h.messages) == 1


def test_a_final_outside_trading_time_is_dropped_and_the_next_one_inside_is_sent():
    h = special7()
    with at(7, 50):
        h.publish(stamp(7, 50), {'15m': rising(stamp(7, 50))})
        machine = next(m for m in h.machines if m.direction == 'LONG')
        h.final_oz(machine, stamp(7, 55))
    assert h.messages == [], 'outside trading time: no alert'
    with at(8, 1):
        assert machine.active, 'the dropped final did not end the setup'
    assert h.messages == [], 'nothing is sent later on its own'
    with at(8, 5):
        h.final_oz(machine, stamp(8, 5), event_id='final-second')
    assert len(h.messages) == 1


@pytest.mark.parametrize('hour,minute', [(11, 55), (12, 0)])
def test_final_at_the_last_minute_is_inside(hour, minute):
    h = special7()
    with at(11, 30):
        h.publish(stamp(11, 30), {'15m': rising(stamp(11, 30))})
        machine = next(m for m in h.machines if m.direction == 'LONG')
    with at(hour, minute):
        h.final_oz(machine, stamp(hour, minute))
    assert len(h.messages) == 1


def test_setup_inside_and_final_after_trading_time_is_dropped():
    h = special7()
    with at(11, 50):
        h.publish(stamp(11, 50), {'15m': rising(stamp(11, 50))})
        machine = next(m for m in h.machines if m.direction == 'LONG')
    with at(12, 1):
        h.final_oz(machine, stamp(12, 1))
    assert h.messages == []


# --- KIM private strategy (Composer) --------------------------------------------------------------

def composer():
    from test_recipe_alerts101 import manager
    m, kernel = manager()
    m.config = dict(CONFIG)
    m._time_policy = TimePolicy(CONFIG)
    m._last_signatures = {}
    m._signature_records = NS(put=lambda *args: None)
    m.manual_specs = {}
    m._save_private_state_locked = lambda: None
    m._track_watch_payload = lambda item: True
    return m, kernel


def private_spec(spec_id='P1', time_filters=('MAIN_ASIA',), persistent=False, final_action='OZ'):
    from command_models import ConditionSpec, StrategySpec
    spec = StrategySpec(spec_id=spec_id, name='개인 전략', symbol='TEST',
                        conditions=(ConditionSpec('TREND', '15m', 'LONG'),), oz_tfs=('1m',),
                        final_action=final_action, destination='PRIVATE', owner_chat_id='user',
                        persistent=persistent, time_filters=time_filters, source='PRIVATE')
    spec.validate()
    return spec


def oz_final(child, hour, minute):
    return {'kind': 'FINAL_ALERT', 'strategy': 'OZ', 'watch_id': child, 'watch_ids': [child],
            'request_chat_id': 'user', 'direction': 'LONG', 'symbol': 'TEST', 'source_tf': '1m',
            'message': '올존 완성', 'event_time': stamp(hour, minute), 'event_id': f'oz-{hour}{minute}'}


def test_private_oz_arms_before_trading_time_and_alerts_inside_it():
    m, kernel = composer()
    spec = private_spec()
    with at(8, 30):
        m._arm_oz_locked(spec, 'LONG', 'setup-1')
    assert len(m.commands) == 1, 'arming is no longer held back outside trading time'
    child = m.commands[0]['watch_id']
    assert m._active_children[child]['trading_times'] == {'P1': ['MAIN_ASIA']}
    with at(8, 55):
        result = m._handle_oz_event_core(oz_final(child, 8, 55))
    assert result.get('suppressed') and kernel.messages == []
    assert child in m._active_children, 'the watch stays for its next trigger'
    with at(9, 5):
        m._handle_oz_event_core(oz_final(child, 9, 5))
    assert len(kernel.messages) == 1


def test_a_shared_watch_alerts_when_any_of_its_strategies_is_in_trading_time():
    m, kernel = composer()
    with at(8, 30):
        m._arm_oz_locked(private_spec('ASIA', ('MAIN_ASIA',)), 'LONG', 'a')
        m._arm_oz_locked(private_spec('ALLDAY', ()), 'LONG', 'b')
    assert len(m.commands) == 1, 'same OZ profile and owner share one watch'
    child = m.commands[0]['watch_id']
    assert m._active_children[child]['trading_times'] == {'ASIA': ['MAIN_ASIA'], 'ALLDAY': []}
    with at(13, 0):
        m._handle_oz_event_core(oz_final(child, 13, 0))
    assert len(kernel.messages) == 1


def test_private_condition_alert_outside_trading_time_is_dropped_not_sent_later():
    m, kernel = composer()
    spec = private_spec(final_action='NOTIFY', persistent=False)
    m.manual_specs[spec.spec_id] = spec
    m._delivery_context.oz_event = None
    m._condition_source_binding = lambda spec, cond: (None, {})
    m.send_telegram = lambda *args, **kwargs: kernel.messages.append(args) or True
    with at(8, 50):
        m._notify_spec_locked(spec, 'LONG', 'state-1')
    assert kernel.messages == []
    assert spec.spec_id in m.manual_specs, 'a one-shot strategy waits for its next trigger'
    with at(9, 5):
        m._notify_spec_locked(spec, 'LONG', 'state-1')
    assert kernel.messages == [], 'the same completion is not sent later'
    with at(9, 6):
        m._notify_spec_locked(spec, 'LONG', 'state-2')
    assert len(kernel.messages) == 1
