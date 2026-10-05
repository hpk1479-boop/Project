"""Bound duplicated work without loosening data validity or ACK completion."""
import logging
import unittest
from unittest.mock import patch
from harness import World

class AckPressure(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.w=World()
        self.addCleanup(self.w.close)
        self.w.isolate_specs()

    def test_missing_last_timeframe_does_no_discarded_feature_work(self):
        w=self.w
        w.feed(w.market(),tf='1m'); w.feed(w.market(),tf='10m')
        with patch.object(w.compat_module,'apply_requested_features',wraps=w.compat_module.apply_requested_features) as calculate:
            for _ in range(4):
                with self.assertRaises(w.staff.SnapshotUnavailable):
                    w.data_request({'symbol':'BTCUSD','timeframes':['1m','10m','12h'],
                                     'indicators':['RSI','STO','DI','PRICE']})
            self.assertEqual(calculate.call_count,0)
        self.assertIn('10m',w.data_request({'symbol':'BTCUSD','timeframes':['10m'],'indicators':[]}))

    def test_health_reads_metadata_without_copying_market_frames(self):
        w=self.w;w.feed(w.market())
        with patch.object(w.snapshots,'frame',side_effect=AssertionError('health copied OHLC')):
            reply=w.data_request({'kind':'SOURCE_HEALTH','symbol':'BTCUSD','timeframes':['1m','12h']})
        self.assertEqual(reply['feeds']['1m']['status'],'FRESH')
        self.assertEqual(reply['feeds']['12h']['status'],'UNAVAILABLE')
        self.assertEqual(reply['feeds']['1m']['age_seconds'],0)
        self.assertEqual(reply['feeds']['1m']['stale_seconds'],30)
        reply['feeds']['1m']['indicators'].clear()
        self.assertTrue(w.raw_frame('BTCUSD','1m').attrs['indicator_validity'])

    def populate(self):
        w=self.w;w.feed(w.market())
        scope=w.kim.fact_scope({'strategy':'TREND','symbol':'BTCUSD','source_tf':'1m'})
        w.manager._source_bindings[scope]=w.kim.source_health({'1m':w.raw_frame('BTCUSD','1m')})
        specs=[]
        for i in range(24):
            s=w.kim.StrategySpec('PRESSURE:'+str(i),'Pressure','BTCUSD',
                (w.kim.ConditionSpec('TREND','1m'),),('1m',),final_action='NOTIFY',
                validation_mode='BLIND',final_direction='LONG',destination='PRIVATE',
                owner_chat_id='OFFLINE_PRIVATE',persistent=True,source='PRIVATE')
            w.manager.manual_specs[s.spec_id]=s;specs.append(s)
        return specs

    def test_composer_batches_one_evaluation_health_without_cross_event_cache(self):
        self.populate();w=self.w
        original=w.manager.staff.health
        def delayed(symbol,tfs):
            w.clock.advance(5)
            return original(symbol,tfs)
        start=w.clock.monotonic()
        with patch.object(w.manager.staff,'health',side_effect=delayed) as health:
            w.manager._evaluate_symbol_locked('BTCUSD')
            self.assertEqual(health.call_count,1)
            self.assertEqual(w.clock.monotonic()-start,5)
            w.manager._evaluate_symbol_locked('BTCUSD')
            self.assertEqual(health.call_count,2)
        self.assertFalse(w.http.deliveries)

    def test_health_batch_cannot_extend_source_freshness(self):
        specs=self.populate();w=self.w
        started=w.clock.monotonic()
        health=w.manager.staff.health('BTCUSD',['1m'])
        self.assertTrue(w.manager._condition_source_usable(specs[0],specs[0].conditions[0],(health,started)))
        w.clock.advance(31)
        self.assertFalse(w.manager._condition_source_usable(specs[0],specs[0].conditions[0],(health,started)))
        self.assertTrue(w.manager._source_bindings)

if __name__=='__main__': unittest.main()
