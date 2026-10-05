"""Slow startup: MQL readiness gates and Python deferred-feed replay contracts."""
import logging
import unittest
from unittest.mock import patch
from harness import ROOT, World

class SlowFeed(unittest.TestCase):
    def test_mql_skips_uncalculated_buffers_before_copy(self):
        code=(ROOT/'program/MT5/THE_STAFF_OF_MOSES.mq5').read_text(encoding='utf-8')
        body=code.split('bool CopyOneBuffer(')[1].split('int CreateCustom(')[0]
        self.assertIn('BarsCalculated(handle) < count',body)
        self.assertLess(body.index('BarsCalculated(handle)'),body.index('CopyBuffer(handle'))
        self.assertIn('ArrayInitialize(out, EMPTY_VALUE)',body)

    def test_mql_partial_history_and_fair_timer_contract(self):
        code=(ROOT/'program/MT5/THE_STAFF_OF_MOSES.mq5').read_text(encoding='utf-8')
        # The LIVE payload builder is shared by PublishFeed and the backtest pipe capture.
        publish=code.split('bool BuildStaffPayload(')[1].split('int OnInit()')[0]
        self.assertIn('BuildStaffPayload(',code.split('bool PublishFeed(')[1].split('int OnInit()')[0])
        self.assertIn('SERIES_BARS_COUNT',publish)
        self.assertIn('MathMin(count,available)',publish)
        self.assertIn('if(got < 250)',publish)
        timer=code.split('void OnTimer()')[1]
        self.assertLess(timer.index('g_feed_cursor='),timer.index('PublishFeed('))
        self.assertIn('STAFF_WORK_BUDGET_MS',timer)
        self.assertIn('next_poll_ms',timer)
        self.assertNotIn('Sleep(',timer)

    def test_wait_reply_is_throttled_but_never_success_and_recovers(self):
        with World() as w:
            request={'symbol':'BTCUSD','timeframes':['12h'],'indicators':[]}
            with patch.object(w.staff.logging,'warning') as warning:
                for _ in range(50):
                    with self.assertRaises(w.staff.SnapshotUnavailable) as caught:
                        w.data_request(request)
                    reply=w.server._feed_wait_reply(caught.exception)
                    self.assertIn('error',reply)
                    self.assertEqual(reply['error_code'],'FEED_NOT_READY')
                self.assertEqual(warning.call_count,1)
                w.clock.advance(31)
                w.server._feed_wait_reply(caught.exception)
                self.assertEqual(warning.call_count,2)
            self.assertFalse(w.server.cache.has('BTCUSD','12h'))
            w.feed(w.market(),tf='12h')
            self.assertIn('12h',w.data_request(request))
            self.assertFalse(w.server._feed_wait_logs)

    def test_delayed_timeframe_does_not_block_ready_ohlc_or_fake_indicator(self):
        with World() as w:
            frame=w.market()
            for col in w.staff.MT5_REQUIRED_BY_INDICATOR['RSI']: frame[col]=float('nan')
            w.feed(frame,tf='10m')
            for _ in range(5):
                with self.assertRaises(RuntimeError):
                    w.data_request({'symbol':'BTCUSD','timeframes':['12h'],'indicators':[]})
                self.assertIn('10m',w.data_request({'symbol':'BTCUSD','timeframes':['10m'],'indicators':[]}))
                with self.assertRaises(RuntimeError):
                    w.data_request({'symbol':'BTCUSD','timeframes':['10m'],'indicators':['RSI']})
            w.feed(w.market(),tf='12h')
            w.feed(w.market(),tf='10m')
            self.assertIn('10m',w.data_request({'symbol':'BTCUSD','timeframes':['10m'],'indicators':['RSI']}))
            self.assertIn('12h',w.data_request({'symbol':'BTCUSD','timeframes':['12h'],'indicators':[]}))
            w.clock.advance(31)
            with self.assertRaises(RuntimeError):
                w.data_request({'symbol':'BTCUSD','timeframes':['12h'],'indicators':[]})

if __name__=='__main__': unittest.main()
