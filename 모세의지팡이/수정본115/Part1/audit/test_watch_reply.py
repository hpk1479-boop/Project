"""Reply registration/alert/cancel contracts through real parsers and OZ controllers.
Only Telegram/MT5/transport I/O is replaced by audit.harness.World.
"""
import json
import logging
import threading
import types
import unittest
from unittest.mock import patch

from harness import World


class WatchReply(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.w = World()
        self.addCleanup(self.w.close)
        self.w.isolate_specs()
        self.seq = 100

    def register(self, text='BTCUSD 5분 이하 매수올존 감시해', owner='123', mid=None):
        self.seq += 1
        mid = self.seq if mid is None else mid
        self.w.clock.advance(.01)
        before = set(self.w.manager._watch_message_links)
        self.w.manager.handle_command(text, owner, message_id=mid)
        self.w.commands()
        added = set(self.w.manager._watch_message_links) - before
        self.assertEqual(len(added), 1, (text, self.w.http.deliveries))
        link = self.w.manager._watch_message_links[added.pop()]
        self.assertIsNotNone(link['confirmation_message_id'])
        return link

    def cancel(self, link, owner=None, reply=None):
        self.w.manager.handle_command('취소', owner or link['owner_chat_id'], message_id=900,
                                      reply_to_message_id=reply or link['confirmation_message_id'])
        self.w.commands()

    def reply_id(self, delivery=None):
        delivery = delivery or self.w.http.deliveries[-1]
        return json.loads(delivery['reply_parameters'])['message_id']

    def test_exact_user_example_registration_and_alert(self):
        w = self.w
        w.manager.config['TARGET_SYMBOLS'] = 'BTCUSD,NAS100'
        text = '나스닥 5분 이하 매수올존 감시해'
        link = self.register(text, mid=456)
        self.assertEqual(link['original_command'], text)
        self.assertEqual(link['watch_type'], 'OZ')
        self.assertEqual(w.watch._watches[link['watch_id']].timeframes, ('1m','2m','3m','4m','5m'))
        self.assertEqual(w.http.deliveries[-1]['text'], '✅ NAS100 · 1분·2분·3분·4분·5분 올존 LONG 감시')
        self.assertTrue(w.watch.try_fire('NAS100','3m','LONG','C',trigger_name='WONBI',alert_identity='a'))
        self.assertEqual(w.http.deliveries[-1]['text'], '🔔 3분 올존 C급 LONG · NAS100 · WONBI')
        self.assertEqual(self.reply_id(), 456)

    def test_raw_command_is_saved_before_alias_normalization(self):
        text = '  BTCUSD 5분 이하 매수올존 감시해  '
        link = self.register(text)
        self.assertEqual(link['original_command'], text)
        stored = json.loads(self.w.manager._state_path.read_text(encoding='utf-8'))
        self.assertEqual(stored['watch_message_links'][0]['original_command'], text)

    def test_cancel_entire_timeframe_bundle_and_preserve_other_watches(self):
        first = self.register('BTCUSD 5분 이하 매수올존 계속 감시해')
        second = self.register('BTCUSD 10분 매수올존 계속 감시해')
        third = self.register('BTCUSD 5분 이하 매수올존 계속 감시해', owner='456')
        with patch.object(self.w.watch, 'cancel_manual', wraps=self.w.watch.cancel_manual) as cancel:
            self.cancel(first)
        self.assertNotIn(first['watch_id'], self.w.watch._watches)
        self.assertIn(second['watch_id'], self.w.watch._watches)
        self.assertIn(third['watch_id'], self.w.watch._watches)
        self.assertEqual(cancel.call_count, 1)
        self.assertEqual(cancel.call_args.kwargs['watch_id'], first['watch_id'])
        self.assertEqual(cancel.call_args.args[0], [])
        self.assertEqual(self.w.http.deliveries[-1]['text'], '🗑 BTCUSD · 1분·2분·3분·4분·5분 올존 LONG 지속 감시 취소')

    def test_plain_cancel_does_not_delete_anything(self):
        link = self.register()
        self.w.manager.handle_command('취소','123',message_id=500)
        self.w.commands()
        self.assertIn(link['watch_id'], self.w.watch._watches)
        self.assertIn('답장', self.w.http.deliveries[-1]['text'])

    def test_wrong_original_and_alert_reply_targets_cannot_cancel(self):
        link = self.register('BTCUSD 1분 매수올존 계속 감시해',mid=9001)
        self.w.watch.try_fire('BTCUSD','1m','LONG','A',alert_identity='persistent')
        alert_mid = len(self.w.http.deliveries)
        for target in (9001, alert_mid, 99999):
            self.cancel(link, reply=target)
            self.assertIn(link['watch_id'], self.w.watch._watches)
            self.assertIn('답장',self.w.http.deliveries[-1]['text'])

    def test_another_chat_cannot_cancel_by_message_id(self):
        link = self.register()
        self.cancel(link, owner='456')
        self.assertIn(link['watch_id'],self.w.watch._watches)
        self.assertEqual(self.w.http.deliveries[-1]['chat_id'], '456')
        self.assertIn('답장',self.w.http.deliveries[-1]['text'])

    def test_message_ids_are_scoped_to_each_chat(self):
        first = self.register(owner='123')
        second = self.register(owner='456')
        second['confirmation_message_id'] = first['confirmation_message_id']
        self.w.manager._save_private_state_locked()
        self.cancel(second)
        self.assertIn(first['watch_id'],self.w.watch._watches)
        self.assertNotIn(second['watch_id'],self.w.watch._watches)

    def test_repeated_cancel_does_not_delete_another_watch(self):
        first = self.register('BTCUSD 1분 매수올존 계속 감시해')
        second = self.register('BTCUSD 2분 매수올존 계속 감시해')
        self.cancel(first)
        self.cancel(first)
        self.assertIn(second['watch_id'],self.w.watch._watches)
        self.assertIn('이미',self.w.http.deliveries[-1]['text'])

    def test_stale_replaced_one_shot_does_not_delete_current_watch(self):
        first = self.register()
        second = self.register('BTCUSD 10분 매수올존 감시해')
        self.assertNotIn(first['watch_id'],self.w.watch._watches)
        self.cancel(first)
        self.assertIn(second['watch_id'],self.w.watch._watches)
        self.assertIn('종료',self.w.http.deliveries[-1]['text'])

    def test_completed_one_shot_cancellation_reports_no_live_watch(self):
        link = self.register()
        self.w.watch.try_fire('BTCUSD','1m','LONG','A',alert_identity='done')
        self.cancel(link)
        self.assertIn('종료',self.w.http.deliveries[-1]['text'])
        self.assertEqual(link['status'],'inactive')

    def test_generic_ma_registration_alert_and_existing_cancel_path(self):
        link = self.register('BTCUSD 1분 기울기(SMA17) > 0 알려줘',mid=202)
        self.assertEqual(link['cancel_action'],'CANCEL_GENERIC')
        self.assertTrue(self.w.generic.fire(link['watch_id'],'existing alert text',event_id='generic-test'))
        self.assertEqual(self.w.http.deliveries[-1]['text'],'existing alert text')
        self.assertEqual(self.reply_id(),202)
        # canonical MA defaults to one-shot; stale cancellation still uses only its ID.
        with patch.object(self.w.generic,'cancel',wraps=self.w.generic.cancel) as cancel:
            self.cancel(link)
        self.assertEqual(cancel.call_count,1)
        self.assertEqual(cancel.call_args.args[0]['watch_id'],link['watch_id'])

    def test_generic_cancel_preserves_sibling(self):
        first = self.register('BTCUSD 1분 기울기(SMA17) > 0 알려줘')
        second = self.register('BTCUSD 1분 SMA20 > EMA50 알려줘')
        self.cancel(first)
        self.assertNotIn(first['watch_id'],self.w.generic._watches)
        self.assertIn(second['watch_id'],self.w.generic._watches)
        self.assertTrue(self.w.http.deliveries[-1]['text'].startswith('🗑'))

    def test_restart_restores_manual_reply_and_precise_cancellation(self):
        link = self.register('BTCUSD 5분 이하 매수올존 계속 감시해',mid=301)
        self.w.restart_manager()
        self.w.restart_oz()
        self.w.sender.verify_connection()  # actual OZ startup cleanup must keep this root
        self.w.commands()
        self.assertIn(link['watch_id'],self.w.watch._watches)
        self.assertTrue(self.w.watch.try_fire('BTCUSD','3m','LONG','C',alert_identity='restart'))
        self.assertEqual(self.reply_id(),301)
        self.cancel(link)
        self.assertNotIn(link['watch_id'],self.w.watch._watches)

    def test_restart_restores_generic_reply_and_cancellation(self):
        link = self.register('BTCUSD 1분 기울기(SMA17) > 0 알려줘',mid=302)
        self.w.restart_manager();self.w.restart_oz()
        self.w.sender.verify_connection();self.w.commands()
        self.assertIn(link['watch_id'],self.w.generic._watches)
        self.cancel(link)
        self.assertNotIn(link['watch_id'],self.w.generic._watches)

    def test_same_event_replies_to_each_original_once(self):
        a = self.register('BTCUSD 1분 매수올존 계속 감시해',mid=401)
        b = self.register('BTCUSD 5분 이하 매수올존 계속 감시해',mid=402)
        before = len(self.w.http.deliveries)
        self.assertTrue(self.w.watch.try_fire('BTCUSD','1m','LONG','A',alert_identity='shared'))
        alerts = self.w.http.deliveries[before:]
        self.assertEqual(len(alerts),2)
        self.assertEqual({self.reply_id(x) for x in alerts},{401,402})
        self.assertTrue(self.w.watch.try_fire('BTCUSD','1m','LONG','A',alert_identity='shared'))
        self.assertEqual(len(self.w.http.deliveries),before+2)

    def test_duplicate_registration_ack_retains_first_confirmation(self):
        link = self.register()
        before = len(self.w.http.deliveries)
        event = dict(kind='CONTROL_ACK',strategy='OZ',watch_event='REGISTERED',
                     watch_id=link['watch_id'],request_chat_id='123',message=link['confirmation_text'])
        self.assertTrue(self.w.manager._handle_event(event)['delivered'])
        self.assertEqual(len(self.w.http.deliveries),before)
        self.assertEqual(link['confirmation_message_id'],before)

    def test_restart_recovers_confirmation_binding_from_delivery_receipt(self):
        link = self.register()
        mid = link['confirmation_message_id']
        link['confirmation_message_id'] = None
        self.w.manager._save_private_state_locked()
        self.w.restart_manager()
        before = len(self.w.http.deliveries)
        self.w.manager._retry_watch_confirmations()
        restored = self.w.manager._watch_message_links[link['watch_id']]
        self.assertEqual(restored['confirmation_message_id'],mid)
        self.assertEqual(len(self.w.http.deliveries),before)

    def test_reply_failure_has_no_standalone_fallback_and_retains_watch(self):
        link = self.register()
        requests = self.w.kim.requests
        before = len(self.w.http.deliveries)
        original = requests.post
        def fail_reply(url, **kwargs):
            self.assertIn('reply_parameters',kwargs['data'])
            response = original(url,**kwargs)
            return types.SimpleNamespace(status_code=400,text='replied message not found',
                                         json=lambda:{'ok':False,'description':'message not found'})
        with patch.object(requests,'post',side_effect=fail_reply):
            self.assertFalse(self.w.watch.try_fire('BTCUSD','1m','LONG','A',alert_identity='missing-original'))
        self.assertEqual(len(self.w.http.deliveries),before+1)
        self.assertIn(link['watch_id'],self.w.watch._watches)
        params=json.loads(self.w.http.deliveries[-1]['reply_parameters'])
        self.assertIs(params['allow_sending_without_reply'],False)

    def test_api_ok_false_is_not_a_successful_delivery(self):
        with patch.object(self.w.kim.requests,'post',return_value=types.SimpleNamespace(
                status_code=200,text='invalid',json=lambda:{'ok':False})):
            self.assertFalse(self.w.manager.notifier.send('test','123'))

    def test_gemini_normalization_keeps_actual_original_command(self):
        raw='사용자의 원래 표현'
        with patch.object(self.w.manager,'_gemini_canonicalize_command',return_value='BTCUSD 1분 올존 알려줘'):
            link=self.register(raw,mid=501)
        self.assertEqual(link['original_command'],raw)
        self.assertEqual(link['command_message_id'],501)
        self.assertIsNone(getattr(self.w.manager._command_context,'current',None))

    def test_legacy_state_and_callers_keep_normal_delivery(self):
        self.w.manager._state_path.write_text('{"version":2,"watches":[]}',encoding='utf-8')
        self.w.restart_manager()
        self.w.manager.handle_command('BTCUSD 1분 매수올존 알려줘','123')
        self.w.commands()
        self.assertFalse(self.w.manager._watch_message_links)
        self.w.watch.try_fire('BTCUSD','1m','LONG','A',alert_identity='legacy')
        self.assertNotIn('reply_parameters',self.w.http.deliveries[-1])

    def test_explicit_reset_keeps_old_confirmations_from_affecting_new_watches(self):
        old=self.register()
        self.w.manager._reset_owner('123');self.w.commands()
        new=self.register()
        self.cancel(old)
        self.assertIn(new['watch_id'],self.w.watch._watches)

    def test_invalid_message_ids_never_guess_a_watch(self):
        link=self.register()
        for invalid in (None,0,-1,True,'1.2','x'):
            self.w.manager.handle_command('취소','123',reply_to_message_id=invalid)
            self.assertIn(link['watch_id'],self.w.watch._watches)

    def test_no_metadata_leaks_to_programmatic_registration(self):
        self.register()
        self.w.clock.advance(.01)
        self.w.manager.handle_command('BTCUSD 10분 올존 알려줘','456')
        self.w.commands()
        self.assertEqual(len(self.w.manager._watch_message_links),1)

    def add_chain(self, scheduled=False, mid=601):
        w=self.w
        chain=w.chain.TimedChainSpec('CHAIN:REPLY','123','BTCUSD',
                    (w.chain.ChainTriggerSpec('BAR_CLOSE','1m'),),
                    final_action='NOTIFY',created_at=w.clock.time(),
                    start_at=w.clock.time()+3600 if scheduled else None)
        with patch.object(w.manager,'_handle_command_text',side_effect=lambda *_:w.manager._add_timed_chain(chain)):
            w.manager.handle_command('원본 연쇄 WATCH 명령','123',message_id=mid)
        w.commands()
        return chain,w.manager._watch_message_links[chain.chain_id]

    def test_chain_registration_and_whole_chain_cancel(self):
        chain,link=self.add_chain()
        self.assertTrue(link['members'])
        children=set(link['members'])
        self.cancel(link)
        self.assertNotIn(chain.chain_id,self.w.manager.timed_chains)
        self.assertFalse(children.intersection(self.w.generic._watches))
        self.assertTrue(self.w.http.deliveries[-1]['text'].startswith('🗑'))

    def test_chain_completion_reply_survives_pending_notification_restart(self):
        chain,link=self.add_chain(mid=602)
        w=self.w
        w.http.status_code=500
        ev=dict(kind='GENERIC_TRIGGER',strategy='OZ',chain_id=chain.chain_id,
                chain_stage=0,watch_id=chain.current_watch_id,watch_type='BAR_CLOSE',
                event_time=w.clock.time(),message='chain original alert')
        w.manager._handle_event(ev)
        self.assertTrue(w.manager.watch_orchestrator.pending_notifications)
        w.restart_manager();w.http.status_code=200
        w.manager.watch_orchestrator.retry_notifications()
        self.assertEqual(self.reply_id(),602)
        self.assertEqual(w.http.deliveries[-1]['text'],'chain original alert')
        self.assertFalse(w.manager.watch_orchestrator.pending_notifications)

    def test_scheduled_chain_cancel_before_any_child_exists(self):
        chain,link=self.add_chain(scheduled=True)
        self.assertFalse(link['members'])
        self.cancel(link)
        self.w.clock.advance(7200)
        self.w.manager._maintain_timed_chains();self.w.commands()
        self.assertNotIn(chain.chain_id,self.w.manager.timed_chains)
        self.assertFalse(self.w.generic._watches)

    def test_fvg_registration_and_individual_cancellation(self):
        link=self.register('BTCUSD 1분 FVG 생성 알려줘')
        self.assertEqual(link['watch_type'],'FVG_NEW')
        self.assertIn(link['watch_id'],self.w.manager.fvg_created_watches)
        self.cancel(link)
        self.assertNotIn(link['watch_id'],self.w.manager.fvg_created_watches)

    def test_receiver_forwards_private_message_and_reply_ids(self):
        stop=threading.Event()
        received=[]
        commander=types.SimpleNamespace(token='OFFLINE',chat_id='123',config={},
                handle_command=lambda *args,**kwargs:(received.append((args,kwargs)),stop.set()))
        bot=self.w.kim.TelegramBot(commander,stop)
        update={'update_id':1,'message':{'chat':{'id':123,'type':'private'},'message_id':30,
                                       'text':'취소','reply_to_message':{'message_id':20}}}
        response=types.SimpleNamespace(status_code=200,json=lambda:{'ok':True,'result':[update]})
        with patch.object(bot,'_check_webhook'),patch.object(bot,'_prime_offset'),\
             patch.object(self.w.kim.requests,'get',return_value=response):
            bot.run()
        self.assertEqual(received,[(('취소',),{'incoming_chat_id':'123','message_id':30,'reply_to_message_id':20})])

    def test_receiver_forwards_official_channel_message_and_reply_ids(self):
        stop=threading.Event();received=[]
        commander=types.SimpleNamespace(token='OFFLINE',chat_id='-1001',config={},
                handle_command=lambda *args,**kwargs:(received.append((args,kwargs)),stop.set()))
        bot=self.w.kim.TelegramBot(commander,stop)
        update={'update_id':1,'channel_post':{'chat':{'id':-1001,'type':'channel'},'message_id':33,
                                            'text':'취소','reply_to_message':{'message_id':22}}}
        response=types.SimpleNamespace(status_code=200,json=lambda:{'ok':True,'result':[update]})
        with patch.object(bot,'_check_webhook'),patch.object(bot,'_prime_offset'),\
             patch.object(self.w.kim.requests,'get',return_value=response):
            bot.run()
        self.assertEqual(received[0][1],{'incoming_chat_id':'-1001','message_id':33,'reply_to_message_id':22})


    def test_registration_send_failure_retries_with_same_watch(self):
        self.w.http.status_code=500
        self.w.manager.handle_command('BTCUSD 1분 올존 알려줘','123',message_id=701)
        self.w.commands()
        link=next(iter(self.w.manager._watch_message_links.values()))
        self.assertIsNone(link['confirmation_message_id'])
        self.assertIn(link['watch_id'],self.w.watch._watches)
        self.w.http.status_code=200
        self.w.manager._retry_watch_confirmations()
        self.assertIsNotNone(link['confirmation_message_id'])
        self.assertEqual(len(self.w.manager._watch_message_links),1)

    def test_cancel_delivery_failure_recovers_after_restart(self):
        link=self.register()
        self.w.http.status_code=500
        self.cancel(link)
        self.assertNotIn(link['watch_id'],self.w.watch._watches)
        self.assertTrue(link['cancel_confirmation_pending'])
        self.w.restart_manager();self.w.http.status_code=200
        self.w.manager._retry_watch_confirmations()
        self.assertTrue(self.w.http.deliveries[-1]['text'].startswith('🗑'))
        self.assertFalse(self.w.manager._watch_message_links[link['watch_id']]['cancel_confirmation_pending'])

    def test_cancel_queue_failure_does_not_claim_success_or_lose_retry(self):
        link=self.register()
        with patch.object(self.w.manager.oz_queue,'push',side_effect=OSError('disk full')):
            self.w.manager.handle_command('취소','123',reply_to_message_id=link['confirmation_message_id'])
        self.assertEqual(link['status'],'active')
        self.assertIn(link['watch_id'],self.w.watch._watches)
        self.assertTrue(self.w.http.deliveries[-1]['text'].startswith('❌'))
        self.cancel(link)
        self.assertNotIn(link['watch_id'],self.w.watch._watches)

    def test_pending_cancel_resumes_after_restart_before_queue_append(self):
        link=self.register()
        link['status']='cancel_requested'
        self.w.manager._save_private_state_locked()
        self.w.restart_manager()
        self.w.manager._retry_watch_confirmations();self.w.commands()
        self.assertNotIn(link['watch_id'],self.w.watch._watches)
        self.assertTrue(self.w.http.deliveries[-1]['text'].startswith('🗑'))

    def test_replayed_engine_cancel_does_not_send_contradictory_ack(self):
        link=self.register()
        self.cancel(link)
        count=len(self.w.http.deliveries)
        self.w.manager._push({'action':'CANCEL_MANUAL','watch_id':link['watch_id'],
                             'request_chat_id':'123','reply_cancel':True})
        self.w.commands()
        self.assertEqual(len(self.w.http.deliveries),count)
        self.assertEqual(link['status'],'cancelled')

    def test_cancelled_parent_cannot_be_rearmed_from_stale_snapshot(self):
        chain,link=self.add_chain()
        payload=self.w.manager._chain_trigger_payloads_locked(chain)[0]
        self.cancel(link)
        self.w.manager._push(payload);self.w.commands()
        self.assertNotIn(payload['watch_id'],self.w.generic._watches)

    def test_private_condition_notification_replies_to_origin(self):
        w=self.w
        spec=w.kim.StrategySpec('PRIVATE:REPLY','조건알림','BTCUSD',
                 (w.kim.ConditionSpec('TREND','1m'),),('1m',),final_action='NOTIFY',
                 final_direction='LONG',destination='PRIVATE',owner_chat_id='123',
                 persistent=True,source='PRIVATE')
        with patch.object(w.manager,'_handle_command_text',side_effect=lambda *_:w.manager._add_private_spec(spec)):
            w.manager.handle_command('원본 조건 WATCH','123',message_id=801)
        w.manager._notify_spec_locked(spec,'LONG','test-signature')
        self.assertEqual(self.reply_id(),801)
        link=w.manager._watch_message_links[spec.spec_id]
        self.cancel(link)
        self.assertNotIn(spec.spec_id,w.manager.manual_specs)

    def test_fvg_created_alert_replies_to_origin(self):
        link=self.register('BTCUSD 1분 FVG 생성 알려줘',mid=802)
        self.w.manager._apply_fact_event({'kind':'FVG_CREATED','strategy':'FVG','symbol':'BTCUSD',
                'source_tf':'1m','direction':'LONG','fvg_side':'BULL','zone_id':'zone-reply',
                'event_time':self.w.clock.time(),'fvg_time':self.w.clock.time()})
        self.assertEqual(self.reply_id(),802)
        self.assertIn('FVG 생성',self.w.http.deliveries[-1]['text'])

    def test_fvg_to_oz_child_inherits_original_command(self):
        w=self.w
        payload=dict(watch_id='FVGCREATEDOZ:REPLY',symbol='BTCUSD',timeframes=['1m'],
                     direction='LONG',request_chat_id='123',final_action='OZ',oz_tfs=['1m'],
                     oz_direction='LONG',persistent=False)
        with patch.object(w.manager,'_handle_command_text',side_effect=lambda *_:w.manager._add_fvg_created_watch(payload)):
            w.manager.handle_command('FVG 생성 후 올존 원본 명령','123',message_id=803)
        w.manager._apply_fact_event({'kind':'FVG_CREATED','strategy':'FVG','symbol':'BTCUSD',
                'source_tf':'1m','direction':'LONG','fvg_side':'BULL','zone_id':'zone-child',
                'event_time':w.clock.time(),'fvg_time':w.clock.time()})
        w.commands()
        link=w.manager._watch_message_links[payload['watch_id']]
        self.assertTrue(link['members'])
        w.restart_manager();w.restart_oz()
        self.assertTrue(w.watch.try_fire('BTCUSD','1m','LONG','A',alert_identity='fvg-oz'))
        self.assertEqual(self.reply_id(),803)


if __name__=='__main__':
    unittest.main(verbosity=2)
