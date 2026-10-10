"""Telegram chats by role, read from config only: who may send commands, who also gets alerts.

Commands come only from 1:1 command chats. Those chats may also receive the alert room's live and
economy alerts (off unless set to true); without an alert room they are the only place those go.
"""

# A 1:1 command chat may also receive what goes to the alert room. Off unless set to true.
PRIVATE_ALERT_KEYS = {'live': 'PRIVATE_LIVE_ALERTS_ENABLED', 'economy': 'PRIVATE_ECONOMY_ALERTS_ENABLED'}


def command_chat_ids(config):
    """The chats allowed to send commands: TELEGRAM_COMMAND_CHAT_IDS, or TELEGRAM_CHAT_ID when that key is absent."""
    raw = config.get('TELEGRAM_COMMAND_CHAT_IDS', config.get('TELEGRAM_CHAT_ID', ''))
    return tuple(dict.fromkeys(v.strip() for v in str(raw).split(',') if v.strip()))


def private_alert_chats(config, kind):
    """1:1 command chats that also get alerts of `kind` ('live' or 'economy'); Telegram 1:1 chat IDs are positive numbers."""
    if str(config.get(PRIVATE_ALERT_KEYS[kind], 'false')).strip().lower() != 'true':
        return ()
    return tuple(chat for chat in command_chat_ids(config) if chat.isdigit())
