"""김매니저: SIGNAL formatting, recipient selection, deduplication and delivery only.

No market input, strategy state, composition, threads or durable outbox. The host
owns the transport and may persist completed delivery receipts in event_state.
"""
import json
import logging
from event_engine.model import Kind


class SignalOutput:
    def __init__(self, config, *, transport, receipts=None, attempts=2):
        self.config = dict(config)
        self.transport = transport
        self.receipts = dict(receipts or {})
        self.attempts = max(1, int(attempts))
        self.results = []

    def _message_id(self, symbol, chat, token):
        if token is None: return None
        from event_commands import LOGICAL_MESSAGE_BASE
        if int(token) < LOGICAL_MESSAGE_BASE: return int(token)
        for record in self.receipts.values():
            if (record.get('symbol'), record.get('recipient'), record.get('message_token')) == (symbol, chat, token):
                return record.get('message_id')
        raise ValueError('confirmation delivery unresolved; reply must not become an unrelated message')

    def logical_reply_id(self, chat, actual):
        for record in self.receipts.values():
            if record.get('recipient') == str(chat) and record.get('message_id') == actual:
                return record.get('message_token') or actual
        return actual

    def reply_symbol(self, chat, actual):
        if actual is None:return None
        for record in self.receipts.values():
            if record.get('recipient')==str(chat) and record.get('message_id')==actual:
                return record.get('symbol')
        return None

    def accept(self, event):
        if event.kind != Kind.SIGNAL: return ()
        content = event.payload['content']
        if content.get('type') != 'NOTIFICATION' and content.get('status') != 'DEGRADED': return ()
        signal_id = event.payload['signal_id']
        symbol = event.payload.get('symbol', '')
        text = str(content.get('message', ''))
        recipients = content.get('recipients') or [self.config.get('TELEGRAM_CHAT_ID', '')]
        delivered = []
        for chat in dict.fromkeys(str(x) for x in recipients if str(x)):
            key = json.dumps([signal_id, chat], ensure_ascii=False, separators=(',', ':'))
            if key in self.receipts:
                delivered.append(self.receipts[key]); continue
            try:
                reply = self._message_id(symbol, chat, content.get('reply_to_message_id'))
                data = {'chat_id': chat, 'text': text}
                if reply is not None:
                    data['reply_parameters'] = json.dumps({'message_id': reply, 'allow_sending_without_reply': False})
                for attempt in range(self.attempts):
                    # Unknown transport outcome is not retried: a sent message
                    # must not be duplicated after a lost HTTP response.
                    response = self.transport(data)
                    payload = response.json()
                    if response.status_code == 200 and payload.get('ok') is True:
                        record = {'signal_id': signal_id, 'symbol': symbol, 'recipient': chat,
                                  'source_time': event.source_time, 'text': text,
                                  'message_id': payload['result']['message_id'],
                                  'message_token': content.get('message_token'), 'reply_to_message_id': reply}
                        self.receipts[key] = record; self.results.append(record); delivered.append(record)
                        break
                    if response.status_code < 500 and response.status_code != 429: break
                else:
                    logging.error('SIGNAL delivery failed after retry: %s', signal_id)
            except Exception:
                logging.exception('SIGNAL delivery failed: %s', signal_id)
        return tuple(delivered)
