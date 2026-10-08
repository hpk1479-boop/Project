"""Original normal behavior plus user-authorized remediation regression contracts."""
import hashlib
import json
import logging
import unittest
from unittest.mock import patch

import pandas as pd

from harness import FIXTURES, ROOT, World


class Baseline(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.w = World()
        self.addCleanup(self.w.close)
        self.w.isolate_specs()

    def spec(self, kinds=("TREND",), action="NOTIFY", persistent=True):
        w = self.w
        spec = w.kim.StrategySpec("AUDIT", "Audit", "BTCUSD",
            tuple(w.kim.ConditionSpec(k, "1m") for k in kinds), ("1m",),
            final_action=action, validation_mode="BLIND", final_direction="LONG",
            destination="PRIVATE", owner_chat_id="OFFLINE_PRIVATE", persistent=persistent,
            source="PRIVATE", sweep_levels=("PDL",))
        spec.validate()
        w.manager.manual_specs[spec.spec_id] = spec
        w.manager._save_private_state_locked()
        return spec

    def trend_once(self):
        w = self.w
        w.trend_engine.run_watch_once({"BTCUSD": {"1m": ()}})

    def chain_spec(self, action="NOTIFY"):
        w = self.w
        return w.chain.TimedChainSpec("CHAIN:AUDIT", "OFFLINE_PRIVATE", "BTCUSD",
            (w.chain.ChainTriggerSpec("WONBI_TOUCH", "1m", next_window_sec=60),
             w.chain.ChainTriggerSpec("FVG_NEW", "1m")),
            final_action=action, oz_tfs=("1m",) if action == "OZ" else (),
            final_window_sec=120 if action == "OZ" else None, created_at=w.clock.time())

    def chain_event(self, chain):
        return {"kind": "GENERIC_TRIGGER", "watch_id": chain.current_watch_id,
                "chain_id": chain.chain_id, "chain_stage": chain.stage, "direction": "LONG",
                "event_time": self.w.clock.time(), "message": "fixture condition"}

    def test_source_hashes_match_authorized_change_units(self):
        from source_integrity import verify_sources
        self.assertEqual(verify_sources()['integrity_errors'], [])

    def test_pipe_roundtrip_features_and_copy(self):
        w = self.w
        frame = w.market()
        w.feed(frame)
        data = w.trend_engine.staff.request("BTCUSD", ["1m"], ["HMA"])["1m"]
        self.assertEqual(len(data), 80)
        self.assertEqual(data.iloc[-1].wonbi_mid, frame.iloc[-1].open_band_4_mid)
        self.assertNotIn("atr_14", data)
        enriched = w.manager.staff.request("BTCUSD", ["1m"], ["HMA"])["1m"]
        self.assertEqual(len(enriched), 80)
        self.assertEqual(enriched.iloc[-1].wonbi_mid, frame.iloc[-1].open_band_4_mid)
        self.assertEqual(enriched.iloc[-1].wonbi_upper, frame.iloc[-1].wonbi_upper)
        self.assertEqual(enriched.iloc[-1].wonbi_lower, frame.iloc[-1].wonbi_lower)
        self.assertGreater(enriched.iloc[-1].atr_14, 0)
        data.loc[0, "close"] = -999
        self.assertNotEqual(w.raw_frame("BTCUSD", "1m").iloc[0].close, -999)
        enriched.loc[0, "close"] = -999
        self.assertNotEqual(w.manager.staff.request("BTCUSD", ["1m"], ["HMA"])["1m"].iloc[0].close, -999)

    def test_pipe_duplicate_sequence_and_partial_do_not_replace(self):
        w = self.w
        frame = w.market()
        w.feed(frame, snapshot=50)
        changed = frame.copy()
        changed.loc[79, "close"] = 999
        w.feed(changed, snapshot=50)
        self.assertEqual(w.raw_frame("BTCUSD", "1m").iloc[-1].close, 120)
        with self.assertRaises(OSError): w.feed(changed, snapshot=51, truncate=8)
        self.assertEqual(w.server.cache._snapshot[("BTCUSD", "1m")], 50)

    def test_staff_stale_boundary_and_client_error(self):
        w = self.w
        w.feed(w.market())
        w.clock.advance(30)
        self.assertIsNotNone(w.fvg_engine.staff.request("BTCUSD", ["1m"], []))
        w.clock.advance(.001)
        self.assertIsNone(w.fvg_engine.staff.request("BTCUSD", ["1m"], []))
        self.assertTrue(w.data_request({"kind": "PING"})["pong"])

    def test_optional_indicator_failure_is_request_scoped(self):
        w = self.w
        df = w.market()
        df["RSI_val"] = float("nan")
        w.feed(df)
        self.assertIn("1m", w.data_request({"symbol": "BTCUSD", "timeframes": ["1m"]}))
        with self.assertRaises(RuntimeError):
            w.data_request({"symbol": "BTCUSD", "timeframes": ["1m"], "indicators": ["RSI"]})

    def test_sigma_sync_uses_staff_and_rejects_invalid(self):
        w = self.w
        self.assertEqual(w.manager.wonbi_state.set_sigma(2), 2)
        self.assertEqual(w.data_request({"kind": "PING"})["wonbi_sigma"], 2)
        with self.assertRaises(ValueError): w.manager.wonbi_state.set_sigma(0)

    def test_weekend_empty_response_and_crypto_exemption(self):
        w = self.w
        w.clock.epoch = pd.Timestamp("2026-09-19T12:00:00Z").timestamp()
        self.assertEqual(w.data_request({"symbol": "XAUUSD", "timeframes": ["1m"]}), {})
        with self.assertRaises(RuntimeError): w.data_request({"symbol": "BTCUSD", "timeframes": ["1m"]})

    def test_client_timeout_recreates_req_socket(self):
        w = self.w
        old = w.fvg_engine.staff.socket
        w.bus.fail_next.add(w.server.endpoint)
        self.assertIsNone(w.fvg_engine.staff.request("BTCUSD", ["1m"], []))
        self.assertTrue(old.closed)
        self.assertIsNot(old, w.fvg_engine.staff.socket)

    def test_trend_fixture_and_duplicate_suppression(self):
        w = self.w
        w.feed(w.market())
        self.trend_once()
        fact = w.manager.trend_facts[("BTCUSD", "1m")]
        self.assertEqual((fact["trend"], fact["direction"]), ("UP", "LONG"))
        # 전략 추세 = SMA20(시가) 1봉 기울기와 HMA50 2봉 기울기가 같은 방향 (fixture: 둘 다 상승).
        data = w.trend_engine.staff.request("BTCUSD", ["1m"], ["HMA"])["1m"]
        s20 = data["open"].rolling(20).mean()
        self.assertEqual(fact["sma20_slope"], float(s20.iloc[-1] - s20.iloc[-2]))
        self.assertEqual(fact["hma50_slope"], float(data["hma_50"].iloc[-1] - data["hma_50"].iloc[-3]))
        self.assertGreater(fact["sma20_slope"], 0)
        self.assertGreater(fact["hma50_slope"], 0)
        # 평상시 추세 상태에는 전체 지표 추세점수를 계산/전송하지 않습니다 (test_indicator_split 참조).
        self.assertNotIn("long_score", fact)
        self.assertNotIn("short_score", fact)
        before = len(w.bus.trace)
        self.trend_once()
        self.assertEqual(len(w.bus.trace), before + 2)  # STAFF and authoritative current state

    def test_manager_restart_recovers_unchanged_trend_fact(self):
        w = self.w
        self.spec()
        w.feed(w.market())
        self.trend_once()
        self.assertTrue(w.manager.trend_facts)
        w.restart_manager()
        self.assertIn("AUDIT", w.manager.manual_specs)
        w.manager._sync_engine_subscriptions(force_refresh=True)
        w.commands()
        self.trend_once()
        self.assertTrue(w.manager.trend_facts)

    def test_trend_restart_reemits_state_and_duplicate_notification(self):
        w = self.w
        self.spec()
        w.feed(w.market())
        self.trend_once()
        before = len(w.http.deliveries)
        w.restart_engines("TREND")
        self.trend_once()
        self.assertEqual(len(w.http.deliveries), before)  # Composer signature suppresses
        self.assertTrue(w.manager.trend_facts)

    def test_failed_trend_ack_retries_unchanged_state(self):
        w = self.w
        w.feed(w.market())
        w.bus.fail_next.add(w.manager.alert_endpoint)
        self.trend_once()
        self.trend_once()
        self.assertTrue(w.manager.trend_facts)

    def test_trend_pending_survives_restart_and_lost_ack(self):
        w = self.w
        self.spec()
        w.feed(w.market())
        w.bus.lose_ack_next.add(w.manager.alert_endpoint)
        self.trend_once()
        before = len(w.http.deliveries)
        w.restart_engines('TREND')
        self.trend_once()
        self.assertEqual(len(w.http.deliveries), before)
        events = [x['request'] for x in w.bus.trace if x['request'].get('kind') == 'TREND_STATE']
        self.assertEqual(events[0]['event_id'], events[1]['event_id'])
        self.assertEqual(w.trend_engine._last_watch_state[('BTCUSD','1m')], 'UP')

    def test_fact_duplicate_and_final_alert_duplicate_are_different(self):
        w = self.w
        self.spec()
        w.feed(w.market())
        self.trend_once()
        event = dict(w.manager.trend_facts[("BTCUSD", "1m")])
        before = len(w.http.deliveries)
        w.manager._handle_event(event)
        self.assertEqual(len(w.http.deliveries), before)
        final = {"kind": "FINAL_ALERT", "strategy": "OZ", "message": "same-final", "watch_ids": ["x"], "event_id": "final-1"}
        w.manager._handle_event(final)
        w.manager._handle_event(final)
        self.assertEqual(len(w.http.deliveries), before + 1)
        w.restart_manager()
        w.manager._handle_event(final)
        self.assertEqual(len(w.http.deliveries), before + 1)
        w.manager._handle_event(dict(final, event_id='final-2'))
        self.assertEqual(len(w.http.deliveries), before + 2)

    def test_fvg_live_fill_is_not_closed_invalidation(self):
        w = self.w
        df = w.market()
        initial = w.fvg.build_fvg_state("BTCUSD", "1m", df)
        zone = initial["zones"][0]
        self.assertAlmostEqual(zone["zone_bot"], 116.2)
        self.assertAlmostEqual(zone["zone_top"], 118.6)
        w.fvg_engine.process_watch_result(initial)
        df.loc[79, "low"] = 116.2
        live = w.fvg.build_fvg_state("BTCUSD", "1m", df)
        self.assertIn(zone["zone_id"], [z["zone_id"] for z in live["zones"]])
        nxt = pd.concat([df, df.iloc[[-1]]], ignore_index=True)
        nxt.loc[80, "time"] += pd.Timedelta(minutes=1)
        closed = w.fvg.build_fvg_state("BTCUSD", "1m", nxt)
        w.fvg_engine.process_watch_result(closed)
        self.assertNotIn(("BTCUSD", "1m", zone["zone_id"]), w.manager.fvg_touches)
        kinds = [x["request"].get("kind") for x in w.bus.trace]
        self.assertIn("FVG_FILLED", kinds)

    def test_fvg_restart_before_fill_reconciles_kim_touch(self):
        w = self.w
        df = w.market()
        w.fvg_engine.process_watch_result(w.fvg.build_fvg_state("BTCUSD", "1m", df))
        self.assertTrue(w.manager.fvg_touches)
        w.restart_engines("FVG")
        df.loc[79, "low"] = 116
        nxt = pd.concat([df, df.iloc[[-1]]], ignore_index=True)
        nxt.loc[80, "time"] += pd.Timedelta(minutes=1)
        w.fvg_engine.process_watch_result(w.fvg.build_fvg_state("BTCUSD", "1m", nxt))
        self.assertFalse(w.manager.fvg_touches)
        w.restart_engines("FVG")
        w.fvg_engine.process_watch_result(w.fvg.build_fvg_state("BTCUSD", "1m", nxt))
        self.assertFalse(w.manager.fvg_touches)

    def test_fvg_first_snapshot_suppresses_created_but_emits_touch(self):
        w = self.w
        result = w.fvg.build_fvg_state("BTCUSD", "1m", w.market())
        w.fvg_engine.process_watch_result(result)
        kinds = [x["request"].get("kind") for x in w.bus.trace]
        self.assertIn("FVG_TOUCH", kinds)
        self.assertNotIn("FVG_CREATED", kinds)
        count = len(w.bus.trace)
        w.fvg_engine.process_watch_result(result)
        self.assertEqual(len(w.bus.trace), count + 1)  # snapshot only; no repeated lifecycle event

    def test_fact_snapshot_empty_partial_and_out_of_order(self):
        w = self.w
        result = w.fvg.build_fvg_state('BTCUSD','1m',w.market())
        w.fvg_engine.process_watch_result(result)
        old = next(x['request'] for x in w.bus.trace if x['request'].get('kind') == 'FVG_TOUCH')
        snapshot = w.fvg_engine.manager.stream.snapshot('BTCUSD','1m',[])
        self.assertFalse(w.manager._handle_event(dict(snapshot, complete=False))['ok'])
        self.assertTrue(w.manager.fvg_touches)
        self.assertTrue(w.manager._handle_event(snapshot)['ok'])
        self.assertFalse(w.manager.fvg_touches)
        w.manager._handle_event(old)
        self.assertFalse(w.manager.fvg_touches)
        w.restart_manager()
        w.manager._handle_event(old)
        self.assertFalse(w.manager.fvg_touches)

    def test_snapshot_save_failure_preserves_facts_and_retry_succeeds(self):
        w = self.w
        result = w.fvg.build_fvg_state('BTCUSD', '1m', w.market())
        w.fvg_engine.process_watch_result(result)
        snapshot = w.fvg_engine.manager.stream.snapshot('BTCUSD', '1m', [])
        zones, touches = dict(w.manager.fvg_zones), dict(w.manager.fvg_touches)
        revisions = w.manager._fact_revisions.all()
        bindings = dict(w.manager._source_bindings)
        deliveries = len(w.http.deliveries)
        with patch.object(w.manager._fact_revisions, 'put', side_effect=PermissionError('locked')):
            with self.assertRaises(PermissionError):
                w.manager._handle_event(snapshot)
        self.assertEqual(w.manager.fvg_zones, zones)
        self.assertEqual(w.manager.fvg_touches, touches)
        self.assertEqual(w.manager._fact_revisions.all(), revisions)
        self.assertEqual(w.manager._source_bindings, bindings)
        self.assertEqual(len(w.http.deliveries), deliveries)
        self.assertTrue(w.manager._handle_event(snapshot)['ok'])
        self.assertFalse(w.manager.fvg_zones)
        self.assertFalse(w.manager.fvg_touches)

    def test_reconciliation_after_manager_restart_does_not_renotify(self):
        w = self.w
        self.spec()
        w.feed(w.market())
        self.trend_once()
        before = len(w.http.deliveries)
        w.restart_manager()
        self.trend_once()
        self.assertTrue(w.manager.trend_facts)
        self.assertEqual(len(w.http.deliveries), before)

    def sweep_setup(self):
        w = self.w
        payload = {"action": "SWEEP_WATCH", "watch_id": "SWEEP:AUDIT", "symbol": "BTCUSD", "source_tf": "1m", "levels": ["PDL"]}
        w.sweep_registry.add(payload)
        w.external.register(payload)
        w.feed_all()
        spec = w.sweep_registry.snapshot()[0]
        w.sweep_engine.run_spec_once(spec)
        w.sweep_engine.flush_state()
        w.pump(w.sweep_event_worker)
        return spec

    def test_sweep_closed_touch_restart_restores_without_oz_republish(self):
        w = self.w
        spec = self.sweep_setup()
        self.assertTrue(w.manager.sweep_touches)
        before = w.sweep_engine.events.path.read_bytes()
        w.restart_engines("SWEEP")
        w.sweep_engine.run_spec_once(spec)
        self.assertEqual(w.sweep_engine.events.path.read_bytes(), before)
        self.assertTrue(next(iter(w.manager.sweep_touches.values()))["restored"])

    def test_sweep_restart_before_and_after_invalidation(self):
        w = self.w
        spec = self.sweep_setup()
        w.restart_engines("SWEEP")
        daily = w.market()
        daily.loc[78, "time"] += pd.Timedelta(days=1)
        daily.loc[79, "time"] += pd.Timedelta(days=1)
        daily.loc[78, "low"] = 50
        w.feed(daily, "1d")
        w.sweep_engine.run_spec_once(spec)
        w.sweep_engine.flush_state()
        self.assertFalse(w.manager.sweep_touches)
        w.restart_engines("SWEEP")
        w.sweep_engine.run_spec_once(spec)
        self.assertFalse(w.manager.sweep_touches)

    def test_external_invalidation_then_old_duplicate_stays_invalid(self):
        w = self.w
        self.sweep_setup()
        event = dict(next(iter(w.manager.sweep_touches.values())))
        w.external.apply_event(dict(event, kind="SWEEP_INVALIDATED", event_time=event["event_time"]+60))
        self.assertFalse(w.external._states)
        w.external.apply_event(event)
        self.assertFalse(w.external._states)
        w.restart_oz()
        w.external.apply_event(event)
        self.assertFalse(w.external._states)

    def test_oz_restart_restores_observed_candidate(self):
        w = self.w
        w.watch.add_manual(["1m"], symbol="BTCUSD", watch_id="OZARM:AUDIT", validation_mode="BLIND", persistent=True)
        out, inside = w.market("OUT"), w.market("IN")
        w.monitor._process_hma_cross("1m", out)
        w.monitor._process_out_in("1m", out)
        w.monitor._process_out_in("1m", inside)
        self.assertIsNotNone(w.monitor.candidates[("1m", "LONG")])
        before = w.monitor.candidates[('1m','LONG')]
        w.restart_oz()
        self.assertTrue(w.watch._watches)
        self.assertEqual(w.monitor.candidates[("1m", "LONG")], before)
        w.monitor._process_out_in("1m", inside)
        self.assertEqual(w.monitor.candidates[("1m", "LONG")], before)

    def test_oz_resume_does_not_invent_unobserved_out_in(self):
        w = self.w
        w.monitor._process_out_in('1m', w.market('OUT'))
        w.restart_oz()
        for _ in range(2): w.monitor._process_out_in('1m', w.market('IN'))
        self.assertIsNone(w.monitor.candidates[('1m','LONG')])

    def test_oz_restored_candidate_cancels_on_opposite_cross(self):
        w = self.w
        w.monitor._process_hma_cross('1m', w.market('OUT'))
        w.monitor._process_out_in('1m', w.market('OUT'))
        w.monitor._process_out_in('1m', w.market('IN'))
        w.restart_oz()
        frame = w.market('NEXT_BAR')
        frame.loc[len(frame)-1,'hma_6'] = 118
        w.monitor._process_hma_cross('1m', frame)
        self.assertIsNone(w.monitor.candidates[('1m','LONG')])

    def test_oz_candidate_restart_replay_finishes_once(self):
        w = self.w
        w.watch.add_manual(['1m'], symbol='BTCUSD', watch_id='persist', validation_mode='BLIND', persistent=True)
        for phase in ('OUT','IN'):
            w.feed_all(phase)
            revision, tfs = w.watch.snapshot_for_symbol('BTCUSD','BLIND','OZ')
            w.monitor.run_once(revision,tfs)
        self.assertIsNotNone(w.monitor.candidates[('1m','LONG')])
        w.restart_oz()
        w.feed_all('NEXT_BAR')
        revision,tfs = w.watch.snapshot_for_symbol('BTCUSD','BLIND','OZ')
        w.monitor.run_once(revision,tfs)
        finals = lambda: [x for x in w.bus.trace if x['request'].get('kind')=='FINAL_ALERT']
        self.assertEqual(len(finals()),1)
        w.restart_oz()
        w.monitor.run_once(revision,tfs)
        self.assertEqual(len(finals()),1)

    def test_observed_state_roundtrip_all_profiles_and_sides(self):
        w = self.w
        for vm,tm in w.oz.PROFILE_KEYS:
            for side in ('LONG','SHORT'):
                with self.subTest(profile=(vm,tm),side=side):
                    frames = [w.market(phase) for phase in ('OUT','IN')]
                    if side == 'SHORT':
                        for frame in frames:
                            high,low = frame.high.copy(),frame.low.copy()
                            for col in ('open','close','hma_6','hma_17'): frame[col] = 240-frame[col]
                            frame['high'],frame['low'] = 240-low,240-high
                            for col in ('RSI_val','STO_val','DI_val','price_hma_6'): frame[col] = 100-frame[col]
                    symbol = 'OBSERVED_'+side
                    mon = w.oz.OZMonitor(symbol,w.config,w.sender,w.watch,validation_mode=vm,trigger_mode=tm)
                    mon._process_hma_cross('1m',frames[0])
                    for frame in frames: mon._process_out_in('1m',frame)
                    self.assertIsNotNone(mon.candidates[('1m',side)])
                    restored = w.oz.OZMonitor(symbol,w.config,w.sender,w.watch,validation_mode=vm,trigger_mode=tm)
                    for name in mon._checkpoint_fields:
                        self.assertEqual(getattr(restored,name),getattr(mon,name))

    def test_restored_oz_timer_is_not_restarted(self):
        w = self.w
        w.monitor._process_hma_cross('1m',w.market('OUT'))
        w.monitor._process_out_in('1m',w.market('OUT'))
        w.monitor._process_out_in('1m',w.market('IN'))
        w.restart_oz()
        frame = w.market('IN')
        for i in range(12):
            row = frame.iloc[[-1]].copy()
            row['time'] += pd.Timedelta(minutes=1)
            row['open'] = row['close']
            frame = pd.concat([frame,row],ignore_index=True)
        w.monitor._maintain_base_candidate('1m','LONG',frame)
        self.assertIsNone(w.monitor.candidates[('1m','LONG')])

    def test_watch_registration_restart_and_ping_reconcile(self):
        w = self.w
        self.spec(("TREND", "FVG", "SWEEP"))
        w.manager._sync_engine_subscriptions()
        # Crash before consumers see registration: new consumers skip past it.
        w.restart_engines()
        w.commands()
        self.assertFalse(w.trend_registry.snapshot())
        for engine in (w.trend_engine, w.fvg_engine, w.sweep_engine):
            self.assertTrue(engine.manager.verify_connection())
        w.commands()
        self.assertTrue(w.trend_registry.snapshot())
        self.assertTrue(w.fvg_registry.snapshot())
        self.assertTrue(w.sweep_registry.snapshot())
        w.restart_engines()
        self.assertTrue(w.trend_registry.snapshot())
        self.assertTrue(w.fvg_registry.snapshot())
        self.assertTrue(w.sweep_registry.snapshot())

    def test_start_order_failed_ping_periodic_refresh_recovers_registration(self):
        w = self.w
        self.spec(("TREND", "FVG", "SWEEP"))
        handler = w.bus.handlers.pop(w.manager.alert_endpoint)
        self.assertFalse(w.trend_engine.manager.verify_connection())
        w.bus.handlers[w.manager.alert_endpoint] = handler
        w.manager._sync_engine_subscriptions(force_refresh=True)
        w.commands()
        self.assertTrue(w.trend_registry.snapshot())

    def test_unowned_oz_watch_survives_restart_ping(self):
        w = self.w
        w.generic.add({"watch_id": "GEN:AUDIT", "watch_type": "WONBI_TOUCH", "timeframes": ["1m"], "symbol": "BTCUSD", "request_chat_id": "OFFLINE_PRIVATE"})
        self.assertTrue(w.generic._watches)
        w.restart_oz()
        w.sender.verify_connection()
        w.commands()
        self.assertIn('GEN:AUDIT', w.generic._watches)

    def test_oz_ping_cancels_only_owned_stale_watches(self):
        w = self.w
        for wid, owner in [('owned', 'KIM'), ('independent', 'OZ')]:
            w.generic.add({'watch_id': wid, 'watch_type': 'BAR', 'timeframes': ['1m'],
                           'persistent': True, 'watch_owner': owner, 'request_chat_id': 'OFFLINE_PRIVATE'})
            w.watch.add_manual(['1m' if owner == 'KIM' else '2m'], watch_id='manual:'+wid, persistent=True,
                               watch_owner=owner, request_chat_id='OFFLINE_PRIVATE')
        w.restart_oz()
        w.sender.verify_connection()
        w.commands()
        self.assertNotIn('owned', w.generic._watches)
        self.assertIn('independent', w.generic._watches)
        self.assertNotIn('manual:owned', w.watch._watches)
        self.assertIn('manual:independent', w.watch._watches)

    def test_timed_chain_middle_restart_and_stale_duplicate(self):
        w = self.w
        chain = self.chain_spec()
        w.manager.watch_orchestrator.add_chain(chain)
        first = self.chain_event(chain)
        w.manager._handle_event(first)
        deadline, wid = chain.stage_deadline, chain.current_watch_id
        w.restart_manager()
        restored = w.manager.timed_chains[chain.chain_id]
        self.assertEqual((restored.stage, restored.stage_deadline, restored.current_watch_id), (1, deadline, wid))
        self.assertTrue(w.manager._handle_event(first)["stale"])
        w.manager._handle_event(self.chain_event(restored))
        self.assertNotIn(chain.chain_id, w.manager.timed_chains)

    def test_timed_chain_deadline_equality_is_order_independent(self):
        w = self.w
        chain = self.chain_spec()
        w.manager.watch_orchestrator.add_chain(chain)
        w.manager._handle_event(self.chain_event(chain))
        w.clock.advance(60)
        event = self.chain_event(chain)
        self.assertNotIn("expired", w.manager._handle_event(event))
        chain = self.chain_spec()
        w.manager.watch_orchestrator.add_chain(chain)
        w.manager._handle_event(self.chain_event(chain))
        w.clock.advance(60)
        w.manager.watch_orchestrator.maintenance()
        self.assertIn(chain.chain_id, w.manager.timed_chains)
        self.assertNotIn('expired',w.manager._handle_event(self.chain_event(chain)))

    def test_chain_deadline_uses_event_time_after_maintenance_and_restart(self):
        w = self.w
        chain = self.chain_spec()
        w.manager.watch_orchestrator.add_chain(chain)
        w.manager._handle_event(self.chain_event(chain))
        deadline = chain.stage_deadline
        event = dict(self.chain_event(chain),event_time=deadline)
        w.clock.advance(300)
        w.manager.watch_orchestrator.maintenance()
        self.assertTrue(chain.deadline_elapsed)
        w.restart_manager()
        self.assertNotIn('expired',w.manager._handle_event(event))
        self.assertNotIn(chain.chain_id,w.manager.timed_chains)

    def test_intermediate_stage_does_not_extend_final_deadline(self):
        w = self.w
        chain = w.chain.TimedChainSpec('CHAIN:DEADLINE','OFFLINE_PRIVATE','BTCUSD',
            (w.chain.ChainTriggerSpec('WONBI_TOUCH','1m',next_window_sec=60),
             w.chain.ChainTriggerSpec('FVG_NEW','1m',next_window_sec=120),
             w.chain.ChainTriggerSpec('BAR_CLOSE','1m')),final_action='NOTIFY',created_at=w.clock.time())
        w.manager.watch_orchestrator.add_chain(chain)
        w.manager._handle_event(self.chain_event(chain))
        deadline = chain.stage_deadline
        w.clock.advance(50)
        w.manager._handle_event(self.chain_event(chain))
        self.assertEqual(chain.stage_deadline,deadline)
        before = len(w.http.deliveries)
        reply = w.manager._handle_event(dict(self.chain_event(chain),event_time=deadline+.001))
        self.assertTrue(reply['expired'])
        self.assertEqual(len(w.http.deliveries),before)
        missing = self.chain_event(chain)
        missing.pop('event_time')
        self.assertFalse(w.manager._handle_event(missing)['ok'])

    def test_final_oz_completion_must_be_within_chain_deadline(self):
        w = self.w
        chain = self.chain_spec(action='OZ')
        w.manager.watch_orchestrator.add_chain(chain)
        w.manager._handle_event(self.chain_event(chain))
        deadline = chain.stage_deadline
        w.clock.advance(50)
        w.manager._handle_event(self.chain_event(chain))
        self.assertEqual(chain.stage_deadline,deadline)
        event = {'kind':'FINAL_ALERT','strategy':'OZ','watch_ids':[chain.active_child_id],
                 'message':'final-deadline','request_chat_id':'OFFLINE_PRIVATE'}
        w.clock.advance(300)
        w.manager.watch_orchestrator.maintenance()
        w.restart_manager()
        before = len(w.http.deliveries)
        late = w.manager._handle_event(dict(event,event_time=deadline+.001,event_id='too-late'))
        self.assertTrue(late['expired'])
        self.assertEqual(len(w.http.deliveries),before)
        valid = w.manager._handle_event(dict(event,event_time=deadline,event_id='on-time'))
        self.assertTrue(valid['delivered'])
        self.assertEqual(len(w.http.deliveries),before+1)

    def test_deadline_boundary_matrix(self):
        w = self.w
        for delta in (-.001, 0, .001):
            for maintenance_first in (False, True):
                for restart in (False, True):
                    with self.subTest(delta=delta, maintenance_first=maintenance_first, restart=restart):
                        chain = self.chain_spec()
                        chain.chain_id += f':{delta}:{maintenance_first}:{restart}'
                        w.manager.watch_orchestrator.add_chain(chain)
                        w.manager._handle_event(self.chain_event(chain))
                        event = dict(self.chain_event(chain), event_time=chain.stage_deadline + delta)
                        w.clock.advance(120)
                        if maintenance_first:
                            w.manager.watch_orchestrator.maintenance()
                        if restart:
                            w.restart_manager()
                        reply = w.manager._handle_event(event)
                        self.assertEqual(bool(reply.get('expired')), delta > 0)
                        self.assertEqual(chain.chain_id in w.manager.timed_chains, delta > 0)

    def test_unordered_distinct_conditions_and_expiry_boundary(self):
        w = self.w
        latch = w.chain.UnorderedConditionLatch
        state = latch.new_state()
        def hit(key, timestamp):
            return latch.register(state, correlation_key="LONG", condition_key=key, event_ts=timestamp,
                                  token=key, required_count=2, window_sec=60, now=w.clock.time())
        self.assertFalse(hit("A", w.clock.time())["matched"])
        self.assertEqual(hit("A", w.clock.time())["count"], 1)
        w.clock.advance(60)
        self.assertTrue(hit("B", w.clock.time())["matched"])
        w.clock.advance(.001)
        self.assertFalse(hit("B", w.clock.time())["matched"])

    def test_command_alias_macro_and_invalid_reload(self):
        w = self.w
        ci = w.manager.command_interpreter
        macro = ci.expand_condition_macro("기본더블비", "5m", "LONG")
        self.assertEqual([x["kind"] for x in macro["conditions"]], ["TREND", "WONBI"])
        self.assertEqual(ci.normalize_command_text("골드 5분 wonbi notify"), "골드 5분 원비 알려줘")
        previous = ci.language()
        ci.aliases_path.write_text("{broken", encoding="utf-8")
        loaded = w.modules["command_interpreter"].load_command_language(ci.aliases_path, previous)
        self.assertIs(loaded, previous)

    def test_stale_staff_does_not_expire_trend_fact(self):
        w = self.w
        spec = self.spec()
        w.feed(w.market())
        self.trend_once()
        w.clock.advance(300)
        self.assertIsNone(w.trend_engine.staff.request("BTCUSD", ["1m"], ["HMA"]))
        before = dict(w.manager.trend_facts[('BTCUSD','1m')])
        self.assertIsNone(w.manager._condition_status_locked(spec, spec.conditions[0], "LONG")[0])
        self.assertEqual(w.manager.trend_facts[('BTCUSD','1m')], before)
        w.feed(w.market())
        self.assertIsNone(w.manager._condition_status_locked(spec, spec.conditions[0], "LONG")[0])
        self.trend_once()
        self.assertTrue(w.manager._condition_status_locked(spec, spec.conditions[0], "LONG")[0])

    def test_mt5_optional_missing_slots_are_request_scoped(self):
        w = self.w
        for family in ('EMA','PRICE','RSI','STO','DI'):
            with self.subTest(family=family):
                frame = w.market()
                columns = w.staff.MT5_REQUIRED_BY_INDICATOR[family]
                # MQL EMPTY_VALUE is DBL_MAX on the actual 45-slot binary boundary.
                for col in columns: frame[col] = 1.7976931348623157e308
                w.feed(frame)
                response = w.data_request({'symbol':'BTCUSD','timeframes':['1m'],'indicators':[]})
                self.assertEqual(response['1m'].iloc[-1]['close'], 120)
                self.assertFalse(response['1m'].attrs['indicator_validity'][family])
                self.assertTrue(response['1m'][columns].isna().all().all())
                with self.assertRaises(RuntimeError):
                    w.data_request({'symbol':'BTCUSD','timeframes':['1m'],'indicators':[family]})
                w.feed(w.market())
                self.assertTrue(w.data_request({'symbol':'BTCUSD','timeframes':['1m'],'indicators':[family]}))

    def test_health_gap_without_prior_stale_poll_requires_resync(self):
        w = self.w
        spec = self.spec()
        w.feed(w.market())
        self.trend_once()
        w.clock.advance(31)
        w.feed(w.market())
        self.assertIsNone(w.manager._condition_status_locked(spec,spec.conditions[0],'LONG')[0])
        self.trend_once()
        self.assertTrue(w.manager._condition_status_locked(spec,spec.conditions[0],'LONG')[0])

    def test_windows_named_pipe_actual_receiver_accepts_snapshot(self):
        import os, threading, time, uuid
        import numpy as np
        if os.name != 'nt': self.skipTest('Windows Named Pipe')
        w = self.w
        cache = type(w.server.cache)(r'\\.\pipe\MosesAudit_' + uuid.uuid4().hex)
        stop = threading.Event()
        df = w.market()
        cols = w.staff.PIPE_VALUE_COLUMNS
        values = np.array([[row.get(c, np.nan) for c in cols] for _, row in df.iterrows()], dtype='<f8')
        times = np.array([pd.Timestamp(t).timestamp() for t in df.time], dtype='<i8')
        volumes = np.array(df.volume, dtype='<i8')
        sym, tf = b'BTCUSD', b'1m'
        raw = w.staff.wire.pack_v2('BTCUSD', '1m', times, volumes, values, seq=1)
        client = None
        cache.start(stop)
        try:
            until = time.monotonic() + 5
            while client is None:
                try: client = open(cache.pipe_name, 'wb', buffering=0)
                except FileNotFoundError:
                    if time.monotonic() >= until: raise
                    time.sleep(.01)
            client.write(raw)
            until = time.monotonic() + 5
            while not cache.has('BTCUSD', '1m') and time.monotonic() < until: time.sleep(.01)
            self.assertTrue(cache.has('BTCUSD', '1m'))
            self.assertEqual(len(w.raw_frame('BTCUSD', '1m', cache)), len(df))
        finally:
            stop.set()
            if client is not None: client.close()
            cache._thread.join(5)
        self.assertFalse(cache._thread.is_alive())

    def test_mt5_missing_indicator_handles_do_not_remove_ohlc_feed(self):
        code = (ROOT/'program/MT5/THE_STAFF_OF_MOSES.mq5').read_text(encoding='utf-8')
        build = code.split('bool BuildFeed(')[1].split('return true;')[0]
        indicator_checks = build.split('if(f.ema20==INVALID_HANDLE')[1]
        self.assertNotIn('return false;', indicator_checks)
        self.assertNotIn('ReleaseFeed(f);', indicator_checks)
        self.assertIn('return false;', build.split('if(f.ema20==INVALID_HANDLE')[0])

    def test_mt5_producer_guards_every_optional_slot(self):
        import re
        code = (ROOT/'program/MT5/THE_STAFF_OF_MOSES.mq5').read_text(encoding='utf-8')
        failure = code.split('if(!ok_ema || !ok_price')[1].split('int payload_count=')[0]
        self.assertNotIn('return false',failure)
        slots = {int(n): expr for n,expr in re.findall(r'PutIPCValue\(values,row,\s*(\d+),(.*?)\);',code)}
        expected = {**dict.fromkeys(range(4,8),'ema'), **dict.fromkeys([21,22,23,36,37,38],'price'),
                    **dict.fromkeys([24,25,26,27,39,40],'rsi'), **dict.fromkeys([28,29,30,31,41,42],'sto'),
                    **dict.fromkeys([32,33,34,35,43,44],'di')}
        for slot, family in expected.items():
            self.assertRegex(slots[slot],r'^\(ok_'+family+r' \? \w+\[i\] : EMPTY_VALUE\)$')

    def test_telegram_failure_keeps_watch(self):
        w = self.w
        w.watch.add_manual(["1m"], symbol="BTCUSD", watch_id="OZARM:AUDIT", validation_mode="BLIND")
        w.http.status_code = 500
        self.assertFalse(w.watch.try_fire("BTCUSD", "1m", "LONG", "S", "BLIND"))
        self.assertTrue(w.watch._watches)

    def test_end_to_end_all_facts_composer_oz_notification(self):
        w = self.w
        spec = self.spec(("TREND", "FVG", "SWEEP"), action="OZ")
        w.manager._sync_engine_subscriptions()
        w.commands()
        w.feed_all("OUT")
        w.trend_engine.run_watch_once(w.trend_registry.snapshot())
        w.fvg_engine.run_watch_once(w.fvg_registry.snapshot())
        self.assertFalse(w.manager._active_children)  # ALL gate not yet satisfied
        for sw in w.sweep_registry.snapshot(): w.sweep_engine.run_spec_once(sw)
        w.sweep_engine.flush_state()
        self.assertTrue(w.manager._active_children)
        w.commands()
        w.pump(w.sweep_event_worker)
        for phase in ("OUT", "IN", "NEXT_BAR"):
            w.feed_all(phase)
            revision, tfs = w.watch.snapshot_for_symbol("BTCUSD", "BLIND", "OZ")
            w.monitor.run_once(revision, tfs)
        finals = [x["request"] for x in w.bus.trace if x["request"].get("kind") == "FINAL_ALERT"]
        self.assertEqual(len(finals), 1)
        expected = json.loads((FIXTURES/"replay.json").read_text(encoding="utf-8"))["expected_final"]
        self.assertEqual({k: finals[0].get(k) for k in expected}, expected)
        self.assertEqual(finals[0]["request_chat_id"], "OFFLINE_PRIVATE")
        self.assertFalse(w.manager._active_children)
        self.assertTrue(any("S급 LONG" in x["text"] for x in w.http.deliveries))
        w.monitor.run_once(revision, tfs)
        self.assertEqual(len([x for x in w.bus.trace if x["request"].get("kind") == "FINAL_ALERT"]), 1)

    def test_actual_special_plugins_load(self):
        w = self.w
        w.restart_manager()
        self.assertEqual(len(w.manager.special_modules), 6)
        self.assertTrue(w.manager.official_specs)
        self.assertTrue(w.manager.official_chain_specs)
        self.assertTrue(w.manager._active_children)

    def test_invalid_event_and_unknown_action_contract(self):
        w = self.w
        self.assertEqual(w.manager._handle_event([])["error"], "invalid_event")
        self.assertEqual(w.manager._handle_event({"kind": "BOGUS"})["error"], "unknown_kind:BOGUS")
        w.fvg_worker._apply({"action": "BOGUS"})
        self.assertFalse(w.fvg_registry.snapshot())

    def test_jsonl_partial_line_waits_for_completion(self):
        w = self.w
        raw = json.dumps({"action": "FVG_WATCH", "watch_id": "partial", "symbol": "BTCUSD", "source_tf": "1m"})
        with w.fvg_worker.path.open("a", encoding="utf-8") as f: f.write(raw[:25])
        w.pump(w.fvg_worker)
        with w.fvg_worker.path.open("a", encoding="utf-8") as f: f.write(raw[25:] + "\n")
        w.pump(w.fvg_worker)
        self.assertTrue(w.fvg_registry.snapshot())

    def test_jsonl_all_workers_utf8_partial_and_malformed_records(self):
        w = self.w
        for worker in (w.trend_worker, w.fvg_worker, w.sweep_worker, w.oz_worker, w.sweep_event_worker):
            with self.subTest(worker=type(worker).__name__):
                received = []
                if worker is w.sweep_event_worker:
                    worker.controller.apply_external_event = received.append
                else:
                    worker._apply = received.append
                worker.path.write_bytes(b'')
                worker._offset = 0
                raw = json.dumps({'action': 'SWEEP_EVENT', 'value': '한글'}, ensure_ascii=False).encode('utf-8')
                split = raw.index('한'.encode('utf-8')) + 1
                with worker.path.open('ab') as f: f.write(raw[:split])
                w.pump(worker)
                self.assertEqual(received, [])
                self.assertEqual(worker._offset, 0)
                with worker.path.open('ab') as f: f.write(raw[split:])
                w.pump(worker)
                self.assertEqual(received, [])
                with worker.path.open('ab') as f: f.write(b'\nmalformed\n' + raw + b'\n')
                w.pump(worker)
                self.assertEqual(len(received), 2)
                w.pump(worker)
                self.assertEqual(len(received), 2)

    def test_manager_restart_recovers_unchanged_fvg_and_sweep(self):
        w = self.w
        self.sweep_setup()
        w.fvg_engine.process_watch_result(w.fvg.build_fvg_state("BTCUSD", "1m", w.market()))
        self.assertTrue(w.manager.fvg_touches)
        self.assertTrue(w.manager.sweep_touches)
        w.restart_manager()
        w.fvg_engine.process_watch_result(w.fvg.build_fvg_state("BTCUSD", "1m", w.market()))
        for spec in w.sweep_registry.snapshot(): w.sweep_engine.run_spec_once(spec)
        self.assertTrue(w.manager.fvg_touches)
        self.assertTrue(w.manager.sweep_touches)

    def test_metric_freshness_independent_from_trend_state(self):
        w = self.w
        w.manager._handle_event({"kind": "TREND_METRIC_STATE", "strategy": "TREND", "symbol": "BTCUSD",
            "source_tf": "1m", "metrics": {"adx": 30, "price": float("nan"), "made_up": 2}})
        self.assertIsNotNone(w.manager._trend_metric_fact_fresh_locked("BTCUSD", "1m", "adx"))
        self.assertEqual(len(w.manager.trend_metric_facts), 1)
        w.clock.advance(5)
        self.assertIsNotNone(w.manager._trend_metric_fact_fresh_locked("BTCUSD", "1m", "adx"))
        w.clock.advance(.001)
        self.assertIsNone(w.manager._trend_metric_fact_fresh_locked("BTCUSD", "1m", "adx"))

    def test_oz_out_in_equal_band_is_in_and_strict_breaker(self):
        w = self.w
        row = w.market().iloc[-1].copy()
        row["RSI_val"] = row["RSI_db"]
        self.assertEqual(w.oz.percentile_states(row)["RSI"], "IN")
        df = w.market("NEXT_BAR")
        cross = df.iloc[-2].time
        self.assertFalse(w.monitor._breaker_bo_break_trigger(df, "LONG", 118.2, cross))
        df.loc[len(df)-1, "low"] = 118.199
        self.assertTrue(w.monitor._breaker_bo_break_trigger(df, "LONG", 118.2, cross))

    def test_oz_silent_completion_without_environment(self):
        w = self.w
        for phase in ("OUT", "IN", "NEXT_BAR"):
            w.feed_all(phase)
            w.monitor.run_once(0, ())
        self.assertTrue(w.monitor.candidates[("1m", "LONG")].completed_outside_window)
        self.assertFalse(any(x["request"].get("kind") == "FINAL_ALERT" for x in w.bus.trace))

    def test_oz_normal_and_regime_validation_from_same_family(self):
        w = self.w
        df = w.market("NEXT_BAR")
        monitor = w.oz.OZMonitor("BTCUSD", w.config, w.sender, w.watch)
        for phase in ("OUT", "IN"):
            frame = w.market(phase)
            monitor._process_hma_cross("1m", frame)
            monitor._process_out_in("1m", frame)
        middle, upper = df.copy(), df.copy()
        for col in ("RSI_val", "STO_val", "DI_val", "price_hma_6"): middle.loc[len(middle)-1, col] = 30
        middle.loc[len(middle)-1, "open"] = 121
        data = {"1m": w.compat_module.apply_requested_features(df, w.oz.REQUIRED_INDS),
                "3m": w.compat_module.apply_requested_features(middle, w.oz.REQUIRED_INDS),
                "6m": w.compat_module.apply_requested_features(upper, w.oz.REQUIRED_INDS)}
        result = monitor._candidate_completion_decision(data, "1m", "LONG", require_external=False, commit_validation=False)
        self.assertIsNotNone(result)
        self.assertEqual(result.grade, "S")
        monitor.trigger_mode = "REGIME"
        for col in ("RSI_regime_slope", "STO_regime_slope", "DI_regime_slope", "price_regime_slope"):
            data["6m"].loc[len(upper)-1, col] = .01
        self.assertIsNotNone(monitor._candidate_completion_decision(data, "1m", "LONG", require_external=False, commit_validation=False))
        for col in ("RSI_regime_slope", "STO_regime_slope", "DI_regime_slope", "price_regime_slope"):
            data["6m"].loc[len(upper)-1, col] = -1
        self.assertIsNone(monitor._candidate_completion_decision(data, "1m", "LONG", require_external=False, commit_validation=False))

    def test_filter_overlap_survives_restart_and_expires(self):
        w = self.w
        chain = w.chain.TimedChainSpec("CHAIN:FILTER", "OFFLINE_PRIVATE", "BTCUSD",
            (w.chain.ChainTriggerSpec("WONBI_TOUCH", "1m", valid_sec=60),
             w.chain.ChainTriggerSpec("FVG_NEW", "1m", valid_sec=120)),
            final_action="OZ", oz_tfs=("1m",), order_mode="FILTER", created_at=w.clock.time())
        w.manager.watch_orchestrator.add_chain(chain)
        def event(c, stage):
            return {"kind": "GENERIC_TRIGGER", "chain_id": c.chain_id, "chain_stage": stage,
                    "watch_id": c.current_watch_ids[str(stage)], "event_time": w.clock.time(), "direction": "LONG"}
        w.manager._handle_event(event(chain, 0))
        w.restart_manager()
        chain = w.manager.timed_chains[chain.chain_id]
        w.clock.advance(10)
        reply = w.manager._handle_event(event(chain, 1))
        self.assertTrue(reply["matched"])
        self.assertEqual(chain.active_until, w.clock.time() + 50)
        w.clock.advance(50)
        w.manager.watch_orchestrator.maintenance()
        self.assertNotIn(chain.chain_id, w.manager.timed_chains)

    def test_generic_bar_close_through_orchestrator_to_notification(self):
        w = self.w
        chain = w.chain.TimedChainSpec("CHAIN:BAR", "OFFLINE_PRIVATE", "BTCUSD",
            (w.chain.ChainTriggerSpec("BAR_CLOSE", "1m"),), final_action="NOTIFY", created_at=w.clock.time())
        w.manager.watch_orchestrator.add_chain(chain)
        w.commands()
        generic = w.oz.GenericConditionMonitor("BTCUSD", w.config, w.generic)
        watch = w.generic._watches[chain.current_watch_id]
        for phase in ("IN", "NEXT_BAR"):
            w.feed(w.market(phase))
            data = generic.client.request("BTCUSD", ["1m"], [])
            generic._bar_close(watch, "1m", data["1m"])
        self.assertNotIn(chain.chain_id, w.manager.timed_chains)
        events = [x["request"] for x in w.bus.trace if x["request"].get("kind") == "GENERIC_TRIGGER"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["watch_type"], "BAR")
        self.assertTrue(any("봉마감" in x["text"] for x in w.http.deliveries))

    def test_fvg_atr_bounds_are_inclusive(self):
        w = self.w
        rows = [{"time": pd.Timestamp(1789387200+i*60, unit="s"), "open": 100.0, "high": 101.0,
                 "low": 99.0, "close": 100.0, "volume": 100} for i in range(20)]
        df = pd.DataFrame(rows)
        for gap, expected in ((.5, True), (.49999, False), (3.5, True), (3.50001, False)):
            with self.subTest(gap=gap):
                sample = df.copy()
                sample.loc[19, ["low", "high", "close"]] = [101+gap, 103+gap, 102+gap]
                self.assertEqual(bool(w.fvg.add_fvg_local(sample).iloc[-1].is_bull_fvg), expected)

    def test_sweep_selects_outermost_and_consumes_other_levels(self):
        w = self.w
        spec = w.sweep.SweepSpec("OUTERMOST", "BTCUSD", "1m")
        detector = w.sweep.ExternalLiquidityDetector(spec)
        levels = [{"id": str(p), "direction": "LONG", "level_code": "PDL", "level_name": "fixture", "price": p} for p in (118.8, 119.2)]
        events = detector.process(w.market(), levels)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["level_price"], 118.8)
        self.assertTrue(detector.states["119.2"]["consumed"])
        self.assertEqual(detector.process(w.market(), levels), [])

    def test_lost_final_ack_does_not_duplicate_delivery(self):
        w = self.w
        w.bus.lose_ack_next.add(w.manager.alert_endpoint)
        self.assertFalse(w.sender.send("ACK-loss", require_delivery=True, event_id='loss-1'))
        w.restart_oz()
        w.restart_manager()
        self.assertTrue(w.sender.send("ACK-loss", require_delivery=True, event_id='loss-1'))
        self.assertEqual(sum(x["text"] == "ACK-loss" for x in w.http.deliveries), 1)

    def test_delivery_identity_per_recipient_and_unknown_outcome(self):
        w = self.w
        event = {'kind':'FINAL_ALERT', 'strategy':'OZ', 'event_id':'recipient-test', 'message':'same'}
        for recipient in ('A', 'B', 'A'):
            self.assertTrue(w.manager._handle_event(dict(event, request_chat_id=recipient))['delivered'])
        self.assertEqual([d['chat_id'] for d in w.http.deliveries], ['A','B'])
        key = w.kim.identity('uncertain', 'A')
        w.manager.notifier.deliveries.put(key, {'status':'sending'})
        self.assertFalse(w.manager.notifier.send('uncertain', 'A', event_id='uncertain'))
        self.assertEqual(len(w.http.deliveries), 2)

    def test_fvg_thirty_closed_bars_and_latest_three(self):
        w = self.w
        df = w.market()
        for i in range(len(df)):
            df.loc[i, ["open", "high", "low", "close"]] = [100+i*2, 102+i*2, 100+i*2, 101+i*2]
        result = w.fvg.build_fvg_state("BTCUSD", "1m", df)
        self.assertEqual(len(result["eligible_zone_ids"]), 30)
        self.assertEqual(len(result["zones"]), 3)
        self.assertEqual([z["age_bars"] for z in result["zones"]], [0, 1, 2])

    def test_fvg_manager_restart_then_engine_restart_repairs_touch(self):
        w = self.w
        result = w.fvg.build_fvg_state("BTCUSD", "1m", w.market())
        w.fvg_engine.process_watch_result(result)
        w.restart_manager()
        w.fvg_engine.process_watch_result(result)
        self.assertTrue(w.manager.fvg_touches)
        w.restart_engines("FVG")
        w.fvg_engine.process_watch_result(result)
        self.assertTrue(w.manager.fvg_touches)

    def test_oz_restart_external_state_replay_keeps_touch_identity(self):
        w = self.w
        self.sweep_setup()
        before = {k: (v.event_time, v.status) for k, v in w.external._states.items()}
        self.assertTrue(before)
        w.restart_oz()
        w.pump(w.sweep_event_worker)
        self.assertEqual({k: (v.event_time, v.status) for k, v in w.external._states.items()}, before)

    def test_chain_notify_failure_preserves_pending_completion(self):
        w = self.w
        chain = w.chain.TimedChainSpec("CHAIN:FAIL", "OFFLINE_PRIVATE", "BTCUSD",
            (w.chain.ChainTriggerSpec("WONBI_TOUCH", "1m"),), final_action="NOTIFY", created_at=w.clock.time())
        w.manager.watch_orchestrator.add_chain(chain)
        w.http.status_code = 500
        reply = w.manager._handle_event(self.chain_event(chain))
        self.assertTrue(reply["ok"])
        self.assertFalse(reply["delivered"])
        self.assertNotIn(chain.chain_id, w.manager.timed_chains)
        self.assertEqual(len(w.manager.watch_orchestrator.pending_notifications), 1)
        w.restart_manager()
        self.assertNotIn(chain.chain_id, w.manager.timed_chains)
        self.assertEqual(len(w.manager.watch_orchestrator.pending_notifications), 1)
        w.http.status_code = 200
        before = len(w.http.deliveries)
        w.manager.watch_orchestrator.retry_notifications()
        self.assertFalse(w.manager.watch_orchestrator.pending_notifications)
        self.assertEqual(len(w.http.deliveries), before + 1)
        w.restart_manager()
        w.manager.watch_orchestrator.retry_notifications()
        self.assertEqual(len(w.http.deliveries), before + 1)

    def test_filter_notify_failure_preserves_pending_completion(self):
        w = self.w
        chain = w.chain.TimedChainSpec('CHAIN:FILTER:NOTIFY','OFFLINE_PRIVATE','BTCUSD',
            (w.chain.ChainTriggerSpec('WONBI_TOUCH','1m',valid_sec=60),),
            final_action='NOTIFY',order_mode='FILTER',created_at=w.clock.time())
        w.manager.watch_orchestrator.add_chain(chain)
        w.http.status_code = 500
        event = dict(self.chain_event(chain),watch_id=chain.current_watch_ids['0'],chain_stage=0)
        w.manager._handle_event(event)
        self.assertEqual(len(w.manager.watch_orchestrator.pending_notifications),1)
        self.assertNotIn(chain.chain_id,w.manager.timed_chains)
        w.restart_manager()
        w.http.status_code = 200
        before = len(w.http.deliveries)
        w.manager.watch_orchestrator.retry_notifications()
        self.assertFalse(w.manager.watch_orchestrator.pending_notifications)
        self.assertEqual(len(w.http.deliveries),before+1)
        w.restart_manager()
        w.manager.watch_orchestrator.retry_notifications()
        self.assertEqual(len(w.http.deliveries),before+1)

    def test_chain_delivery_commit_crash_does_not_resend(self):
        w = self.w
        token = 'completed-before-crash'
        orchestrator = w.manager.watch_orchestrator
        orchestrator.pending_notifications[token] = {'text':'completion','recipient':'OFFLINE_PRIVATE','chain_id':'old'}
        orchestrator.save_state_locked()
        self.assertTrue(w.manager.send_telegram('completion','OFFLINE_PRIVATE',event_id=token))
        before = len(w.http.deliveries)
        w.restart_manager()
        w.manager.watch_orchestrator.retry_notifications()
        self.assertEqual(len(w.http.deliveries), before)
        self.assertFalse(w.manager.watch_orchestrator.pending_notifications)

    def test_unknown_sweep_selector_is_rejected(self):
        w = self.w
        for values in (('VAH',), ('VAL',), ('PDL','VAH'), ('ALL','VAH'), ('BOGUS',), ()):
            with self.subTest(values=values), self.assertRaises(ValueError): w.sweep.selector_codes(values)
        self.assertEqual(w.sweep.selector_codes(('PDL',)), {'PDL'})
        self.assertEqual(w.sweep.selector_codes(('ALL',)), w.sweep.ALL_LEVEL_CODES)
        self.assertEqual(w.sweep.selector_codes(None), w.sweep.ALL_LEVEL_CODES)
        with self.assertRaises(ValueError): w.kim.ConditionSpec('SWEEP','1m',side='VAH')
        for value in ('VAH','VAL'):
            self.assertNotIn(value,w.kim.DEFAULT_SWEEP_LEVELS)
            w.manager.handle_command('BTCUSD 1분 '+value+' 터치 알려줘','OFFLINE_PRIVATE')
        self.assertFalse(w.manager.manual_specs)
        self.assertTrue(all('지원하지 않는' in x['text'] for x in w.http.deliveries))

    def test_sweep_legacy_invalid_selector_does_not_discard_valid_watch(self):
        w = self.w
        state = {'version':2, 'watches':{'bad':{'symbol':'BTCUSD','source_tf':'1m','levels':['VAH']},
                                         'good':{'symbol':'BTCUSD','source_tf':'1m','levels':['PDL']}}}
        w.sweep_registry._state_path.write_text(json.dumps(state),encoding='utf-8')
        w.restart_engines('SWEEP')
        self.assertEqual([spec.watch_id for spec in w.sweep_registry.snapshot()],['good'])
        aliases = json.loads((ROOT/'program/command_aliases.json').read_text(encoding='utf-8'))
        self.assertNotIn('전일val',aliases['phrase_aliases'])
        self.assertNotIn('전일vah',aliases['phrase_aliases'])

    def test_oz_atr_gate_boundary_and_confirmed_survival(self):
        w = self.w
        spec = self.sweep_setup()
        data = w.data_request({"symbol": "BTCUSD", "timeframes": ["1m"]})
        w.external.update_market("BTCUSD", data, [spec.watch_id])
        state = w.external.state(spec.watch_id, "LONG")
        self.assertEqual(state.status, "ACTIVE")
        self.assertTrue(w.external.validate_true_b0(spec.watch_id, "LONG", state.level_price - state.max_distance))
        data["1m"].loc[len(data["1m"])-1, "low"] = state.level_price - state.max_distance - 1
        w.external.update_market("BTCUSD", data, [spec.watch_id])
        self.assertEqual(w.external.state(spec.watch_id, "LONG").status, "CONFIRMED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
