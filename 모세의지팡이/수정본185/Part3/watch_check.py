"""WATCH sentence check in a separate process (Part1/Part2 are only imported, never modified).

usage: python watch_check.py <project_root> <sentence> [symbol]
Prints one JSON line: whether the real engine registered a watch for the sentence,
the registered commands, and the engine's reply messages. No network, no market data.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

sys.dont_write_bytecode=True


def check(root:Path,text:str,symbol:str='XAUUSD+')->dict:
    sys.path[:0]=[str(root/'Part1/program'),str(root/'Part2')]
    from event_backtest.system import deny_network
    deny_network()
    import logging;logging.disable(logging.CRITICAL)
    from event_application import create_event_engine
    from event_engine.model import Kind
    from event_engine.domain_support import plain
    config={'SYMBOLS':symbol,'TELEGRAM_TOKEN':'OFFLINE',
            'TELEGRAM_CHAT_ID':'BACKTEST','GEMINI_FALLBACK_ENABLED':'false'}
    engine=create_event_engine(config,symbols=(symbol,),selection=['WATCH'],backtest=True)
    engine.ingress.post(Kind.COMMAND,source='part3',source_seq=1,source_time=1_700_000_000_000,
                        payload={'symbol':symbol,'strategy':'WATCH','text':text,'chat_id':'BACKTEST'})
    engine.run()
    commands,replies,unresolved=[],[],False
    for signal in engine.signals:
        content=plain(signal.payload).get('content',{})
        kind=content.get('type')
        if kind=='WATCH_COMMAND':commands.append(content.get('command'))
        elif kind=='NOTIFICATION':replies.append(str(content.get('message','')))
        elif kind=='EXTERNAL_REQUEST':unresolved=True
    return {'text':text,'symbol':symbol,'recognized':bool(commands),'commands':commands,'replies':replies,
            'needs_external_interpretation':unresolved,'errors':[str(e) for e in engine.error_log]}


if __name__=='__main__':
    root,text=Path(sys.argv[1]),sys.argv[2]
    print(json.dumps(check(root,text,sys.argv[3] if len(sys.argv)>3 else 'XAUUSD+'),ensure_ascii=False,default=str))
