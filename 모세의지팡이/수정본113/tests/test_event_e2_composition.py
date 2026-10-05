"""Integration at the unchanged composition decision boundary, without sockets."""
import sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Part1/program'))
from event_application import create_event_engine
from event_engine import Kind
from test_event_e2_domains import snap


def feed(engine,i):
    tfs=('1m','2m','3m','5m','10m','15m','20m','30m','1h','2h','3h','4h','6h','8h','12h','1d')
    engine.ingress.post(Kind.MARKET_BUNDLE,source='staff',source_seq=i+1,
        source_time=1790380680000+i*60000,
        payload={'symbol':'XAUUSD+','feeds':{tf:snap(i,i+1) for tf in tfs}})
    engine.run()


def test_all_consumers_construct_and_checkpoint(monkeypatch):
    import socket
    import types
    def deny(*a,**k):raise AssertionError('network forbidden')
    for method in ('connect','connect_ex','sendto'):monkeypatch.setattr(socket.socket,method,deny)
    requests=types.ModuleType('requests');requests.Session=deny;requests.get=deny;requests.post=deny
    monkeypatch.setitem(sys.modules,'requests',requests)
    config={'STAFF_ALLOWED_SYMBOLS':'XAUUSD+,NAS100,BTCUSD','TARGET_SYMBOLS':'XAUUSD+,NAS100,BTCUSD',
            'WONBI_SIGMA':'3','TELEGRAM_TOKEN':'OFFLINE','TELEGRAM_CHAT_ID':'test-recipient'}
    engine=create_event_engine(config,symbols=('XAUUSD+',))
    feed(engine,0)
    assert not engine.error_log,engine.error_log
    checkpoint=engine.checkpoint()
    resumed=create_event_engine(config,symbols=('XAUUSD+',))
    resumed.restore(checkpoint)
    for i in (1,2):feed(engine,i);feed(resumed,i)
    assert not engine.error_log,engine.error_log
    assert not resumed.error_log,resumed.error_log
    actual=lambda e:[(s.source_time,s.payload['signal_id'],s.payload['content']) for s in e.signals
                     if s.source_time>1790380680000]
    assert actual(engine)==actual(resumed)


def test_domain_dispatch_has_no_filesystem_or_wall_clock(monkeypatch):
    import types,time
    def deny(*a,**k):raise AssertionError('domain used host IO or clock')
    requests=types.ModuleType('requests');requests.Session=deny;requests.get=deny;requests.post=deny
    monkeypatch.setitem(sys.modules,'requests',requests)
    engine=create_event_engine({'WONBI_SIGMA':'3','STAFF_ALLOWED_SYMBOLS':'XAUUSD+',
                                'TELEGRAM_TOKEN':'OFFLINE','TELEGRAM_CHAT_ID':'offline'},symbols=('XAUUSD+',))
    # Dependency import and immutable startup config belong to the host. The
    # first domain dispatch (including lazy strategy construction) does not.
    import logging
    old=logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with monkeypatch.context() as guard:
            for name in ('open','resolve','mkdir','is_file','exists'):guard.setattr(Path,name,deny)
            guard.setattr(time,'time',deny)
            guard.setattr(time,'monotonic',deny)
            feed(engine,0)
    finally:logging.disable(old)
    assert not engine.error_log,engine.error_log


def test_aliases_and_special_trigger_inputs_are_preserved_and_isolated(monkeypatch):
    import types,oz_profiles
    requests=types.ModuleType('requests')
    monkeypatch.setitem(sys.modules,'requests',requests)
    original=oz_profiles.special_trigger_overrides()
    engine=create_event_engine({'WONBI_SIGMA':'3','STAFF_ALLOWED_SYMBOLS':'XAUUSD+',
                                'TELEGRAM_TOKEN':'OFFLINE','TELEGRAM_CHAT_ID':'offline'},symbols=('XAUUSD+',),
                               trigger_overrides={'SPECIAL7':'무지성 올존'})
    engine.ingress.post(Kind.COMMAND,source='command',source_seq=1,source_time=1790380680000,
        payload={'symbol':'XAUUSD+','chat_id':'offline','text':'골드 1분 기울기(SMA17) > 0 알려줘'})
    engine.run()
    assert not engine.error_log,engine.error_log
    notices=[s.payload['content'] for s in engine.signals if s.payload['content'].get('type')=='NOTIFICATION']
    assert notices and not any('오류' in n['message'] for n in notices)
    kernel=engine.strategy_state['COMPOSER']['kernels']['XAUUSD+']
    spec=kernel.manager.official_specs['PIPELINE_7']
    assert (spec.validation_mode,spec.trigger_mode)==('BLIND','OZ')
    assert oz_profiles.special_trigger_overrides()==original


def test_same_millisecond_same_owner_keeps_both_watches_and_replays(monkeypatch):
    import types
    monkeypatch.setitem(sys.modules,'requests',types.ModuleType('requests'))
    config={'WONBI_SIGMA':'3','STAFF_ALLOWED_SYMBOLS':'XAUUSD+',
            'TELEGRAM_TOKEN':'OFFLINE','TELEGRAM_CHAT_ID':'offline'}
    def make():return create_event_engine(config,symbols=('XAUUSD+',),enabled_specials=())
    first,second=make(),make()
    commands=('골드 1분 기울기(SMA17) > 0 알려줘','골드 1분 상단 원비 터치 알려줘')
    for engine in (first,second):
        for index,text in enumerate(commands):
            engine.ingress.post(Kind.COMMAND,source='commands',source_seq=index,source_time=1790380680000,
                payload={'symbol':'XAUUSD+','chat_id':'same-owner','text':text})
            engine.run()
        assert not engine.error_log,engine.error_log
        # Storage moved to a resident registry. Assert the same two public
        # Watch identities through explicit host export, not a private dict path.
        from event_startup import export_engine_state
        import json
        watches={w['watch_id']:w for w in json.loads(export_engine_state(engine)['oz_generic_watch_state.json'])['watches']}
        assert len(watches)==2
        assert len(set(watches))==2
    signature=lambda e:[(s.source_time,s.payload['signal_id'],s.payload['content']) for s in e.signals]
    assert signature(first)==signature(second)
    resumed=make();resumed.restore(first.checkpoint())
    old_count=len(first.signals)
    for engine in (first,resumed):
        engine.ingress.post(Kind.COMMAND,source='commands',source_seq=3,source_time=1790380680000,
            payload={'symbol':'XAUUSD+','chat_id':'same-owner','text':'골드 1분 하단 원비 터치 알려줘'})
        engine.run()
        assert len(json.loads(export_engine_state(engine)['oz_generic_watch_state.json'])['watches'])==3
    assert signature(first)[old_count:]==signature(resumed)
