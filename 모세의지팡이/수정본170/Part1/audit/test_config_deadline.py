"""NEW-01: real ConfigTimedChain callbacks, persistence and SPECIAL registration."""
import logging
import unittest

from harness import World


class ConfigDeadline(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.w = World()
        self.addCleanup(self.w.close)
        self.w.isolate_specs()
        self.definitions = []

    def spec(self, **extra):
        item = dict(spec_id=f'AUDITCFG{len(self.definitions)}', name='Audit config',
                    symbol='BTCUSD', cross_tf='1m', fvg_tfs=('1m',),
                    max_gap_sec=60, final_window_sec=120, validation_mode='BLIND')
        item.update(extra)
        self.definitions.append(item)
        # Real SPECIAL loader re-registers definitions after KIM restart.
        (self.w.path/'SPECIAL/SPECIAL99.py').write_text(
            'def register(manager):\n    manager.register_special_bundle(timed_chains='
            + repr(tuple(self.definitions)) + ')\n', encoding='utf-8')
        self.w.manager.register_special_timed_chain(item)
        return self.w.manager.official_chain_specs[item['spec_id']]

    def event(self, spec, stage, timestamp=None, **extra):
        payload = self.w.manager._config_chain_trigger_payloads_locked(spec)[stage]
        event = dict(payload, kind='GENERIC_TRIGGER', strategy='OZ', direction='LONG',
                     event_time=self.w.clock.time() if timestamp is None else timestamp)
        event.update(extra)
        return event

    def send(self, spec, stage, timestamp=None, **extra):
        return self.w.manager._handle_event(self.event(spec, stage, timestamp, **extra))

    def active(self, spec):
        return [v for v in self.w.manager._config_chain_active.values() if v['spec_id'] == spec.spec_id]

    def late(self, maintenance, restart):
        self.w.clock.advance(300)
        if maintenance:
            self.w.manager._maintain_config_timed_chains()
        if restart:
            self.w.restart_manager()

    def final(self, child, timestamp, **extra):
        event = dict(kind='FINAL_ALERT',strategy='OZ',watch_ids=[child['watch_id']],
                     event_time=timestamp, message='official completion')
        event.update(extra)
        return self.w.manager._handle_event(event)

    def test_characterization_order_watch_and_time_filter(self):
        spec = self.spec()
        self.assertEqual(self.send(spec,1)['reason'],'waiting_for_a')
        start = self.w.clock.time()
        self.send(spec,0,start)
        self.assertTrue(self.send(spec,1,start-1)['ignored'])
        self.assertTrue(self.send(spec,1,watch_id='unknown')['stale'])
        policy = self.w.manager._time_policy
        old = policy.allows
        policy.allows = lambda _: False
        self.assertEqual(self.send(spec,1,start+10)['reason'],'time_filter_blocked')
        self.assertEqual(self.w.manager._config_chain_state[spec.spec_id]['stage'],1)
        policy.allows = old
        self.assertTrue(self.send(spec,1,start+10)['advanced'])

    def test_characterization_max_gap_bars_and_unavailable(self):
        w = self.w
        for count in (2,3,None):
            with self.subTest(bars=count):
                spec = self.spec(max_gap_sec=0,max_gap_bars=2)
                w.manager._config_chain_closed_bar_times = lambda *_: (100.,)
                self.send(spec,0)
                times = () if count is None else tuple(100.+60*i for i in range(count+1))
                w.manager._config_chain_closed_bar_times = lambda *_: times
                result = self.send(spec,1,w.clock.time()+10)
                if count == 2: self.assertTrue(result['advanced'])
                elif count == 3: self.assertEqual(result['reason'],'a_to_b_expired_bars')
                else: self.assertEqual(result['reason'],'bar_expiry_unavailable')

    def test_characterization_post_touch_direction_tf_filters_and_remaining_window(self):
        w = self.w
        spec = self.spec(final_fvg_touch_tfs=('5m',),final_time_filters=('LONDON',))
        deadline = w.clock.time()+60
        w.manager._config_chain_state[spec.spec_id]['post_touch_armed'] = dict(
            direction='LONG',expires_at=deadline,pair_signature='observed-pair')
        event = dict(kind='FVG_TOUCH',symbol='BTCUSD',source_tf='5m',direction='LONG',
                     zone_id='observed-zone',event_time=w.clock.time()+10)
        handler = w.manager._handle_official_timed_chain_fvg_touch
        self.assertEqual(handler(dict(event,direction='SHORT'))['armed_children'],0)
        self.assertEqual(handler(dict(event,source_tf='1m'))['armed_children'],0)
        old = w.manager._time_policy.allows
        w.manager._time_policy.allows = lambda _: False
        self.assertEqual(handler(event)['armed_children'],0)
        w.manager._time_policy.allows = lambda _: True
        self.assertEqual(handler(event)['armed_children'],1)
        child = self.active(spec)[0]
        self.assertEqual(child['expires_at'],deadline)
        self.assertEqual(child['source_fvg_zone_id'],'observed-zone')
        self.assertIsNone(w.manager._config_chain_state[spec.spec_id]['post_touch_armed'])
        w.manager._time_policy.allows = old

    def test_characterization_opposite_cancellation_and_unordered_distinctness(self):
        spec = self.spec(cancel_on_opposite_cross=True)
        self.send(spec,0)
        self.assertTrue(self.send(spec,0,direction='SHORT')['cancelled'])
        self.assertEqual(self.w.manager._config_chain_state[spec.spec_id]['stage'],0)
        unordered = self.spec(order_mode='UNORDERED')
        self.assertFalse(self.send(unordered,1)['advanced'])
        self.assertFalse(self.send(unordered,1)['advanced'])
        self.assertFalse(self.send(unordered,0,direction='SHORT')['advanced'])
        self.assertTrue(self.send(unordered,0)['advanced'])

    def test_pair_deadline_boundary_order_restart_matrix(self):
        for delta in (-.001,0,.001):
            for maintenance in (False,True):
                for restart in (False,True):
                    with self.subTest(delta=delta,maintenance=maintenance,restart=restart):
                        spec = self.spec()
                        start = self.w.clock.time()
                        self.send(spec,0,start)
                        self.late(maintenance,restart)
                        result = self.send(spec,1,start+60+delta)
                        self.assertEqual(bool(result.get('advanced')),delta <= 0)
                        if delta > 0: self.assertTrue(result['expired'])

    def test_final_deadline_inherited_and_persisted_matrix(self):
        for delta in (-.001,0,.001):
            for maintenance in (False,True):
                for restart in (False,True):
                    with self.subTest(delta=delta,maintenance=maintenance,restart=restart):
                        spec = self.spec()
                        start = self.w.clock.time()
                        self.send(spec,0,start)
                        self.send(spec,1,start+50)
                        child = self.active(spec)[0]
                        self.late(maintenance,restart)
                        before = len(self.w.http.deliveries)
                        result = self.final(child,start+60+delta)
                        self.assertEqual(len(self.w.http.deliveries)-before,int(delta <= 0))
                        if delta > 0: self.assertTrue(result['expired'])
                        self.assertEqual(len(self.active(spec)),1)  # persistent official watch

    def test_final_window_uses_pair_event_time_and_no_reset_after_touch(self):
        w = self.w
        spec = self.spec(max_gap_sec=600,final_window_sec=20,final_fvg_touch_tfs=('5m',))
        start = w.clock.time()
        self.send(spec,0,start)
        self.send(spec,1,start+10)
        self.late(True,True)
        event = dict(kind='FVG_TOUCH',symbol='BTCUSD',source_tf='5m',direction='LONG',
                     zone_id='touch',event_time=start+25)
        self.assertEqual(w.manager._handle_official_timed_chain_fvg_touch(event)['armed_children'],1)
        child = self.active(spec)[0]
        self.assertEqual(child['expires_at'],start+30)
        self.late(True,True)
        before = len(w.http.deliveries)
        self.assertTrue(self.final(child,start+30+.001)['expired'])
        self.assertEqual(len(w.http.deliveries),before)
        self.assertTrue(self.final(child,start+30)['delivered'])
        self.assertEqual(len(w.http.deliveries),before+1)

    def test_unordered_delayed_pair_and_final_window(self):
        for reverse in (False,True):
            for delta in (-.001,0,.001):
                with self.subTest(reverse=reverse,delta=delta):
                    spec = self.spec(order_mode='UNORDERED')
                    start = self.w.clock.time()
                    self.send(spec,int(reverse),start)
                    self.late(True,True)
                    result = self.send(spec,int(not reverse),start+60+delta)
                    self.assertEqual(result['advanced'],delta <= 0)
                    if delta <= 0:
                        child = self.active(spec)[0]
                        self.assertTrue(self.final(child,start+60+.001)['expired'])
                        self.assertTrue(self.final(child,start+60)['delivered'])

    def test_missing_completion_timestamp_is_rejected_without_state_loss(self):
        spec = self.spec()
        start = self.w.clock.time()
        self.send(spec,0,start)
        event = self.event(spec,1)
        event.pop('event_time')
        self.assertFalse(self.w.manager._handle_event(event)['ok'])
        self.assertTrue(self.send(spec,1,start+10)['advanced'])
        child = self.active(spec)[0]
        before = len(self.w.http.deliveries)
        self.final(child,None)
        self.assertEqual(len(self.w.http.deliveries),before)

    def test_post_touch_boundary_does_not_consume_late_delivery_evidence(self):
        for mode in ('SEQUENTIAL','UNORDERED'):
            for delta in (-.001,0,.001):
                with self.subTest(mode=mode,delta=delta):
                    spec = self.spec(order_mode=mode,final_fvg_touch_tfs=('5m',))
                    start = self.w.clock.time()
                    self.send(spec,0,start)
                    self.send(spec,1,start+50)
                    self.late(True,True)
                    handler = self.w.manager._handle_official_timed_chain_fvg_touch
                    event = dict(kind='FVG_TOUCH',symbol='BTCUSD',source_tf='5m',
                                 direction='LONG',zone_id='touch',event_time=start+60+delta)
                    # Other specs have earlier deadlines; only this setup can arm.
                    self.assertEqual(handler(event)['armed_children'],int(delta <= 0))
                    if delta > 0:
                        self.assertEqual(handler(dict(event,event_time=start+60))['armed_children'],1)

    def test_max_gap_bars_is_still_an_independent_gate_after_restart(self):
        w = self.w
        for count in (2,3):
            for maintenance in (False,True):
                with self.subTest(count=count,maintenance=maintenance):
                    spec = self.spec(max_gap_sec=60,max_gap_bars=2)
                    start = w.clock.time()
                    w.manager._config_chain_closed_bar_times = lambda *_: (100.,)
                    self.send(spec,0,start)
                    w.clock.advance(300)
                    times = tuple(100.+60*i for i in range(count+1))
                    w.manager._config_chain_closed_bar_times = lambda *_: times
                    if maintenance: w.manager._maintain_config_timed_chains()
                    w.restart_manager()
                    w.manager._config_chain_closed_bar_times = lambda *_: times
                    result = self.send(spec,1,start+60)
                    self.assertEqual(bool(result.get('advanced')),count == 2)
                    if count == 3: self.assertEqual(result['reason'],'a_to_b_expired_bars')

    def test_special_hook_and_mixed_watch_delivery_respect_deadline(self):
        from types import SimpleNamespace
        w = self.w
        spec = self.spec()
        start = w.clock.time()
        self.send(spec,0,start)
        self.send(spec,1,start+10)
        child = self.active(spec)[0]
        calls = []
        def hook(event):
            calls.append(event)
            return {'ok':True,'delivered':True,'suppressed':True,'special_gate':'preserved'}
        w.manager._special_oz_event_handlers[spec.spec_id] = SimpleNamespace(handle_oz_event=hook)
        self.assertTrue(self.final(child,start+60+.001,source_spec_id=spec.spec_id)['expired'])
        self.assertEqual(calls,[])
        self.assertEqual(self.final(child,start+60,source_spec_id=spec.spec_id)['special_gate'],'preserved')
        self.assertEqual(len(calls),1)
        before = len(w.http.deliveries)
        result = self.final(child,start+61,watch_ids=[child['watch_id'],'independent'])
        self.assertTrue(result['delivered'])
        self.assertEqual(len(w.http.deliveries),before+1)

    def test_completion_timestamp_precedence_and_confirmed_delivery_dedup(self):
        w = self.w
        spec = self.spec()
        start = w.clock.time()
        self.send(spec,0,start)
        # Completion, not earlier event/bar timestamp, decides the boundary.
        self.assertTrue(self.send(spec,1,start+10,completion_time=start+61)['expired'])
        self.assertTrue(self.send(spec,1,start+10,completion_time=start+50)['advanced'])
        child = self.active(spec)[0]
        self.assertTrue(self.final(child,start+50,completion_time=start+61)['expired'])
        before = len(w.http.deliveries)
        event = dict(completion_time=start+60,event_id='config-final-confirmed')
        self.final(child,start+50,**event)
        self.assertEqual(len(w.http.deliveries),before+1)
        self.late(True,True)
        self.final(child,start+50,**event)
        self.assertEqual(len(w.http.deliveries),before+1)


if __name__ == '__main__':
    unittest.main()
