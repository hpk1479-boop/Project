"""김매니저: SIGNAL formatting, recipient selection, deduplication and delivery only.

No market input, strategy state, composition, threads or durable outbox. The host
owns the transport and may persist completed delivery receipts in event_state.
"""
import json
import logging
import math
import threading
import time
from event_engine.model import Kind
from telegram_routing import private_alert_chats


class SignalOutput:
    def __init__(self, config, *, transport, receipts=None, attempts=2, result_observer=None,
                 sleep=time.sleep, max_retry_wait=30):
        self.config = dict(config)
        self.transport = transport
        self.receipts = dict(receipts or {})
        self._receipt_lock = threading.RLock()
        self.attempts = max(1, int(attempts))
        self._sleep = sleep
        self.max_retry_wait = float(max_retry_wait)
        if not math.isfinite(self.max_retry_wait) or self.max_retry_wait < 0:
            raise ValueError('max_retry_wait must be finite and nonnegative')
        self.results = []
        self.result_observer = result_observer
        self.oz_policy = str(self.config.get('OZ_OUTPUT_POLICY', 'ALL_STRATEGIES')).strip().upper()
        if self.oz_policy not in {'ALL_STRATEGIES', 'MARKET_REPRESENTATIVE'}:
            raise ValueError('OZ_OUTPUT_POLICY: ALL_STRATEGIES / MARKET_REPRESENTATIVE')
        self._market_deliveries = {
            (r.get('symbol', ''), r['market_event_id'], r['recipient']): r
            for r in self.receipts.values() if r.get('market_event_id')
        }

    def _message_id(self, symbol, chat, token):
        if token is None: return None
        from event_commands import LOGICAL_MESSAGE_BASE
        if int(token) < LOGICAL_MESSAGE_BASE: return int(token)
        with self._receipt_lock:
            for record in self.receipts.values():
                if (record.get('symbol'), record.get('recipient'), record.get('message_token')) == (symbol, chat, token):
                    return record.get('message_id')
        raise ValueError('confirmation delivery unresolved; reply must not become an unrelated message')

    def logical_reply_id(self, chat, actual):
        with self._receipt_lock:
            for record in self.receipts.values():
                if record.get('recipient') == str(chat) and record.get('message_id') == actual:
                    return record.get('message_token') or actual
        return actual

    def reply_symbol(self, chat, actual):
        if actual is None:return None
        with self._receipt_lock:
            for record in self.receipts.values():
                if record.get('recipient')==str(chat) and record.get('message_id')==actual:
                    return record.get('symbol')
        return None

    def _record(self,event,chat,status):
        if self.result_observer is not None:
            try:self.result_observer(event,chat,status)
            except Exception:logging.exception('LIVE 알림 기록 오류 · 전송은 계속합니다',extra={'trace_module':'KIM'})

    def _recipients(self, event, content):
        """The signal's recipients, plus the 1:1 command chats when it goes to the alert room and they opted in.

        Without an alert room (TELEGRAM_CHAT_ID empty) a broadcast has no recipient of its own; the opted-in
        1:1 command chats are then its only recipients.
        """
        room = str(self.config.get('TELEGRAM_CHAT_ID', ''))
        recipients = [str(x) for x in (content.get('recipients') or [room])]
        if room in recipients:
            kind = 'economy' if event.payload.get('strategy') == 'ECONOMY_HOST' else 'live'
            recipients.extend(private_alert_chats(self.config, kind))
        return list(dict.fromkeys(recipients))

    def record_skipped(self,event,status):
        if self.result_observer is None or event is None or event.kind!=Kind.SIGNAL:return
        content=event.payload.get('content',{})
        if content.get('type')!='NOTIFICATION' and content.get('status')!='DEGRADED':return
        for chat in self._recipients(event,content):self._record(event,chat,status)

    def accept(self, event):
        if event.kind != Kind.SIGNAL: return ()
        content = event.payload['content']
        if content.get('type') != 'NOTIFICATION' and content.get('status') != 'DEGRADED': return ()
        signal_id = event.payload['signal_id']
        symbol = event.payload.get('symbol', '')
        text = str(content.get('message', ''))
        recipients = self._recipients(event, content)
        if logging.getLogger().isEnabledFor(logging.INFO):logging.info("김매니저 수신 · %s · %s · 수신자=%s",signal_id,symbol,recipients)
        delivered = []
        if not any(recipients):self._record(event,'','전송 실패')
        for chat in (x for x in recipients if x):
            key = json.dumps([signal_id, chat], ensure_ascii=False, separators=(',', ':'))
            if key in self.receipts:
                if logging.getLogger().isEnabledFor(logging.INFO):logging.info("김매니저 중복 차단 · %s · %s",signal_id,chat)
                delivered.append(self.receipts[key]);self._record(event,chat,'전송됨'); continue
            market = content.get('market_event_id')
            market_key = (symbol, market, chat)
            if market and self.oz_policy == 'MARKET_REPRESENTATIVE' and market_key in self._market_deliveries:
                representative = self._market_deliveries[market_key]
                self.results.append({'signal_id': signal_id, 'recipient': chat,
                                     'suppressed': 'market_representative',
                                     'representative_signal_id': representative['signal_id']})
                self._record(event,chat,'대표 정책으로 걸러짐')
                continue
            status='전송 실패'
            try:
                reply = self._message_id(symbol, chat, content.get('reply_to_message_id'))
                data = {'chat_id': chat, 'text': text}
                if reply is not None:
                    data['reply_parameters'] = json.dumps({'message_id': reply, 'allow_sending_without_reply': False})
                waited = 0.0
                for attempt in range(self.attempts):
                    # Unknown transport outcome is not retried: a sent message
                    # must not be duplicated after a lost HTTP response.
                    response = self.transport(data)
                    payload = response.json()
                    if response.status_code == 200 and payload.get('ok') is True:
                        record = {'signal_id': signal_id, 'symbol': symbol, 'recipient': chat,
                                  'strategy': event.payload.get('strategy'), 'market_event_id': market,
                                  'source_time': event.source_time, 'text': text,
                                  'message_id': payload['result']['message_id'],
                                  'message_token': content.get('message_token'), 'reply_to_message_id': reply}
                        with self._receipt_lock:
                            self.receipts[key] = record
                        self.results.append(record); delivered.append(record)
                        if market: self._market_deliveries.setdefault(market_key, record)
                        status='전송됨'
                        break
                    if response.status_code == 429 and attempt + 1 < self.attempts:
                        # Telegram explicitly rejected this attempt. Its delay
                        # is a minimum: never shorten it to fit our wait budget.
                        try:
                            delay = float(payload.get('parameters', {}).get('retry_after', 1))
                            if not math.isfinite(delay) or delay < 0:raise ValueError('retry_after')
                        except (TypeError, ValueError, AttributeError):
                            logging.error('김매니저 재시도 대기시간 오류 · %s', signal_id)
                            break
                        if delay > self.max_retry_wait - waited:
                            logging.error('김매니저 재시도 대기 한도 초과 · %s · %s초', signal_id, delay)
                            break
                        self._sleep(delay)
                        waited += delay
                    if response.status_code < 500 and response.status_code != 429:
                        logging.error("김매니저 전송 거부 · %s · HTTP %s",signal_id,response.status_code)
                        break
                else:
                    logging.error('SIGNAL delivery failed after retry: %s', signal_id)
            except Exception:
                logging.exception('SIGNAL delivery failed: %s', signal_id)
            self._record(event,chat,status)
        return tuple(delivered)
