"""Common language acceptance and same-input current LIVE/Part2 replay probes.

Training numbers appear only in fixtures. No real Ollama, MT5 or Telegram I/O.
"""
import copy
import io
import json
import importlib.util
import os
import socket
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / 'Part1/program'))
sys.path.insert(0, str(ROOT.parent / 'Part2'))
from lab.ai.schema import output_schema, recipe_from_intent, validate_intent
from lab.ai_compiler import execution_plan
from lab import catalog, storage
from lab.compiler import compile_recipe
from lab.intent_runtime import IntentMachine
from test_ai_intent_v2 import intent
from test_ai_execution import module_for, snapshot
from event_engine.model import FeedSnapshot, Kind
from staff_schema import PIPE_VALUE_COLUMNS


def price_step(kind='MA_PRICE_CROSS', family='SMA', period=20, tf='1m', **extra):
    step = dict(kind=kind, tfs=[tf], ma_family=family, slow_period=period, **extra)
    if kind == 'MA_PRICE_CROSS': step.setdefault('relation', 'BOTH')
    return step


def notify(*steps, direction='BOTH', order='SIMULTANEOUS', **extra):
    raw = intent()
    raw['interpretation'] = dict(direction=direction, symbols=['XAUUSD+'], steps=list(steps),
        order_mode=order, final={'kind':'NOTIFY'}, **extra)
    return raw


def acceptance():
    cross = dict(kind='MA_CROSS', tfs=['1m'], ma_left='EMA50', ma_right='EMA200', direction='LONG')
    sequential = notify(cross, price_step('MA_PRICE_TOUCH', 'EMA', 200), direction='LONG', order='SEQUENTIAL')
    daily = notify(price_step(tf='1d'))
    daily['interpretation']['symbols'] = ['NAS100']
    fvg = notify(cross, dict(kind='FVG_NEW', tfs=['5m'], side='BULL', direction='LONG'),
        direction='LONG', order='SEQUENTIAL', final_window_sec=3600, final_after='FVG_NEW',
        cancel_conditions=[dict(kind='FVG_NEW', tfs=['5m'], side='BEAR', direction='SHORT')])
    fvg['interpretation']['final'] = dict(kind='OZ', tfs=['1m','2m','3m'], validation_mode='NORMAL', trigger_mode='OZ')
    return {54:fvg, 59:daily, 60:sequential}


def market(stamp, *, current=100., previous=100., forming=100., touch=True, golden=None, seq=1, epoch='probe', tf='1m'):
    base = snapshot(stamp, seq=seq, epoch=epoch)
    values = base.values.copy()
    for name in PIPE_VALUE_COLUMNS:
        if name.startswith(('sma_', 'ema_', 'wma_', 'hma_')):
            values[:, PIPE_VALUE_COLUMNS.index(name)] = 100.
        if name.endswith(('_lower_out','_upper_out')):
            values[:, PIPE_VALUE_COLUMNS.index(name)] = float('nan')
    def put(name, index, value): values[index, PIPE_VALUE_COLUMNS.index(name)] = value
    for index, price in ((-3,previous),(-2,current),(-1,forming)):
        put('close', index, price)
        put('open', index, 100. if touch else price)
        put('low', index, min(price,100.)-1. if touch else price-0.1)
        put('high', index, max(price,100.)+1. if touch else price+0.1)
    if golden is not None:
        values[:,PIPE_VALUE_COLUMNS.index('ema_50')] = 99.
        if golden: put('ema_50', -2, 101.)
    from command_interpreter import tf_seconds
    period=tf_seconds(tf)
    times=stamp+np.arange(-len(base.time)+1,1,dtype=np.int64)*period
    return FeedSnapshot(times, base.volume, values, seq, epoch, {})


def obs(hit, token, at): return dict(ready=True, matched=hit, event=True, token=token, at=at)


def oz_market(stamp, phase, tf):
    """Finite native indicator inputs for actual OZ OUT→IN/HMA/B0 logic."""
    s=market(stamp); a=s.values.copy()
    def col(name,value): a[:,PIPE_VALUE_COLUMNS.index(name)]=value
    for name,value in {'open':119.,'close':120.,'low':118.6,'high':120.6,
        'hma_6':118. if tf=='1m' else 120.,'hma_17':119.,
        'open_band_4_mid':119.,'wonbi_lower':117.,'wonbi_upper':121.}.items(): col(name,value)
    if tf=='3m':
        for name,value in {'open':121.,'close':121.,'low':120.5,'high':122.}.items(): col(name,value)
    a[-1,PIPE_VALUE_COLUMNS.index('low')]=118.2
    if phase>=1 and tf=='1m': a[-1,PIPE_VALUE_COLUMNS.index('hma_6')]=120.
    if phase>=2 and tf=='1m':
        a[-2,PIPE_VALUE_COLUMNS.index('hma_6')]=120.
        a[-1,PIPE_VALUE_COLUMNS.index('low')]=118.1
    for family in ('RSI','STO','DI','price'):
        for name,value in ((family+'_val' if family!='price' else 'price_hma_6',50.),
            (family+'_db' if family!='price' else 'price_band_lower',40.),
            (family+'_ub' if family!='price' else 'price_band_upper',60.),
            (family+'_regime_lower',40.),(family+'_regime_upper',60.),(family+'_regime_slope',1.)):
            col(name,value)
        if (phase==0 and tf=='1m') or tf=='3m':
            a[-1,PIPE_VALUE_COLUMNS.index(family+'_val' if family!='price' else 'price_hma_6')]=30.
        # Current OZ consumes EA's explicit OUT marker slots; a finite
        # percentile value alone does not mean OUT.
        col(family+'_lower_out',float('nan'))
        col(family+'_upper_out',float('nan'))
        if (phase==0 and tf=='1m') or tf=='3m': a[-1,PIPE_VALUE_COLUMNS.index(family+'_lower_out')]=30.
    return FeedSnapshot(s.time,s.volume,a,s.seq,s.source_epoch,{})


class Contracts(unittest.TestCase):
    def check_output_contract(self, raw):
        from jsonschema import Draft202012Validator
        if not hasattr(self, '_output_validator'):
            schema = output_schema()
            Draft202012Validator.check_schema(schema)
            self._output_validator = Draft202012Validator(schema)
        self._output_validator.validate(raw)
        json.dumps(raw,allow_nan=False)

    def test_acceptance_all_formal_stages_and_explicit_generation(self):
        for number, raw in acceptance().items():
            with self.subTest(case=number):
                raw.update(message_ko='', needs_clarification=False, clarification_question=None)
                self.check_output_contract(raw)
                normalized = validate_intent(raw)
                recipe = recipe_from_intent(normalized)
                self.assertEqual(recipe['schema_version'], 2)
                catalog.validate(recipe)
                plan = execution_plan(recipe['strategy_intent'])
                self.assertEqual(plan['mode'], 'CANONICAL')
                source = compile_recipe(recipe, 'Test_SPECIAL777.py')
                self.assertNotIn('from lab', source)
                self.assertNotIn('from Part3', source)
                module, _ = module_for(raw)
                self.assertTrue(callable(module.register))
                with tempfile.TemporaryDirectory() as folder, patch.object(storage, 'ROOT', Path(folder)):
                    preview = storage.preview(recipe)
                    generated = storage.generate(recipe)
                    self.assertEqual(generated['filename'],preview['filename'])
                    self.assertTrue((Path(folder)/'TEST_SPECIAL'/generated['filename']).is_file())

    def test_families_arbitrary_periods_and_relations_in_schema(self):
        for family in ('SMA','WMA','EMA','HMA'):
            for period in (1,21,37,503,10007):
                for kind in ('MA_PRICE_CROSS','MA_PRICE_TOUCH'):
                    for relation in ('BREAK_UP','BREAK_DOWN','BOTH') if kind.endswith('CROSS') else (None,):
                        raw = notify(price_step(kind, family, period, **({'relation':relation} if relation else {})))
                        raw.update(message_ko='',needs_clarification=False,clarification_question=None)
                        self.check_output_contract(raw)
                        recipe = recipe_from_intent(raw)
                        self.assertEqual(recipe['strategy_intent']['steps'][0]['slow_period'],period)

    def test_reject_invalid_periods_relations_and_unrelated_fields(self):
        for kind in ('MA_PRICE_CROSS','MA_PRICE_TOUCH'):
            for period in (0,-1,True,1.5,'20',None):
                with self.subTest(kind=kind,period=period), self.assertRaises(ValueError):
                    recipe_from_intent(notify(price_step(kind,period=period)))
        for field, value in (('ma_family','SMMA'), ('relation','TOUCH'), ('ma_left','EMA50'), ('side','ABOVE')):
            raw = notify(price_step()); raw['interpretation']['steps'][0][field] = value
            with self.assertRaises(ValueError): recipe_from_intent(raw)
        with self.assertRaises(ValueError): recipe_from_intent(notify(price_step('MA_PRICE_TOUCH',relation='TOUCH')))
        with self.assertRaises(ValueError): recipe_from_intent(notify(dict(kind='PRICE_LEVEL',tfs=['1m'],level=100,relation='BOTH')))

    def test_unlimited_bounded_branches_and_unordered_contract(self):
        for gap in ('omitted',None,600):
            extra = {} if gap == 'omitted' else {'within_sec':gap}
            raw = notify(price_step(), price_step('MA_PRICE_TOUCH'), order='SEQUENTIAL', **extra)
            self.assertEqual(recipe_from_intent(raw)['strategy_intent'].get('within_sec'),extra.get('within_sec'))
            branch = intent(); branch['interpretation'].update(steps=[],branches=[raw['interpretation'] | {'symbols': ['XAUUSD+']}])
            branch['interpretation']['branches'][0].pop('symbols')
            branch['interpretation']['order_mode'] = 'SEQUENTIAL'
            branch['interpretation'].pop('within_sec')
            recipe_from_intent(branch)
        unordered = recipe_from_intent(notify(price_step(),price_step(),order='UNORDERED'))
        self.assertEqual(unordered['strategy_intent']['order_mode'], 'UNORDERED')
        self.assertIsNone(unordered['strategy_intent'].get('within_sec'))
        for bad in (0,-1,False):
            with self.assertRaises(ValueError): recipe_from_intent(notify(price_step(),price_step(),order='SEQUENTIAL',within_sec=bad))

    def test_future_multi_stage_generic_strategy(self):
        raw = notify(price_step(family='WMA',period=43,tf='15m',relation='BREAK_DOWN'),
            price_step('MA_PRICE_TOUCH','HMA',71,'3m'), price_step(family='EMA',period=21,tf='5m',relation='BREAK_UP'),
            direction='SHORT',order='SEQUENTIAL')
        recipe = recipe_from_intent(raw)
        self.assertEqual(len(execution_plan(recipe['strategy_intent'])['meaning']['steps']),3)
        module_for(raw)

    def test_agent_confirmation_then_recipe_generation_for_acceptance(self):
        from lab.ai.agent import Agent
        from lab.ai.provider import ScriptedProvider
        for raw in acceptance().values():
            agent=Agent(ScriptedProvider([{'content':json.dumps(raw),'tool_calls':[]}]))
            with tempfile.TemporaryDirectory() as folder, patch.object(storage,'ROOT',Path(folder)):
                response=agent.send('공통 전략 언어 경로 검증')
                self.assertTrue(response['can_apply'],response)
                self.assertFalse((Path(folder)/'generated').exists())
                recipe=agent.apply()
                catalog.validate(recipe)
                generated=storage.generate(recipe)
                module=types.ModuleType('probe_generated')
                exec(compile(generated['code'],generated['filename'],'exec'),module.__dict__)
                self.assertTrue(callable(module.register))


class Sequence(unittest.TestCase):
    def machine(self, gap=None, count=2, final=None, **extra):
        raw = notify(*[price_step() for _ in range(count)],order='SEQUENTIAL',**extra)
        if gap is not None: raw['interpretation']['within_sec'] = gap
        if final: raw['interpretation']['final'] = final
        return IntentMachine(recipe_from_intent(raw)['strategy_intent'],'XAUUSD+','LONG')

    def test_unlimited_survives_long_wait_and_checkpoint(self):
        m = self.machine()
        m.advance(1,[obs(True,'a',1),obs(False,'b',0)])
        restored = self.machine(); restored.restore(m.checkpoint())
        self.assertEqual(restored.advance(1000001,[obs(False,'a',1),obs(True,'b2',1000001)]),['NOTIFY'])

    def test_followup_must_be_after_previous_event_not_first_event(self):
        m=self.machine(count=3)
        self.assertEqual(m.advance(1,[obs(True,'a',1),obs(True,'b',1),obs(False,'c',0)]),[])
        self.assertEqual(m.stage,1)
        m.advance(10,[obs(False,'a',1),obs(True,'b2',10),obs(False,'c',0)])
        self.assertEqual(m.stage,2)
        self.assertEqual(m.advance(11,[obs(False,'a',1),obs(False,'b',10),obs(True,'old-c',5)]),[])
        self.assertEqual(m.advance(12,[obs(False,'a',1),obs(False,'b',10),obs(True,'c2',12)]),['NOTIFY'])

    def test_order_uses_source_event_time_even_when_delivery_is_later(self):
        m=self.machine()
        m.advance(0,[obs(False,'a',0),obs(False,'b',0)])
        self.assertEqual(m.advance(10,[obs(True,'a',1),obs(True,'b',5)]),['NOTIFY'])

    def test_bounded_sequence_inclusive_deadline_and_late_rejection(self):
        for at, expected in ((601,['NOTIFY']),(602,[])):
            m=self.machine(gap=600)
            m.advance(1,[obs(True,'a',1),obs(False,'b',0)])
            self.assertEqual(m.advance(at,[obs(False,'a',1),obs(True,'b',at)]),expected)

    def test_training_fvg_window_begins_at_followup_and_cancel_resets_candidate(self):
        meaning=recipe_from_intent(acceptance()[54])['strategy_intent']
        m=IntentMachine(meaning,'XAUUSD+','LONG')
        m.advance(1,[obs(True,'cross',1),obs(False,'old-fvg',0)])
        self.assertEqual(m.advance(100000,[obs(False,'cross',1),obs(True,'new-fvg',100000)]),['ARM'])
        self.assertTrue(m.final_allowed(103600,[obs(False,'cross',1),obs(False,'new-fvg',100000)]))
        self.assertFalse(m.final_allowed(103601,[obs(False,'cross',1),obs(False,'new-fvg',100000)]))
        self.assertEqual(m.advance(100001,[obs(False,'cross',1),obs(False,'new-fvg',100000)],cancelled=True),['CANCEL'])
        self.assertEqual(m.stage,0)


class GeneratedExecution(unittest.TestCase):
    def setUp(self):
        self.block=patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden'))
        self.block.start()
    def tearDown(self): self.block.stop()
    def engine(self, raw, live=False):
        from event_application import create_event_engine
        module,_=module_for(raw)
        return create_event_engine({'WONBI_SIGMA':'3','TELEGRAM_TOKEN':'','TELEGRAM_CHAT_ID':'TEST',
            'STAFF_ALLOWED_SYMBOLS':'XAUUSD+,NAS100','TARGET_SYMBOLS':'XAUUSD+,NAS100','LONDON':'1600-0100'},
            plugins={'Test_SPECIAL777':module},enabled_specials=('Test_SPECIAL777',),
            symbols=tuple(raw['interpretation']['symbols']),backtest=not live,oz_evaluation='all')
    def send(self, engine, stamp, feeds, symbol='XAUUSD+'):
        engine.ingress.post(Kind.MARKET_BUNDLE,source='logic',source_seq=stamp,source_time=stamp*1000,
            payload={'symbol':symbol,'feeds':feeds})
        engine.run()
        self.assertFalse(engine.error_log,engine.error_log)
        return engine.strategy_state['COMPOSER']['kernels'][symbol].manager._special_watch_handlers['TEST_SPECIAL777']
    def notices(self, engine):
        return [dict(s.payload['content']) for s in engine.signals if s.payload.get('content',{}).get('type')=='NOTIFICATION']

    def test_current_part3_backtest_loader_loads_actual_generated_files(self):
        import event_application
        from event_selection import SPECIAL_DEPENDENCIES
        spec=importlib.util.spec_from_file_location('_backtest_common_probe',ROOT/'backtest.py')
        entry=importlib.util.module_from_spec(spec); spec.loader.exec_module(entry)
        original=getattr(event_application,'_part3_original_loader',event_application.load_strategy_inputs)
        for number,raw in acceptance().items():
            with self.subTest(case=number), tempfile.TemporaryDirectory() as folder, \
                patch.object(storage,'ROOT',Path(folder)), patch.dict(os.environ), \
                patch.dict(SPECIAL_DEPENDENCIES), \
                patch.object(event_application,'load_strategy_inputs',original), \
                patch.object(event_application,'_part3_original_loader',original,create=True):
                saved=storage.generate(recipe_from_intent(raw))
                key=entry.initialize({'project_root':str(ROOT.parent),'strategy':saved['path'],'job_dir':folder})
                symbol=raw['interpretation']['symbols'][0]
                e=event_application.create_event_engine({'WONBI_SIGMA':'3','TELEGRAM_TOKEN':'','TELEGRAM_CHAT_ID':'TEST',
                    'STAFF_ALLOWED_SYMBOLS':symbol,'TARGET_SYMBOLS':symbol,'LONDON':'1600-0100'},
                    symbols=(symbol,),selection=[key],backtest=True,oz_evaluation='all')
                t=1790000040
                for i in range(3):
                    stamp=t+i*(86400 if number==59 else 60)
                    if number==59:
                        feeds={'1d':market(stamp,current=98. if i==0 else 102.,previous=98. if i<2 else 102.,tf='1d')}
                    else:
                        feeds={'1m':market(stamp,golden=i==1)}
                        if number==54:
                            s=market(stamp); a=s.values.copy()
                            if i==2:
                                for name,value in (('open',105.),('close',105.),('low',104.),('high',106.)):
                                    a[-2,PIPE_VALUE_COLUMNS.index(name)]=value
                            feeds['5m']=FeedSnapshot(s.time,s.volume,a,1,s.source_epoch,{})
                    e.ingress.post(Kind.MARKET_BUNDLE,source='probe',source_seq=i+1,source_time=stamp*1000,
                        payload={'symbol':symbol,'feeds':feeds})
                    e.run(); self.assertFalse(e.error_log,e.error_log)
                manager=e.strategy_state['COMPOSER']['kernels'][symbol].manager
                port=manager._special_watch_handlers[key.upper()]
                if number==54: self.assertTrue(port.machines[0].active)
                else: self.assertEqual(len(self.notices(e)),1)

    def test_all_families_literal_variable_period_cross_without_copied_calculation(self):
        from watch_array_facts import WatchMAStore
        for family in ('SMA','WMA','EMA','HMA'):
            for period in (21,37):
                with self.subTest(family=family,period=period):
                    e=self.engine(notify(price_step(family=family,period=period)))
                    t=1790000040
                    with patch.object(WatchMAStore,'get',autospec=True,side_effect=WatchMAStore.get) as store:
                        self.send(e,t,{'1m':market(t,current=98.,previous=98.)})
                        self.send(e,t+60,{'1m':market(t+60,current=102.,previous=98.)})
                        self.assertEqual(len(self.notices(e)),1)
                        # Every period, EMA21 included, goes through the shared Fact store.
                        self.assertTrue(store.called)
                        self.assertEqual(self.notices(e)[0]['direction'],'LONG')

    def test_period_one_uses_existing_source_contract_and_touch(self):
        for family in ('SMA','WMA','EMA','HMA'):
            t=1790000040
            e=self.engine(notify(price_step(family=family,period=1)))
            self.send(e,t,{'1m':market(t,current=98,previous=98)})
            self.send(e,t+60,{'1m':market(t+60,current=102,previous=98)})
            # EMA1 equals CLOSE; the other three use OPEN in this layer.
            self.assertEqual(len(self.notices(e)),0 if family=='EMA' else 1)
            e=self.engine(notify(price_step('MA_PRICE_TOUCH',family,1)))
            self.send(e,t,{'1m':market(t)})
            self.send(e,t+60,{'1m':market(t+60)})
            self.assertEqual(len(self.notices(e)),1)

    def test_cross_directions_no_duplicate_both_and_equality_boundary(self):
        for relation,expected in (('BREAK_UP',['LONG']),('BREAK_DOWN',['SHORT']),('BOTH',['LONG','SHORT'])):
            e=self.engine(notify(price_step(family='EMA',period=200,relation=relation)))
            t=1790000040
            for i,(previous,current) in enumerate(((100,100),(100,102),(102,100),(100,98))):
                self.send(e,t+i*60,{'1m':market(t+i*60,previous=previous,current=current)})
            self.assertEqual([n['direction'] for n in self.notices(e)],expected)

    def test_bidirectional_cross_can_notify_fixed_action_direction(self):
        e=self.engine(notify(price_step(family='EMA',period=200),direction='LONG'))
        t=1790000040
        for i,(previous,current) in enumerate(((100,100),(100,102),(100,98))):
            self.send(e,t+i*60,{'1m':market(t+i*60,previous=previous,current=current)})
        self.assertEqual([n['direction'] for n in self.notices(e)],['LONG','LONG'])

    def test_closed_cross_ignores_forming_then_new_closed_bar_only(self):
        for mode in ('UNSPECIFIED','CLOSED'):
            e=self.engine(notify(price_step(family='EMA',period=200,bar_state=mode)))
            t=1790000040
            self.send(e,t,{'1m':market(t,current=98,previous=98)})
            self.send(e,t+1,{'1m':market(t,current=98,previous=98,forming=102,seq=2)})
            self.assertEqual(self.notices(e),[])
            self.send(e,t+60,{'1m':market(t+60,current=102,previous=98)})
            self.send(e,t+61,{'1m':market(t+60,current=102,previous=98,seq=2)})
            self.assertEqual(len(self.notices(e)),1)

    def test_forming_cross_new_edge_and_unknown_values_do_not_fire(self):
        e=self.engine(notify(price_step(family='EMA',period=200,bar_state='FORMING')))
        t=1790000040
        self.send(e,t,{'1m':market(t,current=98,forming=98)})
        self.send(e,t+1,{'1m':market(t,current=98,forming=102,seq=2)})
        self.send(e,t+2,{'1m':market(t,current=98,forming=103,seq=3)})
        self.assertEqual(len(self.notices(e)),1)
        e=self.engine(notify(price_step(family='EMA',period=10007)))
        self.send(e,t,{'1m':market(t,current=98)})
        self.send(e,t+60,{'1m':market(t+60,current=102,previous=98)})
        self.assertEqual(self.notices(e),[])

    def test_touch_all_families_and_boundary_and_miss(self):
        for family in ('SMA','WMA','EMA','HMA'):
            e=self.engine(notify(price_step('MA_PRICE_TOUCH',family,37)))
            t=1790000040
            self.send(e,t,{'1m':market(t)})
            self.assertEqual(self.notices(e),[])
            self.send(e,t+60,{'1m':market(t+60)})
            self.assertEqual(len(self.notices(e)),1)
        e=self.engine(notify(price_step('MA_PRICE_TOUCH','EMA',200)))
        t=1790000040
        self.send(e,t,{'1m':market(t,current=102,previous=102,forming=102,touch=False)})
        self.send(e,t+60,{'1m':market(t+60,current=102,previous=102,forming=102,touch=False)})
        self.assertEqual(self.notices(e),[])
        s=market(t+120); a=s.values.copy()
        a[-2,PIPE_VALUE_COLUMNS.index('low')]=100.
        a[-2,PIPE_VALUE_COLUMNS.index('high')]=102.
        self.send(e,t+120,{'1m':FeedSnapshot(s.time,s.volume,a,1,s.source_epoch,{})})
        self.assertEqual(len(self.notices(e)),1)

    def test_forming_touch_initial_history_duplicate_and_new_candle(self):
        e=self.engine(notify(price_step('MA_PRICE_TOUCH','EMA',200,bar_state='FORMING')))
        t=1790000040
        self.send(e,t,{'1m':market(t)})
        self.assertEqual(self.notices(e),[])
        self.send(e,t+60,{'1m':market(t+60)})
        self.send(e,t+61,{'1m':market(t+60,seq=2)})
        self.assertEqual(len(self.notices(e)),1)

    def test_followup_touch_cannot_use_cross_candle_or_initial_touch(self):
        raw=acceptance()[60]; e=self.engine(raw); t=1790000040
        self.send(e,t,{'1m':market(t,current=102,previous=102,forming=102,touch=False,golden=False)})
        port=self.send(e,t+60,{'1m':market(t+60,golden=True)})
        self.assertEqual(port.machines[0].stage,1)
        self.assertEqual(self.notices(e),[])
        self.send(e,t+61,{'1m':market(t+60,golden=True,seq=2)})
        self.assertEqual(self.notices(e),[])
        self.send(e,t+120,{'1m':market(t+120,golden=False)})
        self.assertEqual(len(self.notices(e)),1)
        self.assertEqual(self.notices(e)[0]['direction'],'LONG')

    def test_fvg_sequence_real_processor_and_oz_window(self):
        raw=acceptance()[54]; e=self.engine(raw); t=1790000040
        # Form a bullish gap only after the MA event, via the actual FVG processor.
        def fvg(stamp,new=False):
            s=market(stamp); a=s.values.copy()
            if new:
                for name,val in (('open',105.),('close',105.),('low',104.),('high',106.)):
                    a[-2,PIPE_VALUE_COLUMNS.index(name)]=val
            return FeedSnapshot(s.time,s.volume,a,1,s.source_epoch,{})
        self.send(e,t,{'1m':market(t,golden=False),'5m':fvg(t)})
        port=self.send(e,t+60,{'1m':market(t+60,golden=True),'5m':fvg(t+60)})
        self.assertEqual(port.machines[0].stage,1)
        later=t+100000
        port=self.send(e,later,{'1m':market(later,golden=False),'5m':fvg(later,True)})
        self.assertTrue(port.machines[0].active)
        self.assertEqual(port.machines[0].active_until,later+3600)
        bear=fvg(later+60); a=bear.values.copy()
        for name,value in (('open',94.),('close',94.),('low',93.),('high',95.)):
            a[-2,PIPE_VALUE_COLUMNS.index(name)]=value
        port=self.send(e,later+60,{'1m':market(later+60,golden=False),
            '5m':FeedSnapshot(bear.time,bear.volume,a,1,bear.source_epoch,{})})
        self.assertFalse(port.machines[0].active)
        self.assertEqual(port.machines[0].stage,0)

    def test_final_oz_actual_processor_after_common_sequence(self):
        e=self.engine(acceptance()[54]); t=1790000040
        self.send(e,t,{'1m':market(t,golden=False),'5m':market(t)})
        self.send(e,t+60,{'1m':market(t+60,golden=True),'5m':market(t+60)})
        s=market(t+120); a=s.values.copy()
        for name,value in (('open',105.),('close',105.),('low',104.),('high',106.)):
            a[-2,PIPE_VALUE_COLUMNS.index(name)]=value
        self.send(e,t+120,{'1m':market(t+120),'5m':FeedSnapshot(s.time,s.volume,a,1,s.source_epoch,{})})
        for i,phase in enumerate((0,1,2,3)):
            stamp=t+180+(i-1)*60 if i>1 else t+180+i
            self.send(e,stamp,{tf:oz_market(t+180 if i<2 else stamp,phase,tf) for tf in catalog.TF_LABELS})
        alerts=[n for n in self.notices(e) if n.get('source_spec_id','').startswith('Test_SPECIAL777:BRANCH:')]
        self.assertEqual(len(alerts),1,self.notices(e))

    def test_same_wire_input_actual_live_reader_and_part2_capture_replay(self):
        from event_host import load_staff
        from event_engine.staff_adapter import StaffIngressAdapter
        from event_engine.capture_io import FILE_HEADER, RECORD_HEADER
        from event_backtest.bridge import CaptureInputs
        import staff_schema as wire
        try: import zmq
        except ImportError:
            zmq=types.ModuleType('zmq')
            def forbidden(name): raise AssertionError('No ZMQ transport in this probe: '+name)
            zmq.__getattr__=forbidden
        with patch.dict(sys.modules,{'zmq':zmq}): staff=load_staff()
        t=1790000040
        self.parity_results=[]
        for number, raw in acceptance().items():
            with self.subTest(case=number), tempfile.TemporaryDirectory() as folder:
                symbol=raw['interpretation']['symbols'][0]
                tf='1d' if number==59 else '1m'
                samples=[]
                for i in range(4):
                    stamp=t+i*(86400 if number==59 else 60)
                    if number==59: s=market(stamp,current=(98,102,102,98)[i],previous=(98,98,102,102)[i],tf='1d')
                    else: s=market(stamp,golden=i==1)
                    feeds={tf:s}
                    if number==54:
                        five=market(stamp); a=five.values.copy()
                        if i>=2:
                            for name,val in (('open',105.),('close',105.),('low',104.),('high',106.)):
                                a[-2,PIPE_VALUE_COLUMNS.index(name)]=val
                        feeds['5m']=FeedSnapshot(five.time,five.volume,a,1,five.source_epoch,{})
                    samples.append((stamp,feeds))
                if number==54:
                    for i,phase in enumerate((0,1,2,3)):
                        stamp=t+240+(i-1)*60 if i>1 else t+240+i
                        samples.append((stamp,{frame:oz_market(t+240 if i<2 else stamp,phase,frame) for frame in catalog.TF_LABELS}))
                    # Every capture feed begins with FULL, including OZ's
                    # higher-TF dependencies. They carry quiet inputs initially.
                    for stamp,feeds in samples[:4]:
                        for frame in catalog.TF_LABELS: feeds.setdefault(frame,market(stamp))
                    stamp=t+480
                    feeds={frame:oz_market(stamp,3,frame) for frame in catalog.TF_LABELS}
                    five=market(stamp); a=five.values.copy()
                    for name,value in (('open',94.),('close',94.),('low',93.),('high',95.)):
                        a[-2,PIPE_VALUE_COLUMNS.index(name)]=value
                    feeds['5m']=FeedSnapshot(five.time,five.volume,a,1,five.source_epoch,{})
                    samples.append((stamp,feeds))
                root=Path(folder); capture=root/'capture'; capture.mkdir()
                manifest=['key\tvalue','symbol\t'+symbol,'pipe_capture\tSTAFF_PIPE_V2','pipe_observation_unit\tmilliseconds']
                for n, frame in enumerate(samples[0][1]):
                    file='feed_'+frame+'.bin'; manifest.append(f'pipe_feed\t{n}\t{frame}\t{file}\t{len(samples)}')
                    with (capture/file).open('wb') as f:
                        f.write(FILE_HEADER.pack(0x4D535033,2,len(PIPE_VALUE_COLUMNS),250))
                        for i,(stamp,feeds) in enumerate(samples,1):
                            s=feeds[frame]; child=wire.pack_v2(symbol,frame,s.time,s.volume,s.values,seq=i)
                            f.write(RECORD_HEADER.pack(stamp*1000,0,len(child))); f.write(child)
                (capture/'manifest.tsv').write_text('\n'.join(manifest),'ascii'); (capture/'complete.txt').write_text('VERIFIED\n','ascii')
                outputs=[]; states=[]
                for live in (True,False):
                    e=self.engine(raw,live=live)
                    clock=[0.]
                    cache=staff.StaffPipeCache('',health_session='PROBE',monotonic=lambda:clock[0],gap_journal=root/'gaps.jsonl')
                    if live:
                        adapter=StaffIngressAdapter(cache,e.ingress)
                        for i,(stamp,feeds) in enumerate(samples,1):
                            children=[wire.pack_v2(symbol,frame,s.time,s.volume,s.values,seq=i) for frame,s in feeds.items()]
                            packet=wire.pack_bundle(symbol,children,seq=i,sent_at_ms=stamp*1000)
                            clock[0]+=0.001; adapter.receive_one(io.BytesIO(packet).read,allowed_symbols=(symbol,))
                            e.run()
                    else:
                        inputs=CaptureInputs(staff,cache,[capture],clock=clock,capture_start='beginning')
                        for item in inputs:
                            e.ingress.post(item.kind,source=item.source,source_seq=item.source_seq,source_time=item.source_time,payload=item.payload)
                            e.run()
                    self.assertFalse(e.error_log,e.error_log)
                    outputs.append(self.notices(e))
                    port=e.strategy_state['COMPOSER']['kernels'][symbol].manager._special_watch_handlers['TEST_SPECIAL777']
                    states.append([(m.stage,m.active,m.active_until) for m in port.machines])
                self.assertEqual(outputs[0],outputs[1])
                self.assertEqual(states[0],states[1])
                if number==54:
                    self.assertFalse(states[0][0][1])
                    self.assertEqual(states[0][0][0],0)
                    alerts=[n for n in outputs[0] if n.get('source_spec_id','').startswith('Test_SPECIAL777:BRANCH:')]
                    self.assertEqual(len(alerts),1,outputs[0])
                else: self.assertEqual(len(outputs[0]),2 if number==59 else 1)
                self.parity_results.append({'case':number,'same_notifications':outputs[0]==outputs[1],
                    'same_lifecycle':states[0]==states[1],'live':outputs[0],'part2_replay':outputs[1],
                    'lifecycle':states[0]})


if __name__=='__main__': unittest.main()
