"""Logic probes of generated strategies, never a historical prototype baseline."""
import copy
import json
import socket
import sys
import types
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / 'Part1/program'))
from lab.ai.intent import recipe_from_intent
from lab.ai_compiler import execution_plan
from lab.compiler import compile_recipe
from lab.intent_runtime import IntentMachine
from test_ai_intent_v2 import intent
from event_engine.model import FeedSnapshot, Kind
from staff_schema import PIPE_VALUE_COLUMNS


def observation(matched, token=None, at=None):
    result = {'ready': True, 'matched': matched, 'event': token is not None, 'token': token}
    if at is not None: result['at'] = at
    return result


def module_for(value):
    recipe = recipe_from_intent(value)
    source = compile_recipe(recipe, 'Test_SPECIAL777.py')
    module = types.ModuleType('Test_SPECIAL777')
    sys.modules[module.__name__] = module
    exec(compile(source, module.__name__ + '.py', 'exec'), module.__dict__)
    return module, recipe


def snapshot(stamp, *, above=True, low=98., high=102., epoch='test', seq=1):
    rows = 250
    times = np.arange(stamp-(rows-1)*60, stamp+1, 60, dtype=np.int64)
    values = np.full((rows, len(PIPE_VALUE_COLUMNS)), 100., dtype=np.float64)
    for column, value in {'open': 100., 'high': high, 'low': low, 'close': 100.,
                          'ema_50': 101. if above else 99., 'ema_200': 100.,
                          'hma_6': 102., 'hma_17': 100., 'wonbi_upper': 103.,
                          'wonbi_lower': 97., 'open_band_4_mid': 100.}.items():
        values[:, PIPE_VALUE_COLUMNS.index(column)] = value
    return FeedSnapshot(times, np.ones(rows, dtype=np.int64), values, seq, epoch, {})


class Logic(unittest.TestCase):
    def meaning(self, order='SIMULTANEOUS', count=2, final='OZ', **extra):
        value = intent()
        value['interpretation'].update(steps=[{'kind': 'SESSION_START', 'tf': '1m', 'session': 'LONDON'}]*count,
            order_mode=order, final={'kind': final} if final == 'NOTIFY' else value['interpretation']['final'], **extra)
        if order != 'SIMULTANEOUS': value['interpretation']['within_sec'] = 600
        return recipe_from_intent(value)['strategy_intent']

    def test_more_than_four_steps_compile_without_truncation(self):
        value = intent()
        value['interpretation']['steps'] *= 9
        module, recipe = module_for(value)
        self.assertEqual(len(recipe['strategy_intent']['steps']), 9)
        self.assertEqual(len(module._INTENT_PLAN['steps']), 9)

    def test_all_kinds_have_executable_lowering(self):
        samples = [
            {'kind':'TREND'}, {'kind':'MA_STATE','ma_family':'HMA','fast_period':90,'slow_period':270},
            {'kind':'MA_PRICE_STATE','ma_family':'WMA','slow_period':23},
            {'kind':'MA_SLOPE_STATE','ma_family':'SMA','slow_period':37},
            {'kind':'MA_CROSS','ma_left':'WMA17','ma_right':'SMA20'},
            {'kind':'FVG_STATE','state':'EXISTS'}, {'kind':'FVG_NEW'}, {'kind':'FVG_TOUCH'},
            {'kind':'WONBI_TOUCH'}, {'kind':'EXTERNAL_LIQUIDITY_TOUCH','level':'PDL'},
            {'kind':'SESSION_START','session':'LONDON'},
            {'kind':'OZ_ALERT','validation_mode':'BLIND','trigger_mode':'BREAKER'},
            {'kind':'REGIME_BAND','regime_family':'PRICE','relation':'SLOPE_UP'},
            {'kind':'PRICE_LEVEL','level':100.,'relation':'BREAK_UP'},
            {'kind':'LIQUIDITY_LEVEL','level':'PDL','relation':'ABOVE'}]
        for step in samples:
            with self.subTest(kind=step['kind']):
                module, recipe = module_for(intent(dict(step, tfs=['1m'])))
                self.assertEqual(module._INTENT_PLAN['steps'][0]['kind'], step['kind'])

    def test_sequence_order_window_and_distinct_occurrences(self):
        machine = IntentMachine(self.meaning('SEQUENTIAL'), 'XAUUSD+', 'LONG')
        self.assertEqual(machine.advance(0, [observation(False, 'a0', 0), observation(True, 'b0', 0)]), [])
        self.assertEqual(machine.advance(1, [observation(True, 'a1', 1), observation(False, 'b0', 0)]), [])
        self.assertEqual(machine.advance(601, [observation(False, 'a1', 1), observation(True, 'b1', 601)]), ['ARM'])
        late = IntentMachine(self.meaning('SEQUENTIAL'), 'XAUUSD+', 'LONG')
        late.advance(1, [observation(True, 'a1', 1), observation(False, 'b0', 0)])
        self.assertEqual(late.advance(602, [observation(False, 'a1', 1), observation(True, 'b1', 602)]), [])
        repeated = IntentMachine(self.meaning('SEQUENTIAL'), 'XAUUSD+', 'LONG')
        self.assertEqual(repeated.advance(0, [observation(True, 'same', 0), observation(True, 'same', 0)]), [])
        self.assertEqual(repeated.advance(1, [observation(True, 'same', 0), observation(True, 'new', 1)]), ['ARM'])

    def test_unordered_cancel_final_gate_and_checkpoint(self):
        meaning = self.meaning('UNORDERED', final_window_sec=20)
        machine = IntentMachine(meaning, 'XAUUSD+', 'LONG')
        machine.advance(0, [observation(False, 'a', 0), observation(True, 'b', 0)])
        restored = IntentMachine(meaning, 'XAUUSD+', 'LONG')
        restored.restore(machine.checkpoint())
        self.assertEqual(restored.advance(2, [observation(True, 'a', 2), observation(False, 'b', 0)]), ['ARM'])
        self.assertTrue(restored.final_allowed(22, [observation(False, 'a'), observation(False, 'b')]))
        self.assertFalse(restored.final_allowed(23, [observation(False, 'a'), observation(False, 'b')]))
        self.assertEqual(restored.advance(3, [observation(False), observation(False)], cancelled=True), ['CANCEL'])
        state = IntentMachine(self.meaning(count=1), 'XAUUSD+', 'LONG')
        state.advance(0, [observation(True)])
        self.assertFalse(state.final_allowed(1, [observation(True)], [observation(False)]))
        self.assertEqual(state.advance(2, [observation(False)]), ['CANCEL'])

    def test_any_and_one_shot(self):
        m = IntentMachine(self.meaning(global_combine='ANY', persistent=False, final='NOTIFY'), 'XAUUSD+', 'LONG')
        self.assertEqual(m.advance(0, [observation(False), observation(True)]), ['NOTIFY'])
        self.assertEqual(m.advance(1, [observation(True), observation(False)]), [])

    def test_old_event_cannot_complete_new_sequence_and_expired_state_does_not_rearm(self):
        m = IntentMachine(self.meaning('SEQUENTIAL'), 'XAUUSD+', 'LONG')
        m.advance(10, [dict(observation(True, 'a'), at=10), observation(False, 'b')])
        self.assertEqual(m.advance(11, [observation(False, 'a'), dict(observation(True, 'b'), at=5)]), [])
        self.assertEqual(m.advance(12, [observation(False, 'a'), dict(observation(True, 'b2'), at=12)]), ['ARM'])
        state = IntentMachine(self.meaning(count=1, final_window_sec=10), 'XAUUSD+', 'LONG')
        self.assertEqual(state.advance(0, [observation(True)]), ['ARM'])
        self.assertEqual(state.advance(11, [observation(True)]), ['CANCEL'])
        self.assertEqual(state.advance(12, [observation(True)]), [])
        state.advance(13, [observation(False)])
        self.assertEqual(state.advance(14, [observation(True)]), ['ARM'])

    def test_explicit_preset_lifecycle_compiles_through_common_contract(self):
        for base in ('SPECIAL4', 'SPECIAL5'):
            value = intent()
            value['interpretation'] = {'preset': base, 'symbols': ['XAUUSD+']}
            module, recipe = module_for(value)
            plan = execution_plan(recipe['strategy_intent'])
            self.assertEqual(plan['mode'], 'CANONICAL')
            self.assertTrue(any(unit.get('lifecycle') for unit in plan['meaning'].get('branches', [plan['meaning']])))
            self.assertNotIn('inherit_base_rules', recipe['strategy_intent'])
            self.assertTrue(callable(module.register))

    def test_setup_window_starts_before_touch_and_does_not_restart_at_touch(self):
        from confirmed_answers import ANSWERS
        meaning = copy.deepcopy(ANSWERS[23])
        machine = IntentMachine(meaning,'XAUUSD+','LONG')
        empty = [observation(False,'a0'),observation(False,'b0')]
        # Events carry the time they happened; the window is measured from those times.
        self.assertEqual(machine.advance(0,[observation(True,'cross',0),empty[1]],
            after_observations=[observation(False)]),[])
        self.assertEqual(machine.advance(100,[empty[0],observation(True,'new_fvg',100)],
            after_observations=[observation(False)]),[])
        self.assertEqual(machine.active_until,3700)
        self.assertFalse(machine.final_allowed(101,empty))
        self.assertEqual(machine.advance(200,empty,after_observations=[dict(observation(True),at=50)]),[])
        self.assertEqual(machine.advance(300,empty,after_observations=[dict(observation(True),at=300)]),['ARM'])
        self.assertEqual(machine.active_until,3700)
        self.assertTrue(machine.final_allowed(3700,empty))
        self.assertFalse(machine.final_allowed(3701,empty))
        self.assertEqual(machine.advance(3701,empty,after_observations=[observation(True)]),['CANCEL'])

    def test_branches_reject_unknown_fields_and_do_not_silently_run_common_steps(self):
        value = intent()
        value['interpretation']['branches']=[{'steps':[]}]
        with self.assertRaisesRegex(ValueError,'공통 steps'):
            recipe_from_intent(value)
        value['interpretation']['steps']=[]
        value['interpretation']['branches'][0]['invented']=True
        with self.assertRaisesRegex(ValueError,'허용되지'):
            recipe_from_intent(value)

    def test_direction_inherits_previous_step_instead_of_later_final_direction(self):
        value=intent()
        value['interpretation'].update(direction='BOTH',order_mode='SEQUENTIAL',within_sec=600,
            steps=[{'kind':'MA_CROSS','tfs':['1m'],'ma_left':'WMA17','ma_right':'SMA20','direction':'LONG'},
                {'kind':'FVG_NEW','tfs':['5m'],'direction':'SAME_AS_PREVIOUS_DIRECTION'},
                {'kind':'OZ_ALERT','tfs':['15m'],'direction':'SHORT','validation_mode':'NORMAL','trigger_mode':'OZ'}])
        value['interpretation']['final']['direction']='SAME_AS_PREVIOUS_DIRECTION'
        plan=execution_plan(recipe_from_intent(value)['strategy_intent'])['meaning']
        self.assertEqual(plan['steps'][1]['_resolved_direction'],'LONG')
        self.assertEqual(plan['final']['direction'],'SHORT')

    def test_partial_new_fact_is_not_false_state_or_an_arm_permission(self):
        m = IntentMachine(self.meaning(count=1),'XAUUSD+','LONG')
        m.advance(0,[observation(True)])
        unknown = {'ready':False,'matched':False,'event':False}
        self.assertEqual(m.advance(1,[unknown]),[])
        self.assertTrue(m.active)
        self.assertFalse(m.final_allowed(1,[unknown]))


class ActualEngine(unittest.TestCase):
    def setUp(self):
        self.block = patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden'))
        self.block.start()
    def tearDown(self): self.block.stop()
    def engine(self, value):
        from event_application import create_event_engine
        module, _ = module_for(value)
        engine = create_event_engine({'WONBI_SIGMA':'3', 'TELEGRAM_TOKEN':'', 'TELEGRAM_CHAT_ID':'TEST',
            'STAFF_ALLOWED_SYMBOLS':'XAUUSD+', 'TARGET_SYMBOLS':'XAUUSD+', 'LONDON':'1600-0100'},
            plugins={'Test_SPECIAL777': module}, enabled_specials=('Test_SPECIAL777',),
            symbols=('XAUUSD+',), backtest=True, oz_evaluation='all')
        return engine
    def send(self, engine, stamp, feeds):
        engine.ingress.post(Kind.MARKET_BUNDLE, source='logic', source_seq=stamp, source_time=stamp*1000,
                            payload={'symbol':'XAUUSD+', 'feeds':feeds})
        engine.run()
        self.assertFalse(engine.error_log, engine.error_log)
        return engine.strategy_state['COMPOSER']['kernels']['XAUUSD+'].manager
    def test_generated_register_to_python_board_to_notification(self):
        value = intent()
        value['interpretation']['final'] = {'kind':'NOTIFY'}
        engine = self.engine(value)
        stamp = 1790000040
        self.send(engine, stamp, {'1m': snapshot(stamp, above=False)})
        self.assertFalse([s for s in engine.signals if s.payload.get('content',{}).get('type') == 'NOTIFICATION'])
        self.send(engine, stamp+60, {'1m': snapshot(stamp+60, above=True)})
        notices = [s for s in engine.signals if s.payload.get('content',{}).get('type') == 'NOTIFICATION']
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0].payload['content']['direction'], 'LONG')
        self.send(engine, stamp+61, {'1m': snapshot(stamp+60, above=True, seq=2)})
        self.assertEqual(len([s for s in engine.signals if s.payload.get('content',{}).get('type') == 'NOTIFICATION']), 1)
    def test_fvg_dependency_is_accepted_without_accessing_private_board_projection(self):
        value = intent({'kind':'FVG_STATE','tfs':['1m'],'state':'EXISTS'})
        engine = self.engine(value); stamp = 1790000040
        manager = self.send(engine, stamp, {'1m': snapshot(stamp)})
        self.assertTrue(manager._desired_subscriptions_locked()['FVG'])
        self.send(engine, stamp+60, {'1m': snapshot(stamp+60)})
        port = manager._special_watch_handlers['TEST_SPECIAL777']
        self.assertTrue(any(key[0] == 'FVG' for key in port.fact_snapshots))
    def test_tf_independence_and_inherited_direction(self):
        value = intent({'kind':'MA_STATE','tfs':['1m','5m'],'tf_combine':'INDEPENDENT',
            'direction':'LONG','ma_family':'EMA','fast_period':50,'slow_period':200})
        value['interpretation'].update(direction='BOTH')
        value['interpretation']['final'] = {'kind':'NOTIFY','direction':'SAME_AS_PREVIOUS_DIRECTION'}
        engine = self.engine(value); stamp = 1790000040
        manager = self.send(engine, stamp, {'1m':snapshot(stamp), '5m':snapshot(stamp, above=False)})
        port = manager._special_watch_handlers['TEST_SPECIAL777']
        self.assertEqual([(m.direction, m.meaning['steps'][0]['tfs']) for m in port.machines],
                         [('LONG',['1m']), ('LONG',['5m'])])

    def test_correlated_source_conditions_remain_in_same_tf_branch(self):
        from confirmed_answers import ANSWERS
        value = intent(); value['interpretation'] = copy.deepcopy(ANSWERS[21])
        # The conditions themselves are common branches; old source-template
        # annotations are not part of the current execution contract.
        value['interpretation'].pop('base_special')
        engine = self.engine(value); stamp=1790000040
        manager=self.send(engine,stamp,{'1h':snapshot(stamp)})
        port=manager._special_watch_handlers['TEST_SPECIAL777']
        self.assertEqual(len(port.machines),8)
        self.assertTrue(all(len({s['tfs'][0] for s in m.meaning['steps']})==1 for m in port.machines))
        self.assertFalse(any(not m.meaning['steps'] for m in port.machines))

    def test_touch_source_tf_remains_final_tf_in_independent_branches(self):
        from confirmed_answers import ANSWERS
        value=intent(); value['interpretation']=copy.deepcopy(ANSWERS[22])
        value['interpretation'].pop('base_special')
        engine=self.engine(value); stamp=1790000040
        manager=self.send(engine,stamp,{'1m':snapshot(stamp)})
        port=manager._special_watch_handlers['TEST_SPECIAL777']
        self.assertEqual(len(port.machines),22)
        for m in port.machines:
            self.assertEqual(m.meaning['steps'][0]['tfs'],m.meaning['final']['tfs'])

    def test_session_without_direction_notifies_only_once_at_config_boundary(self):
        from confirmed_answers import ANSWERS
        value=intent(); value['interpretation']=copy.deepcopy(ANSWERS[13])
        engine=self.engine(value)
        # 16:00 KST = 07:00 UTC, on an arbitrary source-time day.
        stamp=1790031600; stamp=stamp//86400*86400+7*3600
        self.send(engine,stamp-1,{'1m':snapshot(stamp-1)})
        self.send(engine,stamp,{'1m':snapshot(stamp)})
        notices=[s for s in engine.signals if s.payload.get('content',{}).get('type')=='NOTIFICATION']
        self.assertEqual(len(notices),1)
        self.assertEqual(notices[0].payload['content']['direction'],'BOTH')

    def test_matching_regime_requires_the_same_family_across_tfs(self):
        from confirmed_answers import ANSWERS
        value=intent(); value['interpretation']=copy.deepcopy(ANSWERS[30])
        engine=self.engine(value); stamp=1790000040
        def slopes(tf,up):
            s=snapshot(stamp); a=s.values.copy()
            for name in ('RSI','STO','DI','price'):
                a[:,PIPE_VALUE_COLUMNS.index(name+'_regime_slope')]=1. if name in up else -1.
            return FeedSnapshot(s.time,s.volume,a,s.seq,s.source_epoch,{})
        manager=self.send(engine,stamp,{'5m':slopes('5m',{'RSI'}),'15m':slopes('15m',{'STO'})})
        port=manager._special_watch_handlers['TEST_SPECIAL777']
        consumer=next(s for s in engine.strategies if s.name=='COMPOSER')
        board=engine.board.view(engine.processor_state,consumer,stamp*1000); symbol='XAUUSD+'; now=stamp
        answer=port.observe(board,symbol,port.machines[0].meaning['steps'][0],'LONG',now)
        self.assertFalse(answer['matched'])
        self.assertEqual(answer['families'],set())
        self.send(engine,stamp+1,{'5m':slopes('5m',{'RSI'}),'15m':slopes('15m',{'RSI'})})
        board=engine.board.view(engine.processor_state,consumer,(stamp+1)*1000); now=stamp+1
        answer=port.observe(board,symbol,port.machines[0].meaning['steps'][0],'LONG',now)
        self.assertTrue(answer['matched'])
        self.assertEqual(answer['families'],{'RSI'})

    def test_checkpoint_keeps_adapter_bound_to_restored_kernel(self):
        value = intent()
        value['interpretation']['final'] = {'kind':'NOTIFY'}
        engine = self.engine(value); stamp = 1790000040
        self.send(engine, stamp, {'1m':snapshot(stamp, above=False)})
        checkpoint = engine.checkpoint()
        engine.restore(checkpoint)
        manager = self.send(engine, stamp+60, {'1m':snapshot(stamp+60)})
        port = manager._special_watch_handlers['TEST_SPECIAL777']
        self.assertIs(port.manager, manager)
        self.assertIs(manager.event_services.kernel.manager, manager)
        self.assertTrue(port.machines[0].active)

    def test_closed_cross_waits_for_new_closed_bar_and_forming_cross_updates_live(self):
        def cross(stamp, *, forming):
            base = snapshot(stamp, above=False); values = base.values.copy()
            values[-1 if forming else -2, PIPE_VALUE_COLUMNS.index('ema_50')] = 101.
            return FeedSnapshot(base.time, base.volume, values, 2, base.source_epoch, {})
        for mode in ('CLOSED','FORMING'):
            value = intent({'kind':'MA_CROSS','tfs':['1m'],'ma_left':'EMA50','ma_right':'EMA200','bar_state':mode})
            value['interpretation']['final'] = {'kind':'NOTIFY'}
            engine = self.engine(value); stamp = 1790000040
            self.send(engine, stamp, {'1m':snapshot(stamp, above=False)})
            self.send(engine, stamp+1, {'1m':cross(stamp, forming=mode=='FORMING')})
            notices = lambda: [s for s in engine.signals if s.payload.get('content',{}).get('type') == 'NOTIFICATION']
            self.assertEqual(len(notices()), int(mode=='FORMING'))
            if mode == 'CLOSED':
                self.send(engine, stamp+60, {'1m':cross(stamp+60, forming=False)})
                self.assertEqual(len(notices()), 1)

    def test_live_pipe_reader_and_replay_publish_have_same_signal_output(self):
        from event_host import load_staff
        from event_engine.staff_adapter import StaffIngressAdapter
        import staff_schema as wire
        import io
        value = intent(); value['interpretation']['final'] = {'kind':'NOTIFY'}
        outputs = []
        try: import zmq
        except ImportError:
            zmq = types.ModuleType('zmq')
            def forbidden(name): raise AssertionError('No ZMQ transport is used by this byte-reader probe: ' + name)
            zmq.__getattr__ = forbidden
        with patch.dict(sys.modules, {'zmq':zmq}):
            staff = load_staff()
        stamp = 1790000040
        with tempfile.TemporaryDirectory() as temp:
            for live in (True, False):
                engine = self.engine(value)
                cache = staff.StaffPipeCache('', health_session='TEST', gap_journal=Path(temp)/'gap.jsonl')
                adapter = StaffIngressAdapter(cache, engine.ingress)
                for i, above in enumerate((False, True, True), 1):
                    snap = snapshot(stamp+i*60, above=above, seq=i)
                    raw = wire.pack_bundle('XAUUSD+', [wire.pack_v2('XAUUSD+','1m',snap.time,snap.volume,snap.values,seq=i)],
                                           seq=i,sent_at_ms=(stamp+i*60)*1000)
                    if live:
                        adapter.receive_one(io.BytesIO(raw).read, allowed_symbols=('XAUUSD+',))
                    else: adapter.publish(raw)
                    engine.run()
                    self.assertFalse(engine.error_log, engine.error_log)
                outputs.append([dict(s.payload['content']) for s in engine.signals
                                if s.payload.get('content',{}).get('type') == 'NOTIFICATION'])
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(len(outputs[0]), 1)


if __name__ == '__main__': unittest.main()
